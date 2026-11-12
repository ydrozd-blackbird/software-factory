#!/usr/bin/env python3
"""Run one read-only Codex review for a software factory run, and log it.

    python3 codex_task.py --cd DIR --prompt-file F --out F --factory REPO/.factory --label TEXT --schema F \
        --subject spec|final --round initial|followup \
        [--spec-file F | --expect-head SHA --base-ref REF [--expect-base SHA]] [--open-ids F1,F3] \
        [--phase TEXT] [--model gpt-6.1-sol] [--effort high] [--timeout 540]
    python3 codex_task.py --record-fallback --factory REPO/.factory --subject spec|final --head SHA_OR_HASH [--base SHA]

Spec gate: --spec-file is a bundle inside <factory>/tmp/. The helper copies it to <factory>/tmp/spec-<sha256>.md and
appends the exact bundle bytes to the prompt, so the reviewed input is the hashed input. The sha256 is the target.
The report must have base null and reviewed_head equal to that sha256.
Final gate: --expect-head is the full HEAD of --cd. An initial round needs --base-ref (for example origin/main); the
helper takes `git merge-base REF HEAD` as the base, and --expect-base, if given, must equal it (else exit 2 wrong-base).
A follow-up uses the head of the initial `ok` row or `fallback` row as the base; any other --expect-base is exit 2
wrong-base. A failed git call is exit 2, never a pass.
--open-ids lists the open finding ids. A follow-up needs it (an empty value means none); an initial round refuses it.
--effort is low, medium, high, xhigh or max. The helper prints no prompt, output, event text or env value.
Stderr has one fixed line per status, such as `codex_task: usage-limit`.

--record-fallback launches nothing. The lead calls it after an Opus review replaced a failed initial round. It adds
one `fallback` row, and that row lets the follow-up of the subject start.
It takes the subject lock like a review round: a held lock is exit 8 `busy`. Under the lock it reads the log again.
An `ok` initial row is exit 2 `initial-already-ok`. The same fallback again is exit 0 and adds no row.
A fallback with another head or base is exit 2 `conflicting-fallback`.
After that row a Codex `--round initial` for the subject is exit 2 `initial-after-fallback`. A round reads the log
again under the lock, so a fallback that appears just before the lock is caught too.

Exit codes: 0 ok, 1 crash or stopped, 2 usage, 3 missing, 4 usage-limit, 5 timeout, 6 error,
7 bad-output, 8 budget-spent, attempts-spent or busy, 9 wrong-target.
One lock guards each subject (not each round). A follow-up starts only after the initial round has finished: an `ok`
initial row or a `fallback` row. An `attempt` row alone is not enough.
The review budget counts log rows that started at or after `started` in <factory>/run.json (all rows if there is none).
A follow-up report has `answers`: exactly one {id, status, evidence} per id in --open-ids. Every finding has an `id`.
An id that is both in `bugs` and answered `fixed` or `rejected` is contradictory: exit 7, and the round is not spent.
Any `error` or `turn.failed` event is a failure, even when Codex exits 0.
"""
import argparse, datetime as dt, hashlib, json, os, re, shutil, signal, socket, subprocess, sys, tempfile, time

ENV_KEEP = ("PATH HOME USER LOGNAME SHELL LANG LC_ALL LC_CTYPE TERM TMPDIR CODEX_HOME OPENAI_API_KEY OPENAI_BASE_URL "
            "CODEX_API_KEY HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy "
            "SSL_CERT_FILE SSL_CERT_DIR NODE_EXTRA_CA_CERTS").split()
LIMIT_RE = re.compile(r"usage limit|rate limit|insufficient_quota|exceeded your current quota|too many requests", re.I)
HINTS = (("auth", re.compile(r"401|unauthori[sz]ed|not logged in|log ?in|api key", re.I)),
         ("network", re.compile(r"ENOTFOUND|ECONNREFUSED|ECONNRESET|proxy|certificate|network|dns", re.I)),
         ("model", re.compile(r"model.{0,40}(not found|not supported|does not exist)|unknown model", re.I)))
DEFAULT_TIMEOUT, LOCK_GRACE, MAX_ATTEMPTS, FALLBACK_LOCK_TIMEOUT = 540, 120, 2, 30
SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
STATUS = {0: "ok", 3: "missing", 4: "usage-limit", 5: "timeout", 6: "error", 7: "bad-output", 9: "wrong-target"}
STATE = {"lock": None, "proc": None, "ctx": None}

class Usage(Exception): pass

class Parser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write("codex_task: usage\n")
        sys.exit(2)

def utc_now(): return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def git(cd, *args):
    try:
        r = subprocess.run(["git", "-C", cd, *args], capture_output=True, text=True, errors="replace")
    except OSError:
        return 127, ""
    return r.returncode, r.stdout

real = os.path.realpath

def inside(child, parent):
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)

def read_log(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows

def log_row(log_path, row):
    with open(log_path, "a", encoding="utf-8") as f:  # one short line per write, appended
        f.write(json.dumps(row) + "\n")

def child_env():
    return {k: os.environ[k] for k in ENV_KEEP if k in os.environ}

def parse_events(text):
    info = {"thread": None, "failed": False, "messages": [],
            "tokens": dict.fromkeys(("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
                                     "cache_write_input_tokens"), 0)}
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get("type") if isinstance(ev, dict) else None
        if t == "thread.started":
            info["thread"] = ev.get("thread_id")
        elif t == "turn.completed":
            usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
            for k in info["tokens"]:
                info["tokens"][k] += usage[k] if isinstance(usage.get(k), int) else 0
        elif t in ("error", "turn.failed"):
            info["failed"] = True  # an `error` event counts as much as `turn.failed`, even when Codex exits 0
            err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            msg = ev.get("message") if isinstance(ev.get("message"), str) else err.get("message")
            if isinstance(msg, str):
                info["messages"].append(msg)
    return info

def resolve_schema(schema):
    """Codex rejects a root that is only a `$ref`. Then the `#/$defs/<Name>` target becomes the root;
    `$defs` stays if the target refers to other definitions. Any other schema is returned as it is."""
    ref = schema.get("$ref")
    if "properties" in schema or not (isinstance(ref, str) and ref.startswith("#/$defs/")):
        return schema
    node = schema
    for part in ref[2:].split("/"):
        node = node[part]
    resolved = dict(node)
    defs = schema.get("$defs")
    if isinstance(defs, dict) and "#/$defs/" in json.dumps(node):
        resolved["$defs"] = defs
    return resolved

def parse_time(text):
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None

def run_started(factory):
    """The `started` time of this run from <factory>/run.json, or None (then every row counts)."""
    try:
        with open(os.path.join(factory, "run.json"), encoding="utf-8") as f:
            return parse_time(json.load(f).get("started"))
    except (OSError, ValueError, AttributeError):
        return None

def in_run(row, cutoff):
    """A row counts when it started at or after the run started. A row with no readable start counts only without a cutoff."""
    if cutoff is None:
        return True
    when = parse_time(row.get("start"))
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=dt.timezone.utc)
    return when >= cutoff

def sha_file(path):
    try:
        with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()
    except OSError: return None

def rm(path):
    try: os.unlink(path)
    except OSError: pass

def kill_group(p):
    try: os.killpg(p.pid, signal.SIGKILL)
    except OSError: pass

def wrong_target(args, data, head, base):
    if data.get("target_ok") is False:
        return True
    if args.subject == "spec":
        return data.get("base") is not None or data.get("reviewed_head") not in (head, head[len("sha256:"):])
    return data.get("reviewed_head") != head or data.get("base") != base

ANSWER_STATUS = ("fixed", "open", "rejected")
JSON_TYPES = {"string": lambda v: isinstance(v, str), "null": lambda v: v is None, "boolean": lambda v: isinstance(v, bool),
              "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict),
              "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
              "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)}

def valid(value, schema, root):
    """Check a value against a JSON schema: type, enum, required, properties, additionalProperties false, items,
    minItems and local `#/` refs, recursively. No third-party library."""
    if not isinstance(schema, dict):
        return True
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node = root
        try:
            for part in ref[2:].split("/"):
                node = node[part]
        except (KeyError, TypeError):
            return False
        return valid(value, node, root)
    kind = schema.get("type")
    if kind is not None and not any(JSON_TYPES.get(k, lambda v: False)(value) for k in (kind if isinstance(kind, list) else [kind])):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, dict):
        if any(k not in value for k in schema.get("required", [])):
            return False
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for k, v in value.items():
            if k in props:
                if not valid(v, props[k], root):
                    return False
            elif schema.get("additionalProperties") is False:
                return False
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            return False
        if isinstance(schema.get("items"), dict) and not all(valid(i, schema["items"], root) for i in value):
            return False
    return True

def ids_ok(data, args):
    """Every finding has a unique string id. A follow-up carries `answers`: exactly one per open id, no extra, no repeat.
    Any other round may carry an empty list."""
    seen = set()
    for bug in data["bugs"]:
        bid = bug.get("id") if isinstance(bug, dict) else None
        if not isinstance(bid, str) or not bid or bid in seen:
            return False
        seen.add(bid)
    answers = data.get("answers")
    followup = args.round == "followup"
    if answers is None:
        return not followup
    if not isinstance(answers, list):
        return False
    if not all(isinstance(a, dict) and isinstance(a.get("id"), str) and a.get("status") in ANSWER_STATUS
               and isinstance(a.get("evidence"), str) for a in answers):
        return False
    if followup:
        got = [a["id"] for a in answers]
        if len(got) != len(set(got)) or set(got) != set(args.open_list):
            return False
        # a finding that the same report lists as a bug and answers as fixed or rejected is a contradiction
        return not any(a["status"] != "open" and a["id"] in seen for a in answers)
    return True

def check_output(args, head, base, resolved):
    """Return the exit code for a run where codex exited 0 with no failure event.
    `resolved` is the schema that was passed to Codex."""
    try:
        with open(args.out, encoding="utf-8") as f: data = json.loads(f.read())
    except (ValueError, OSError):
        return 7
    if not isinstance(data, dict) or not valid(data, resolved, resolved) or not isinstance(data.get("bugs"), list):
        return 7
    checked = (data.get("scope") or {}).get("checked") if isinstance(data.get("scope"), dict) else None
    if not checked or not isinstance(checked, list) or not isinstance(data.get("target_ok"), bool):
        return 7
    if not ids_ok(data, args):
        return 7
    return 9 if wrong_target(args, data, head, base) else 0

def alive(kill, n):
    try: kill(n, 0)
    except ProcessLookupError: return False
    except OSError: pass  # alive, but owned by someone else
    return True

def lock_is_stale(path, now):
    """Stale: helper and Codex group both gone on this host, or older than its timeout + 120 s."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        pid, start, timeout, host = int(data["pid"]), float(data["start"]), int(data["timeout"]), data["host"]
    except (OSError, ValueError, KeyError, TypeError):
        try:  # no readable owner: judge by file age
            return now - os.path.getmtime(path) > DEFAULT_TIMEOUT + LOCK_GRACE
        except OSError:
            return False
    if host == socket.gethostname() and not alive(os.kill, pid):
        pgid = data.get("child_pgid")
        if not isinstance(pgid, int) or pgid <= 1 or not alive(os.killpg, pgid):
            return True
    return now - start > timeout + LOCK_GRACE

def lock_file(dirname, data):
    fd, tmp = tempfile.mkstemp(prefix=".lock-", dir=dirname)
    with os.fdopen(fd, "w", encoding="utf-8") as f: f.write(json.dumps(data))
    return tmp

def record_child(path, pid):
    """Add the Codex child's pid and process-group id (the same) to the lock, in one atomic replace."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        os.replace(lock_file(os.path.dirname(path), dict(data, child_pid=pid, child_pgid=pid)), path)
    except (OSError, ValueError, TypeError):
        pass

def write_lock(path, timeout):
    tmp = lock_file(os.path.dirname(path), {"pid": os.getpid(), "host": socket.gethostname(), "start": time.time(),
                                            "timeout": timeout})
    try: os.link(tmp, path)  # fails with FileExistsError if the lock exists
    finally: rm(tmp)

def release(path):
    """Remove a lock, but only if this process owns it."""
    try:
        with open(path, encoding="utf-8") as f:
            if json.load(f).get("pid") == os.getpid(): rm(path)
    except (OSError, ValueError, AttributeError): pass

def reserve(tmp_real, subject, timeout):
    """Return (lock path, stale lock name or None). The path is None if a round of the subject is running.
    One lock guards the subject, not the round: budget/<sha1(subject)>.lock."""
    d = os.path.join(tmp_real, "budget")
    os.makedirs(d, exist_ok=True)
    name = "%s.lock" % hashlib.sha1(subject.encode()).hexdigest()
    path = os.path.join(d, name)
    try:
        write_lock(path, timeout)
        return path, None
    except FileExistsError:
        pass
    if not lock_is_stale(path, time.time()):
        return None, None
    guard = path + ".reclaim"  # two helpers must not both reclaim a stale lock
    try: os.close(os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
    except FileExistsError:
        if time.time() - os.path.getmtime(guard) > LOCK_GRACE: rm(guard)  # an orphan guard
        return None, None
    try:
        if not lock_is_stale(path, time.time()):
            return None, None
        os.unlink(path)
        write_lock(path, timeout)
        return path, name
    except OSError:
        return None, None
    finally:
        rm(guard)

def make_row(ctx, code, status, info, hint=None):
    a = ctx["args"]
    return {"out_sha256": ctx.get("out_sha256"), "start": ctx["start"], "end": utc_now(), "provider": "codex", "model": a.model, "effort": a.effort,
            "label": a.label, "phase": a.phase or "Review", "subject": a.subject, "round": a.round, "head": ctx["head"],
            "base": ctx["base"], "out": a.out, "cd": real(a.cd), "exit": code, "status": status, "hint": hint,
            "task_id": info["thread"], **info["tokens"]}

def on_signal(signum, frame):
    """Kill the Codex group, reap it, log `cancelled`, free the own lock."""
    p, ctx = STATE["proc"], STATE["ctx"]
    try:
        if p is not None:
            kill_group(p)
            p.wait(timeout=5)
        if ctx:
            log_row(ctx["log"], make_row(ctx, 1, "cancelled", parse_events("")))
    except Exception:
        pass
    if STATE["lock"]:
        release(STATE["lock"])
    print("codex_task: stopped", file=sys.stderr, flush=True)
    os._exit(1)

SHA_RE = re.compile(r"[0-9a-f]{40}")

def parse_open_ids(args):
    """The open finding ids. A follow-up needs --open-ids (empty means none). An initial round refuses it."""
    if args.round == "initial":
        if args.open_ids is not None:
            raise Usage("open-ids-on-initial")
        return []
    if args.open_ids is None:
        raise Usage("open-ids-missing")
    if not args.open_ids.strip():
        return []
    ids = [x.strip() for x in args.open_ids.split(",")]
    if not all(re.fullmatch(r"[A-Za-z0-9_.:-]{1,40}", x) for x in ids) or len(set(ids)) != len(ids):
        raise Usage("bad-open-ids")
    return ids

def diff_must_exist(cd, base, head):
    if base == head:
        raise Usage("empty-diff")
    rc = git(cd, "diff", "--quiet", base, head)[0]
    if rc == 0:
        raise Usage("empty-diff")
    if rc != 1:
        raise Usage("diff-failed")

def check_args(args, tmp_real):
    """Return (head, base) of the target, or raise Usage. A final follow-up has base None until the log is read."""
    out_real = real(args.out)
    if not inside(out_real, tmp_real) or out_real == tmp_real or not inside(real(os.path.dirname(args.out)), tmp_real):
        raise Usage("out-outside-tmp")
    if os.path.lexists(args.out):
        raise Usage("out-exists")
    if args.effort not in ("low", "medium", "high", "xhigh", "max"):
        raise Usage("bad-effort")
    if not args.schema:
        raise Usage("schema-missing")
    if not (os.path.isdir(args.cd) and os.path.isfile(args.prompt_file) and os.path.isfile(args.schema)):
        raise Usage("bad-path")
    args.open_list = parse_open_ids(args)
    if args.subject == "spec":
        if args.expect_head is not None or args.expect_base is not None or args.base_ref is not None:
            raise Usage("spec-takes-no-sha")
        if args.spec_file is None:
            raise Usage("spec-file-missing")
        if os.path.islink(args.spec_file) or not os.path.isfile(args.spec_file) or not inside(real(args.spec_file), tmp_real):
            raise Usage("bad-path")
        with open(args.spec_file, "rb") as f:
            data = f.read()
        digest = hashlib.sha256(data).hexdigest()
        args.spec_copy = os.path.join(tmp_real, "spec-%s.md" % digest)  # Codex reads this copy, not the lead's file
        args.spec_bytes = data  # and gets the same bytes in the prompt
        with open(args.spec_copy, "wb") as f:
            f.write(data)
        return "sha256:" + digest, None
    if args.spec_file is not None:
        raise Usage("final-takes-no-spec-file")
    if args.expect_head is None:
        raise Usage("expect-head-missing")
    for name, value in (("head", args.expect_head), ("base", args.expect_base)):
        if value is not None and not SHA_RE.fullmatch(value):
            raise Usage("bad-expect-" + name)
    rc, out = git(args.cd, "rev-parse", "HEAD")
    if rc != 0:
        raise Usage("rev-parse-failed")
    if out.strip() != args.expect_head:
        raise Usage("head-mismatch")
    if args.round == "followup":
        return args.expect_head, None
    if not args.base_ref:
        raise Usage("base-ref-missing")
    if args.base_ref.startswith("-"):
        raise Usage("bad-base-ref")
    rc, out = git(args.cd, "merge-base", args.base_ref, args.expect_head)
    base = out.strip()
    if rc != 0 or not SHA_RE.fullmatch(base):
        raise Usage("merge-base-failed")
    if args.expect_base is not None and args.expect_base != base:
        raise Usage("wrong-base")
    diff_must_exist(args.cd, base, args.expect_head)
    return args.expect_head, base

def prior_round(rows, subject):
    """The latest finished initial round of the subject: an `ok` row or a `fallback` row. None if there is none."""
    done = [r for r in rows if r.get("subject") == subject and r.get("round") == "initial"
            and r.get("status") in ("ok", "fallback")]
    return done[-1] if done else None

def check_followup(args, rows, head):
    """Run under the subject lock. The initial round must be finished. Return the base of a final follow-up."""
    prior = prior_round(rows, args.subject)
    if prior is None:
        raise Usage("followup-without-initial")  # at most an attempt row: the initial round never finished
    if args.subject == "spec":
        if prior.get("head") == head:
            raise Usage("empty-diff")
        return None
    base = prior.get("head")
    if not isinstance(base, str) or not SHA_RE.fullmatch(base):
        raise Usage("followup-without-initial")
    if args.expect_base is not None and args.expect_base != base:
        raise Usage("wrong-base")
    if base != head:
        rc = git(args.cd, "merge-base", "--is-ancestor", base, head)[0]
        if rc == 1:
            raise Usage("base-not-ancestor")
        if rc != 0:
            raise Usage("merge-base-failed")
    diff_must_exist(args.cd, base, head)
    return base

def record_fallback(args, factory):
    """Add one `fallback` row: the lead reviewed the subject with Opus after Codex failed. Nothing is launched.
    The subject lock guards the check and the write, so a round cannot slip in between. Busy is exit 8."""
    if not os.path.isdir(factory):
        raise Usage("bad-path")
    if args.subject == "final":
        if not SHA_RE.fullmatch(args.head) or (args.base is not None and not SHA_RE.fullmatch(args.base)):
            raise Usage("bad-sha")
        head, base = args.head, args.base
    else:
        m = re.fullmatch(r"(?:sha256:)?([0-9a-f]{64})", args.head)
        if not m or args.base is not None:
            raise Usage("bad-hash")
        head, base = "sha256:" + m.group(1), None
    tmp_root = os.path.join(factory, "tmp")
    if os.path.islink(tmp_root):
        raise Usage("tmp-symlink")
    lock, _ = reserve(real(tmp_root), args.subject, FALLBACK_LOCK_TIMEOUT)
    if lock is None:
        print(json.dumps({"exit": 8, "status": "busy", "subject": args.subject}))
        sys.stderr.write("codex_task: busy\n")
        return 8
    STATE["lock"] = lock
    try:
        log_path = os.path.join(factory, "codex-runs.jsonl")
        cutoff = run_started(factory)
        rows = [r for r in read_log(log_path) if in_run(r, cutoff) and r.get("subject") == args.subject]  # read under the lock
        if any(r.get("round") == "initial" and r.get("status") == "ok" for r in rows):
            raise Usage("initial-already-ok")
        fallbacks = [r for r in rows if r.get("round") == "initial" and r.get("status") == "fallback"]
        if fallbacks and not any(r.get("head") == head and r.get("base") == base for r in fallbacks):
            raise Usage("conflicting-fallback")
        if not fallbacks:
            now = utc_now()
            log_row(log_path, {"start": now, "end": now, "provider": "fallback", "subject": args.subject, "round": "initial",
                               "head": head, "base": base, "status": "fallback", "exit": None})
        print(json.dumps({"exit": 0, "status": "fallback", "subject": args.subject, "head": head, "base": base}))
        return 0
    finally:
        release(lock)

def run(args):
    factory = real(args.factory)
    if args.record_fallback:
        return record_fallback(args, factory)
    tmp_root = os.path.join(factory, "tmp")
    if os.path.islink(tmp_root):
        raise Usage("tmp-symlink")
    tmp_real = real(tmp_root)
    head, base = check_args(args, tmp_real)
    log_path = os.path.join(factory, "codex-runs.jsonl")
    cutoff = run_started(factory)

    def now_rows():
        return [r for r in read_log(log_path) if in_run(r, cutoff)]

    def of(rows, rnd, status):
        return [r for r in rows if r.get("subject") == args.subject and r.get("round") == rnd and r.get("status") == status]

    def finished(rows):  # an ok follow-up ends the subject: no third round
        return of(rows, args.round, "ok") or of(rows, "followup", "ok")

    rows = now_rows()
    earlier = finished(rows)
    if args.round == "initial" and not earlier and of(rows, "initial", "fallback"):
        raise Usage("initial-after-fallback")  # the next round is the follow-up: a gate never gets three rounds
    lock = stale = None
    if not earlier:
        lock, stale = reserve(tmp_real, args.subject, args.timeout)
        if lock is not None:
            rows = now_rows()  # read the log again: another helper may have finished the round
            earlier = finished(rows)
            stale = None if earlier else stale
    STATE["lock"] = lock
    try:
        if lock is not None and not earlier and args.round == "initial" and of(rows, "initial", "fallback"):
            raise Usage("initial-after-fallback")  # a fallback row appeared after the first read, before the lock
        if lock is not None and not earlier and args.round == "followup":
            base = check_followup(args, rows, head)
        elif base is None and args.subject == "final" and args.round == "followup":
            prior = prior_round(rows, "final")
            base = prior.get("head") if prior else None
        ctx = {"args": args, "head": head, "base": base, "start": utc_now(), "log": log_path}
        if stale:
            log_row(log_path, make_row(ctx, None, "stale-lock", parse_events("")))
            sys.stderr.write("codex_task: stale-lock\n")
        capped = lock is not None and not earlier and len(of(rows, args.round, "attempt")) >= MAX_ATTEMPTS
        if earlier or capped or lock is None:
            last = earlier[-1] if earlier else {}
            status = "budget-spent" if earlier else "attempts-spent" if capped else "busy"
            print(json.dumps({"exit": 8, "status": status, "out": args.out, "prior_out": last.get("out"),
                              "prior_head": last.get("head"), "prior_base": last.get("base"),
                              "reusable": bool(last) and last.get("head") == head and last.get("base") == base
                              and last.get("out_sha256") is not None and sha_file(last.get("out")) == last["out_sha256"]}))
            sys.stderr.write("codex_task: %s\n" % status)
            return 8
        log_row(log_path, {"start": ctx["start"], "subject": args.subject, "round": args.round, "status": "attempt"})
        STATE["ctx"] = ctx
        return execute(args, ctx, tmp_root, lock)
    finally:
        if lock is not None:
            release(lock)  # the log, not the lock, says what is spent; the lock only guards a running round

def execute(args, ctx, tmp_root, lock):
    binary = os.environ.get("CODEX_BIN") or shutil.which("codex")
    info, hint, tmp_schema = parse_events(""), None, None
    if not binary or not os.path.exists(binary):
        code = 3
    else:
        cmd = [binary, "exec", "-s", "read-only", "-C", args.cd, "-m", args.model,
               "-c", 'model_reasoning_effort="%s"' % args.effort, "--json", "-o", args.out, "--ephemeral"]
        with open(args.schema, encoding="utf-8") as f:
            resolved = resolve_schema(json.load(f))
        fd, tmp_schema = tempfile.mkstemp(prefix="codex-schema-", suffix=".json", dir=tmp_root)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(resolved, f)
        cmd += ["--output-schema", tmp_schema, "-"]
        with open(args.prompt_file, "rb") as pf:
            prompt = pf.read()
        if args.spec_copy:  # the exact bundle bytes, so the reviewed input is the hashed input
            prompt += ("\n\n## Spec bundle under review (sha256 %s, copy at %s)\n\n"
                       % (ctx["head"][len("sha256:"):], args.spec_copy)).encode() + args.spec_bytes
        old = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)  # a signal must not land between start and record
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=child_env(), start_new_session=True)
            STATE["proc"] = p
        except OSError:
            p = None
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
        if p is None:
            code = 3
        else:
            record_child(lock, p.pid)
            code = None
            try:
                so, stderr_text = p.communicate(prompt, timeout=args.timeout)
            except subprocess.TimeoutExpired:
                kill_group(p)
                so, stderr_text = p.communicate()
                code = 5
            STATE["proc"] = None
            so, stderr_text = (so or b"").decode("utf-8", "replace"), (stderr_text or b"").decode("utf-8", "replace")
            info = parse_events(so)
            if code is None and p.returncode == 0 and not info["failed"]:  # an error event comes before the out file
                code = check_output(args, ctx["head"], ctx["base"], resolved)
                ctx["out_sha256"] = sha_file(args.out) if code == 0 else None
            elif code is None:
                blob = "\n".join(info["messages"] + [stderr_text or ""])
                code = 4 if LIMIT_RE.search(blob) else 6
                if code == 6:
                    hint = next((n for n, rx in HINTS if rx.search(blob)), "other")
    if tmp_schema:
        rm(tmp_schema)
    status = STATUS[code]
    log_row(ctx["log"], make_row(ctx, code, status, info, hint))
    print(json.dumps({"exit": code, "status": status, "out": args.out, "task_id": info["thread"],
                      "tokens": info["tokens"], "hint": hint}))
    if code:
        sys.stderr.write("codex_task: %s\n" % ("error " + hint if hint else status))
    return code

def main():
    ap = Parser(description=__doc__, allow_abbrev=False)
    ap.add_argument("--subject", choices=["spec", "final"], required=True)
    ap.add_argument("--round", choices=["initial", "followup"])
    ap.add_argument("--record-fallback", action="store_true")
    for name in ("cd", "prompt-file", "out", "factory", "label", "schema", "spec-file", "expect-head", "expect-base",
                 "base-ref", "open-ids", "phase", "head", "base"):
        ap.add_argument("--" + name)
    ap.add_argument("--model", default="gpt-6.1-sol")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = ap.parse_args()
    if args.record_fallback:  # no launch: only the factory, the subject and the head
        extra = ("cd", "prompt_file", "out", "label", "schema", "spec_file", "expect_head", "expect_base", "base_ref",
                 "open_ids", "phase", "round")
        if args.factory is None or args.head is None or any(getattr(args, n) is not None for n in extra):
            ap.error("")
    elif args.head is not None or args.base is not None or args.round is None or any(
            getattr(args, n) is None for n in ("cd", "prompt_file", "out", "factory", "label")):
        ap.error("")
    args.spec_copy = args.spec_bytes = None
    args.open_list = []
    STATE.update(lock=None, proc=None, ctx=None)
    old = {s: signal.signal(s, on_signal) for s in SIGNALS}
    try:
        return run(args)
    except Usage as e:
        sys.stderr.write("codex_task: usage %s\n" % e)
        return 2
    except Exception:
        sys.stderr.write("codex_task: crash\n")
        return 1
    finally:
        for s, h in old.items():
            signal.signal(s, h)

if __name__ == "__main__":
    sys.exit(main())
