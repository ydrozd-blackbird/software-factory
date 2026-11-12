"""Tests for codex_task.py. A stub `codex` runs instead of the real one (STEER 4). No network.

    python3 -m unittest tests.test_codex_task -v
"""
import contextlib, hashlib, importlib.util, io, json, os, shutil, signal, socket, subprocess, sys, tempfile, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "..", "skills", "software-factory", "scripts", "codex_task.py")
STUB_SRC = os.path.join(HERE, "stub", "codex")
GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
BANNED = ("danger-full-access", "--dangerously-bypass-approvals-and-sandbox", "--full-auto", "--add-dir")
SCHEMA = {"type": "object", "required": ["bugs", "target_ok"]}
BUGS_SCHEMA = os.path.join(HERE, "..", "skills", "software-factory", "references", "bugs.schema.json")


def write_text(path, text):
    with open(path, "w") as f:
        f.write(text)


def read_text(path):
    with open(path) as f:
        return f.read()


def sh(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def lock_name(subject):
    return hashlib.sha1(subject.encode()).hexdigest() + ".lock"


def load_helper():
    spec = importlib.util.spec_from_file_location("codex_task_unit", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256_of(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.stubdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.stubdir, True)
        shutil.copy(STUB_SRC, os.path.join(self.stubdir, "codex"))
        os.chmod(os.path.join(self.stubdir, "codex"), 0o755)
        self.repo = os.path.join(self.root, "repo")
        self.factory = os.path.join(self.repo, ".factory")
        os.makedirs(os.path.join(self.factory, "tmp"))
        sh("git", "init", "-q", cwd=self.repo)
        self.commits = []
        for n in "123":
            write_text(os.path.join(self.repo, "a.txt"), n + "\n")
            sh("git", "add", "a.txt", cwd=self.repo)
            sh("git", *GIT_ID, "commit", "-qm", "c" + n, cwd=self.repo)
            self.commits.append(self.head_of())
        self.c1, self.c2, self.c3 = self.commits
        sh("git", "checkout", "-q", "-b", "side", self.c1, cwd=self.repo)
        write_text(os.path.join(self.repo, "side.txt"), "s\n")
        sh("git", "add", "side.txt", cwd=self.repo)
        sh("git", *GIT_ID, "commit", "-qm", "side", cwd=self.repo)
        self.side = self.head_of()
        sh("git", "branch", "base", self.c1, cwd=self.repo)  # merge-base of base and c2 or c3 is c1
        sh("git", "branch", "atc2", self.c2, cwd=self.repo)  # merge-base of atc2 and c2 is c2: an empty diff
        self.at(self.c2)
        self.prompt = os.path.join(self.root, "prompt.txt")
        write_text(self.prompt, "do it\n")
        self.schema = os.path.join(self.root, "schema.json")
        write_text(self.schema, json.dumps(SCHEMA))
        self.n = 0

    def head_of(self):
        return sh("git", "rev-parse", "HEAD", cwd=self.repo)

    def at(self, sha):
        sh("git", "checkout", "-q", "--detach", sha, cwd=self.repo)

    def set_mode(self, mode):
        write_text(os.path.join(self.stubdir, "mode"), mode)

    def out(self):
        self.n += 1
        return os.path.join(self.factory, "tmp", "out%d.json" % self.n)

    def spec(self, text="spec text\n"):
        self.n += 1
        path = os.path.join(self.factory, "tmp", "spec-%d.md" % self.n)
        write_text(path, text)
        return path

    def command(self, subject="final", rnd="initial", head="auto", base=None, spec=None, out=None, schema="auto", extra=(),
                base_ref="auto", open_ids="auto"):
        followup = rnd == "followup"
        if head == "auto":
            head = None if subject == "spec" else (self.c3 if followup else self.c2)
        if base_ref == "auto":
            base_ref = "base" if subject == "final" and not followup else None
        if open_ids == "auto":
            open_ids = "" if followup else None
        cmd = [sys.executable, HELPER, "--cd", self.repo, "--prompt-file", self.prompt, "--out", out or self.out(),
               "--factory", self.factory, "--label", "L", "--subject", subject, "--round", rnd]
        if schema == "auto":
            cmd += ["--schema", self.schema]
        elif schema:
            cmd += ["--schema", schema]
        for flag, value in (("--expect-head", head), ("--expect-base", base), ("--spec-file", spec)):
            if value is not None:
                cmd += [flag, value]
        if base_ref is not None:
            cmd += ["--base-ref", base_ref]
        if open_ids is not None:
            cmd += ["--open-ids", open_ids]
        return cmd + list(extra)

    def environment(self, env_extra=None, path=None, use_bin=True):
        env = {"PATH": path or (self.stubdir + ":/usr/bin:/bin"), "HOME": self.root}
        if use_bin:
            env["CODEX_BIN"] = os.path.join(self.stubdir, "codex")
        env.update(env_extra or {})
        return env

    def prepare_stub(self, stub, subject, spec, base, report_base):
        self.set_mode(stub)
        for name in ("base", "reviewed_head"):
            if os.path.exists(os.path.join(self.stubdir, name)):
                os.remove(os.path.join(self.stubdir, name))
        if subject == "spec" and spec and os.path.exists(spec):
            write_text(os.path.join(self.stubdir, "reviewed_head"), "sha256:" + sha256_of(spec))
        report = base if report_base == "auto" else report_base
        if report is not None:
            write_text(os.path.join(self.stubdir, "base"), report)

    def expected_base(self, subject, rnd, base, base_ref):
        """The base a good report carries: none for Spec, a given base, the head of a normal initial round, or the merge-base."""
        if subject == "spec":
            return None
        if base:
            return base
        if rnd == "followup" or base_ref is None:
            return self.c2
        try:
            return sh("git", "merge-base", "base" if base_ref == "auto" else base_ref, "HEAD", cwd=self.repo)
        except subprocess.CalledProcessError:
            return None

    def run_helper(self, stub="ok", subject="final", rnd="initial", head="auto", base=None, spec=None, out=None,
                   schema="auto", extra=(), env_extra=None, path=None, use_bin=True, report_base="auto", base_ref="auto",
                   open_ids="auto"):
        cmd = self.command(subject, rnd, head, base, spec, out, schema, extra, base_ref, open_ids)
        self.prepare_stub(stub, subject, spec, self.expected_base(subject, rnd, base, base_ref), report_base)
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True, env=self.environment(env_extra, path, use_bin), timeout=60)
        self.elapsed = time.time() - t0
        self.out_path = cmd[cmd.index("--out") + 1]
        return r

    def lock_path(self, subject="final"):
        return os.path.join(self.factory, "tmp", "budget", lock_name(subject))

    def fallback(self, subject="final", head="auto", base=None, extra=()):
        """Record a fallback row, as the lead does after an Opus review."""
        if head == "auto":
            head = self.c2 if subject == "final" else "sha256:" + "a" * 64
        cmd = [sys.executable, HELPER, "--record-fallback", "--factory", self.factory, "--subject", subject, "--head", head]
        if base is not None:
            cmd += ["--base", base]
        return subprocess.run(cmd + list(extra), capture_output=True, text=True, env=self.environment(), timeout=60)

    def all_rows(self):
        p = os.path.join(self.factory, "codex-runs.jsonl")
        return [json.loads(l) for l in read_text(p).splitlines()] if os.path.exists(p) else []

    def log(self):
        return [r for r in self.all_rows() if r["status"] != "attempt"]

    def attempts(self):
        return [r for r in self.all_rows() if r["status"] == "attempt"]

    def argv(self):
        return read_text(os.path.join(self.stubdir, "args.txt")).splitlines()

    def launched(self):
        return os.path.exists(os.path.join(self.stubdir, "args.txt"))

    def assert_usage_keep_rows(self, r):
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")

    def assert_usage(self, r):
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertEqual(self.all_rows(), [])
        self.assertFalse(self.launched())


class Review(Base):
    def test_ok_log_and_tokens(self):
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        line = json.loads(r.stdout)
        self.assertEqual((line["status"], line["task_id"], line["hint"]), ("ok", "stub-thread-1", None))
        self.assertEqual(line["tokens"], {"input_tokens": 20, "cached_input_tokens": 4, "output_tokens": 10,
                                        "reasoning_output_tokens": 2, "cache_write_input_tokens": 6})
        row = self.log()[0]
        for k in ("start", "end", "provider", "model", "effort", "label", "phase", "subject", "round", "head", "base", "out", "cd",
                  "exit", "status", "hint", "task_id", "input_tokens", "cached_input_tokens", "output_tokens",
                  "reasoning_output_tokens", "cache_write_input_tokens"):
            self.assertIn(k, row)
        self.assertEqual((row["provider"], row["model"], row["effort"], row["phase"]), ("codex", "gpt-6.1-sol", "high", "Review"))
        self.assertEqual((row["subject"], row["round"], row["head"], row["base"]), ("final", "initial", self.c2, self.c1))
        self.assertEqual((row["input_tokens"], row["cached_input_tokens"], row["cache_write_input_tokens"]), (20, 4, 6))
        self.assertNotIn("mode", row)
        self.assertEqual(len(self.attempts()), 1)
        self.assertEqual(r.stderr, "")

    def test_argv(self):
        self.run_helper()
        a = self.argv()
        self.assertEqual(a[0], "exec")
        self.assertEqual(a[a.index("-s") + 1], "read-only")
        self.assertEqual(a[a.index("-C") + 1], self.repo)
        self.assertEqual(a[a.index("-m") + 1], "gpt-6.1-sol")
        self.assertEqual(a[-1], "-")
        for flag in ("--ephemeral", "--json", "--output-schema", "-o"):
            self.assertIn(flag, a)
        self.assertIn('model_reasoning_effort="high"', a)
        for b in BANNED:
            self.assertNotIn(b, a)

    def test_quota_noise_on_success_is_ok(self):
        self.assertEqual(self.run_helper(stub="quota_noise").returncode, 0)

    def test_usage_limit(self):
        r = self.run_helper(stub="limit")
        self.assertEqual(r.returncode, 4)
        self.assertEqual(json.loads(r.stdout)["status"], "usage-limit")
        self.assertEqual(r.stderr.strip(), "codex_task: usage-limit")
        self.assertNotIn("hit your", r.stderr + r.stdout)
        self.assertEqual((self.log()[0]["exit"], self.log()[0]["hint"]), (4, None))

    def test_rate_limit_phrase_is_limit(self):
        r = self.run_helper(stub="ratelimit")
        self.assertEqual(r.returncode, 4)
        self.assertEqual(r.stderr.strip(), "codex_task: usage-limit")

    def test_stray_429_on_failure_is_error_not_limit(self):
        r = self.run_helper(stub="stray429")
        self.assertEqual(r.returncode, 6)
        self.assertEqual(r.stderr.strip(), "codex_task: error other")
        self.assertNotIn("429abc", r.stderr + r.stdout)
        self.assertEqual(self.log()[0]["hint"], "other")

    def test_auth_hint(self):
        r = self.run_helper(stub="auth")
        self.assertEqual(r.returncode, 6)
        self.assertEqual(r.stderr.strip(), "codex_task: error auth")
        self.assertEqual(json.loads(r.stdout)["hint"], "auth")
        self.assertEqual(self.log()[0]["hint"], "auth")
        self.assertNotIn("Unauthorized", r.stderr + r.stdout)

    def test_plain_failure(self):
        r = self.run_helper(stub="fail")
        self.assertEqual(r.returncode, 6)
        self.assertEqual(r.stderr.strip(), "codex_task: error other")
        self.assertNotIn("boom", r.stderr + r.stdout)

    def test_timeout(self):
        r = self.run_helper(stub="hang", extra=["--timeout", "1"])
        self.assertEqual(r.returncode, 5)
        self.assertLess(self.elapsed, 10)
        self.assertEqual(self.log()[0]["status"], "timeout")
        self.assertFalse(os.path.exists(self.lock_path()))

    def test_missing_binary(self):
        if shutil.which("codex", path="/usr/bin:/bin"):
            self.skipTest("a real codex is on /usr/bin:/bin")
        r = self.run_helper(use_bin=False, path="/usr/bin:/bin")
        self.assertEqual(r.returncode, 3)
        self.assertEqual(json.loads(r.stdout)["status"], "missing")
        self.assertEqual(self.log()[0]["exit"], 3)

    def test_bad_json_is_bad_output(self):
        r = self.run_helper(stub="badjson")
        self.assertEqual(r.returncode, 7)
        self.assertEqual(self.log()[0]["status"], "bad-output")

    def test_missing_required_keys_is_bad_output(self):
        self.assertEqual(self.run_helper(stub="shape").returncode, 7)

    def test_free_text_is_bad_output(self):
        self.assertEqual(self.run_helper(stub="freetext").returncode, 7)

    def test_empty_scope_is_bad_output(self):
        self.assertEqual(self.run_helper(stub="noscope").returncode, 7)

    def test_shape_check_follows_root_ref(self):
        write_text(self.schema, json.dumps({"$ref": "#/$defs/BUGS", "$defs": {"BUGS": SCHEMA}}))
        self.assertEqual(self.run_helper(stub="shape").returncode, 7)
        self.assertEqual(self.run_helper().returncode, 0)

    def test_root_ref_schema_is_resolved_for_codex(self):
        full = {"$ref": "#/$defs/BUGS", "$defs": {"BUGS": {"type": "object", "properties": {"bugs": {}, "target_ok": {}},
                                                           "required": ["bugs", "target_ok"]}}}
        write_text(self.schema, json.dumps(full))
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        passed = read_text(os.path.join(self.stubdir, "schema_path.txt")).strip()
        self.assertFalse(os.path.exists(passed))
        got = json.loads(read_text(os.path.join(self.stubdir, "schema_copy.json")))
        self.assertIn("properties", got)
        self.assertNotIn("$ref", got)
        self.assertEqual(json.loads(read_text(self.schema)), full)

    def test_followup_report_needs_answers(self):
        self.assertEqual(self.run_helper(stub="noanswers").returncode, 0)  # an initial round needs none
        self.at(self.c3)
        self.assertEqual(self.run_helper(stub="noanswers", rnd="followup").returncode, 7)
        self.assertEqual(self.run_helper(stub="answered", rnd="followup", open_ids="F1").returncode, 0)

    def test_answer_status_must_be_known(self):
        self.assertEqual(self.run_helper().returncode, 0)
        self.at(self.c3)
        self.assertEqual(self.run_helper(stub="badanswer", rnd="followup", open_ids="F1").returncode, 7)

    def test_finding_without_id_is_bad_output(self):
        self.assertEqual(self.run_helper(stub="bugnoid").returncode, 7)

    def test_wrong_target_object_then_corrected_rerun(self):
        r = self.run_helper(stub="wrongtarget")
        self.assertEqual(r.returncode, 9)
        self.assertEqual(r.stderr.strip(), "codex_task: wrong-target")
        self.assertEqual(self.log()[0]["status"], "wrong-target")
        self.assertFalse(os.path.exists(self.lock_path()))
        self.assertEqual(self.run_helper().returncode, 0)

    def test_stale_reviewed_head_is_wrong_target(self):
        self.assertEqual(self.run_helper(stub="stalehead").returncode, 9)
        self.assertEqual(self.run_helper().returncode, 0)

    def test_report_base_mismatch_is_wrong_target(self):
        self.assertEqual(self.run_helper(report_base="b" * 40).returncode, 9)

    def test_null_report_base_is_wrong_target_for_final(self):
        self.assertEqual(self.run_helper(report_base=None).returncode, 9)

    def test_crash_frees_the_lock(self):
        os.mkdir(os.path.join(self.factory, "codex-runs.jsonl"))
        r = self.run_helper()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.strip(), "codex_task: crash")
        self.assertFalse(os.path.exists(self.lock_path()))

    def test_proxy_and_key_names_pass(self):
        r = self.run_helper(env_extra={"OPENAI_API_KEY": "k", "HTTPS_PROXY": "p", "https_proxy": "p", "SSL_CERT_FILE": "c",
                                       "CODEX_HOME": "/nowhere"})
        self.assertEqual(r.returncode, 0, r.stderr)
        keys = read_text(os.path.join(self.stubdir, "env.txt")).split()
        for k in ("PATH", "OPENAI_API_KEY", "HTTPS_PROXY", "https_proxy", "SSL_CERT_FILE", "CODEX_HOME"):
            self.assertIn(k, keys)
        self.assertNotIn("CODEX_BIN", keys)

    def test_foreign_names_do_not_pass(self):
        self.run_helper(env_extra={"ANTHROPIC_API_KEY": "a", "DATABASE_URL": "d", "AWS_SECRET_ACCESS_KEY": "s"})
        keys = read_text(os.path.join(self.stubdir, "env.txt")).split()
        for k in ("ANTHROPIC_API_KEY", "DATABASE_URL", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(k, keys)

    def test_log_row_has_no_prompt_text(self):
        write_text(self.prompt, "MARKER-7731 please review\n")
        r = self.run_helper()
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("MARKER-7731", read_text(os.path.join(self.factory, "codex-runs.jsonl")) + r.stdout + r.stderr)

    def test_spec_with_file_ok(self):
        spec = self.spec()
        r = self.run_helper(subject="spec", spec=spec)
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self.log()[0]
        self.assertEqual(row["head"], "sha256:" + sha256_of(spec))
        self.assertIsNone(row["base"])
        self.assertEqual(row["subject"], "spec")
        self.assertFalse(os.path.exists(self.lock_path("spec")))

    def test_spec_bundle_is_copied_and_named_in_the_prompt(self):
        spec = self.spec("bundle text\n")
        self.assertEqual(self.run_helper(subject="spec", spec=spec).returncode, 0)
        copy = os.path.join(self.factory, "tmp", "spec-%s.md" % sha256_of(spec))
        self.assertEqual(read_text(copy), "bundle text\n")
        prompt = read_text(os.path.join(self.stubdir, "stdin.txt"))
        self.assertTrue(prompt.startswith("do it\n"))
        self.assertIn(copy, prompt)

    def test_final_prompt_names_no_spec_copy(self):
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(read_text(os.path.join(self.stubdir, "stdin.txt")), "do it\n")

    def test_spec_prompt_carries_the_exact_bundle_bytes(self):
        text = "line one \u00e9\n\n  indented\ttab\r\nno final newline"
        spec = self.spec(text)
        with open(spec, "wb") as f:
            f.write(text.encode("utf-8"))
        self.assertEqual(self.run_helper(subject="spec", spec=spec).returncode, 0)
        with open(os.path.join(self.stubdir, "stdin.txt"), "rb") as f:
            prompt = f.read()
        self.assertTrue(prompt.endswith(text.encode("utf-8")))
        self.assertIn(("sha256 %s" % sha256_of(spec)).encode(), prompt)
        copy = os.path.join(self.factory, "tmp", "spec-%s.md" % sha256_of(spec))
        with open(copy, "rb") as f:
            self.assertEqual(f.read(), text.encode("utf-8"))

    def test_spec_report_with_a_base_is_wrong_target(self):
        spec = self.spec()
        r = self.run_helper(subject="spec", spec=spec, report_base=self.c1)
        self.assertEqual(r.returncode, 9)
        self.assertEqual(self.log()[0]["status"], "wrong-target")
        self.assertEqual(self.run_helper(subject="spec", spec=spec).returncode, 0)

    def test_spec_report_must_echo_the_bundle_hash(self):
        spec = self.spec()
        self.assertEqual(self.run_helper(subject="spec", spec=spec, stub="stalehead").returncode, 9)

    def test_spec_report_for_another_hash_is_wrong_target(self):
        self.assertEqual(self.run_helper(subject="spec", spec=self.spec(), stub="stalehead").returncode, 9)

    def test_spec_target_ok_false_is_wrong_target(self):
        self.assertEqual(self.run_helper(subject="spec", spec=self.spec(), stub="wrongtarget").returncode, 9)


class OpenIds(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_helper().returncode, 0)  # a finished initial round
        self.at(self.c3)

    def test_followup_needs_open_ids(self):
        r = self.run_helper(rnd="followup", open_ids=None)
        self.assert_usage_keep_rows(r)
        self.assertIn("open-ids-missing", r.stderr)

    def test_initial_round_refuses_open_ids(self):
        self.at(self.c2)
        r = self.run_helper(subject="spec", spec=self.spec(), open_ids="F1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("open-ids-on-initial", r.stderr)

    def test_bad_ids_refused(self):
        for bad in ("F1,F1", "F 1", "F1,,F2", ",", "F1;F2"):
            r = self.run_helper(rnd="followup", open_ids=bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("bad-open-ids", r.stderr)

    def test_one_answer_per_id_is_ok(self):
        r = self.run_helper(rnd="followup", stub="answered2", open_ids="F1,F3")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_open_ids_order_and_spaces_do_not_matter(self):
        self.assertEqual(self.run_helper(rnd="followup", stub="answered2", open_ids=" F3 , F1 ").returncode, 0)

    def test_missing_answer_is_bad_output(self):
        r = self.run_helper(rnd="followup", stub="answered", open_ids="F1,F3")
        self.assertEqual(r.returncode, 7)
        self.assertEqual(self.log()[-1]["status"], "bad-output")

    def test_duplicate_answer_is_bad_output(self):
        self.assertEqual(self.run_helper(rnd="followup", stub="dupanswers", open_ids="F1,F3").returncode, 7)
        self.assertEqual(self.run_helper(rnd="followup", stub="dupanswers", open_ids="F1").returncode, 7)

    def test_extra_answer_is_bad_output(self):
        self.assertEqual(self.run_helper(rnd="followup", stub="extraanswer", open_ids="F1").returncode, 7)
        self.assertEqual(self.run_helper(rnd="followup", stub="extraanswer", open_ids="F1,F3").returncode, 7)

    def test_bug_answered_fixed_or_rejected_is_contradictory(self):
        r = self.run_helper(rnd="followup", stub="contradict", open_ids="F1")
        self.assertEqual(r.returncode, 7)
        self.assertEqual(self.log()[-1]["status"], "bad-output")
        self.assertEqual(self.run_helper(rnd="followup", stub="stillopen", open_ids="F1").returncode, 0)  # the round was not spent
        self.assertEqual([x["status"] for x in self.log()], ["ok", "bad-output", "ok"])

    def test_bug_answered_rejected_is_contradictory(self):
        self.assertEqual(self.run_helper(rnd="followup", stub="contradictrej", open_ids="F1").returncode, 7)
        self.assertEqual(self.log()[-1]["status"], "bad-output")

    def test_bug_answered_open_is_not_contradictory(self):
        self.assertEqual(self.run_helper(rnd="followup", stub="stillopen", open_ids="F1").returncode, 0)

    def test_no_open_ids_means_no_answers(self):
        self.assertEqual(self.run_helper(rnd="followup", stub="answered", open_ids="").returncode, 7)
        self.assertEqual(self.run_helper(rnd="followup", open_ids="").returncode, 0)

    def test_bad_output_does_not_spend_the_round(self):
        self.assertEqual(self.run_helper(rnd="followup", stub="answered", open_ids="F1,F3").returncode, 7)
        self.assertEqual(self.run_helper(rnd="followup", stub="answered2", open_ids="F1,F3").returncode, 0)
        self.assertEqual([x["status"] for x in self.log()], ["ok", "bad-output", "ok"])


class ErrorEvents(Base):
    def test_error_event_with_exit_0_is_an_error(self):
        r = self.run_helper(stub="errorevent")
        self.assertEqual(r.returncode, 6)  # the out file is a good report, but the stream had an error
        self.assertEqual(self.log()[0]["status"], "error")
        self.assertNotIn("stream broke", r.stdout + r.stderr)
        self.assertIsNone(self.log()[0]["out_sha256"])

    def test_error_event_with_a_limit_phrase_is_usage_limit(self):
        r = self.run_helper(stub="errorlimit")
        self.assertEqual(r.returncode, 4)
        self.assertEqual(r.stderr.strip(), "codex_task: usage-limit")
        self.assertEqual(self.log()[0]["status"], "usage-limit")

    def test_error_event_ends_no_ok_round(self):
        self.assertEqual(self.run_helper(stub="errorevent").returncode, 6)
        self.assertEqual(self.run_helper().returncode, 0)  # a retry may still pass


class ReportSchema(Base):
    def test_report_is_checked_against_the_schema_given_to_codex(self):
        r = self.run_helper(schema=BUGS_SCHEMA)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(read_text(os.path.join(self.stubdir, "schema_copy.json")))
        self.assertIn("properties", got)

    def test_extra_key_breaks_the_real_schema(self):
        self.assertEqual(self.run_helper(schema=BUGS_SCHEMA, stub="extrakey").returncode, 7)

    def test_bug_with_a_bad_field_breaks_the_real_schema(self):
        self.assertEqual(self.run_helper(schema=BUGS_SCHEMA, stub="bugnoid").returncode, 7)
        self.assertEqual(self.run_helper(schema=BUGS_SCHEMA, stub="noscope").returncode, 7)

    def test_valid_checks_types_enums_and_nested_keys(self):
        mod = load_helper()
        schema = json.loads(read_text(BUGS_SCHEMA))
        root = mod.resolve_schema(schema)
        good = {"provider": "codex", "model": "m", "base": None, "reviewed_head": "h", "target_ok": True,
                "scope": {"checked": ["a"], "unchecked": []}, "bugs": [], "answers": []}
        self.assertTrue(mod.valid(good, root, root))

        def changed(**kw):
            return dict(good, **kw)

        bug = {"id": "F1", "title": "t", "severity": "low", "repro": "r", "expected": "e", "actual": "a", "evidence": "v",
               "files": [], "verdict": None}
        self.assertTrue(mod.valid(changed(bugs=[bug]), root, root))
        for bad in (changed(target_ok="yes"), changed(base=3), changed(scope={"checked": [], "unchecked": []}),
                    changed(scope={"checked": [1], "unchecked": []}), changed(scope={"checked": ["a"]}),
                    changed(bugs=[dict(bug, severity="urgent")]), changed(bugs=[dict(bug, files=[1])]),
                    changed(bugs=[dict(bug, verdict="maybe")]), changed(bugs=[{k: v for k, v in bug.items() if k != "id"}]),
                    changed(answers=[{"id": "F1", "status": "fixed"}]), changed(answers=[{"id": 1, "status": "fixed", "evidence": ""}]),
                    changed(extra=1), {k: v for k, v in good.items() if k != "answers"}, [good]):
            self.assertFalse(mod.valid(bad, root, root), bad)

    def test_valid_follows_local_refs(self):
        mod = load_helper()
        root = {"type": "object", "properties": {"a": {"$ref": "#/$defs/N"}}, "$defs": {"N": {"type": "integer"}}}
        self.assertTrue(mod.valid({"a": 1}, root, root))
        self.assertFalse(mod.valid({"a": "x"}, root, root))
        self.assertFalse(mod.valid({"a": True}, root, root))  # a boolean is not an integer
        self.assertFalse(mod.valid({"a": 1}, {"properties": {"a": {"$ref": "#/$defs/Missing"}}}, {}))


class RecordFallback(Base):
    def test_row_is_written_and_nothing_is_launched(self):
        r = self.fallback(head=self.c2, base=self.c1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "fallback")
        row = self.all_rows()[0]
        self.assertEqual((row["status"], row["subject"], row["round"], row["head"], row["base"]),
                         ("fallback", "final", "initial", self.c2, self.c1))
        self.assertIn("start", row)
        self.assertFalse(self.launched())
        self.assertEqual(os.listdir(os.path.join(self.factory, "tmp", "budget")), [])  # the lock is freed
        self.assertEqual(len(self.all_rows()), 1)

    def test_spec_hash_forms(self):
        digest = "b" * 64
        self.assertEqual(self.fallback("spec", head="sha256:" + digest).returncode, 0)
        self.assertEqual(self.fallback("spec", head=digest).returncode, 0)  # the same hash in the other form
        self.assertEqual([x["head"] for x in self.all_rows()], ["sha256:" + digest])
        self.assertEqual(self.all_rows()[0]["base"], None)

    def test_bad_input_writes_nothing(self):
        for r in (self.fallback("final", head="abc"), self.fallback("final", head=self.c2, base="xyz"),
                  self.fallback("spec", head="abc"), self.fallback("spec", head="a" * 64, base=self.c1),
                  self.fallback("final", head="sha256:" + "a" * 64)):
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertEqual(r.stdout, "")
        self.assertEqual(self.all_rows(), [])

    def test_needs_factory_subject_and_head(self):
        for cmd in ([sys.executable, HELPER, "--record-fallback", "--subject", "final", "--head", self.c2],
                    [sys.executable, HELPER, "--record-fallback", "--factory", self.factory, "--head", self.c2],
                    [sys.executable, HELPER, "--record-fallback", "--factory", self.factory, "--subject", "final"]):
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertEqual(self.all_rows(), [])

    def test_launch_options_are_refused(self):
        r = self.fallback(head=self.c2, extra=["--round", "initial"])
        self.assertEqual(r.returncode, 2)
        r = self.fallback(head=self.c2, extra=["--prompt-file", self.prompt])
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.all_rows(), [])

    def test_head_and_base_belong_to_record_fallback_only(self):
        r = self.run_helper(extra=["--head", self.c2])
        self.assert_usage(r)

    def test_refused_after_an_ok_initial_round(self):
        self.assertEqual(self.run_helper().returncode, 0)
        r = self.fallback(head=self.c2)
        self.assertEqual(r.returncode, 2)
        self.assertIn("initial-already-ok", r.stderr)
        self.assertEqual([x["status"] for x in self.all_rows()], ["attempt", "ok"])

    def test_missing_factory_refused(self):
        cmd = [sys.executable, HELPER, "--record-fallback", "--factory", os.path.join(self.root, "none"), "--subject",
               "final", "--head", self.c2]
        self.assertEqual(subprocess.run(cmd, capture_output=True, text=True).returncode, 2)

    def test_a_fallback_row_of_an_earlier_run_does_not_count(self):
        self.assertEqual(self.fallback(head=self.c2).returncode, 0)
        write_text(os.path.join(self.factory, "run.json"), json.dumps({"started": "2999-01-01T00:00:00Z"}))
        self.at(self.c3)
        r = self.run_helper(rnd="followup")
        self.assert_usage_keep_rows(r)
        self.assertIn("followup-without-initial", r.stderr)

    def test_identical_fallback_twice_is_one_row(self):
        for _ in range(2):
            r = self.fallback(head=self.c2, base=self.c1)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)["status"], "fallback")
        self.assertEqual([x["status"] for x in self.all_rows()], ["fallback"])

    def test_conflicting_fallback_is_refused(self):
        self.assertEqual(self.fallback(head=self.c2, base=self.c1).returncode, 0)
        for head, base in ((self.c3, self.c1), (self.c2, self.c3), (self.c2, None)):
            r = self.fallback(head=head, base=base)
            self.assertEqual((r.returncode, r.stdout), (2, ""))
            self.assertIn("conflicting-fallback", r.stderr)
        self.assertEqual(len(self.all_rows()), 1)
        self.assertEqual(os.listdir(os.path.join(self.factory, "tmp", "budget")), [])

    def test_held_lock_makes_record_fallback_busy(self):
        os.makedirs(os.path.join(self.factory, "tmp", "budget"))
        write_text(self.lock_path(), "")
        r = self.fallback(head=self.c2)
        self.assertEqual(r.returncode, 8, r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "busy")
        self.assertEqual(r.stderr.strip(), "codex_task: busy")
        self.assertEqual(self.all_rows(), [])
        self.assertTrue(os.path.exists(self.lock_path()))  # another helper's lock stays
        self.assertEqual(self.fallback("spec").returncode, 0)  # another subject has its own lock

    def test_live_owner_makes_record_fallback_busy(self):
        os.makedirs(os.path.join(self.factory, "tmp", "budget"))
        owner = {"pid": os.getpid(), "host": socket.gethostname(), "start": time.time(), "timeout": 540}
        write_text(self.lock_path(), json.dumps(owner))  # a live helper owns the lock
        r = self.fallback(head=self.c2)
        self.assertEqual(r.returncode, 8)
        self.assertEqual(json.loads(r.stdout)["status"], "busy")
        self.assertEqual(self.all_rows(), [])
        self.assertEqual(json.loads(read_text(self.lock_path()))["pid"], os.getpid())

    def run_in_process(self, argv, before_reserve):
        """Run main() in this process with the stub codex. before_reserve() runs just before the lock is taken."""
        mod = load_helper()
        real_reserve = mod.reserve
        def reserve(*a, **kw):
            before_reserve()
            return real_reserve(*a, **kw)
        env = {"PATH": self.stubdir + ":/usr/bin:/bin", "HOME": self.root, "CODEX_BIN": os.path.join(self.stubdir, "codex")}
        out = io.StringIO()
        with mock.patch.object(mod, "reserve", reserve), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "argv", ["codex_task.py"] + argv[2:]), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = mod.main()
        return code, out.getvalue(), err.getvalue()

    def test_fallback_between_first_read_and_lock_stops_the_initial_round(self):
        self.prepare_stub("ok", "final", None, self.c1, self.c1)
        log = os.path.join(self.factory, "codex-runs.jsonl")
        def write_fallback():  # the lead records a fallback after the initial call read the log
            row = {"start": "2999-01-01T00:00:00Z", "provider": "fallback", "subject": "final", "round": "initial",
                   "head": self.c2, "base": self.c1, "status": "fallback", "exit": None}
            write_text(log, json.dumps(row) + "\n")
        code, out, err = self.run_in_process(self.command(rnd="initial", head=self.c2), write_fallback)
        self.assertEqual(code, 2, err)
        self.assertIn("initial-after-fallback", err)
        self.assertFalse(self.launched())
        self.assertEqual([x["status"] for x in self.all_rows()], ["fallback"])  # no attempt row
        self.assertFalse(os.path.exists(self.lock_path()))

    def test_a_round_without_a_new_fallback_still_launches_in_process(self):
        self.prepare_stub("ok", "final", None, self.c1, self.c1)
        code, out, err = self.run_in_process(self.command(rnd="initial", head=self.c2), lambda: None)
        self.assertEqual(code, 0, err)
        self.assertTrue(self.launched())

    def test_a_fallback_row_is_per_subject(self):
        self.assertEqual(self.fallback("spec").returncode, 0)
        self.at(self.c3)
        self.assertEqual(self.run_helper(rnd="followup").returncode, 2)
        self.assertEqual(self.fallback("final", head=self.c2).returncode, 0)
        self.assertEqual(self.run_helper(rnd="followup").returncode, 0)


class UsageErrors(Base):
    def test_bogus_flag_prints_only_usage(self):
        r = subprocess.run([sys.executable, HELPER, "--bogus"], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (2, "", "codex_task: usage\n"))

    def test_missing_required_args(self):
        r = subprocess.run([sys.executable, HELPER, "--subject", "final"], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (2, ""))

    def test_out_outside_tmp(self):
        self.assert_usage(self.run_helper(out=os.path.join(self.root, "out.json")))

    def test_out_exists(self):
        out = self.out()
        write_text(out, "old")
        self.assert_usage(self.run_helper(out=out))

    def test_tmp_symlink(self):
        other = os.path.join(self.root, "elsewhere")
        os.makedirs(other)
        shutil.rmtree(os.path.join(self.factory, "tmp"))
        os.symlink(other, os.path.join(self.factory, "tmp"))
        self.assert_usage(self.run_helper(out=os.path.join(self.factory, "tmp", "out.json")))

    def test_out_parent_symlink_leaves_tmp(self):
        other = os.path.join(self.root, "elsewhere")
        os.makedirs(other)
        os.symlink(other, os.path.join(self.factory, "tmp", "link"))
        self.assert_usage(self.run_helper(out=os.path.join(self.factory, "tmp", "link", "out.json")))
        self.assertEqual(os.listdir(other), [])

    def test_schema_required(self):
        r = self.run_helper(schema=None)
        self.assert_usage(r)
        self.assertIn("schema-missing", r.stderr)

    def test_bad_schema_path(self):
        self.assert_usage(self.run_helper(schema=os.path.join(self.root, "none.json")))

    def test_spec_without_file_refused(self):
        r = self.run_helper(subject="spec")
        self.assert_usage(r)
        self.assertIn("spec-file-missing", r.stderr)

    def test_spec_file_outside_tmp_refused(self):
        outside = os.path.join(self.root, "spec.md")
        write_text(outside, "x")
        self.assert_usage(self.run_helper(subject="spec", spec=outside))

    def test_spec_file_symlink_refused(self):
        real = self.spec()
        link = os.path.join(self.factory, "tmp", "link.md")
        os.symlink(real, link)
        self.assert_usage(self.run_helper(subject="spec", spec=link))

    def test_spec_with_sha_refused(self):
        spec = self.spec()
        r = self.run_helper(subject="spec", spec=spec, head=self.c2)
        self.assert_usage(r)
        self.assertIn("spec-takes-no-sha", r.stderr)
        self.assert_usage(self.run_helper(subject="spec", spec=spec, base=self.c1))

    def test_final_with_spec_file_refused(self):
        r = self.run_helper(spec=self.spec())
        self.assert_usage(r)
        self.assertIn("final-takes-no-spec-file", r.stderr)

    def test_final_without_head_refused(self):
        r = self.run_helper(head=None)
        self.assert_usage(r)
        self.assertIn("expect-head-missing", r.stderr)

    def test_final_initial_without_base_ref_refused(self):
        r = self.run_helper(base_ref=None)
        self.assert_usage(r)
        self.assertIn("base-ref-missing", r.stderr)
        r = self.run_helper(base_ref=None, extra=["--base-ref=-x"])
        self.assert_usage(r)
        self.assertIn("bad-base-ref", r.stderr)

    def test_spec_takes_no_base_ref(self):
        self.assert_usage(self.run_helper(subject="spec", spec=self.spec(), base_ref="base"))

    def test_shas_must_be_40_hex(self):
        self.assert_usage(self.run_helper(head="abc123"))
        self.assert_usage(self.run_helper(head="G" * 40))
        self.assert_usage(self.run_helper(base="abc"))

    def test_head_mismatch_before_launch(self):
        r = self.run_helper(head="0" * 40)
        self.assert_usage(r)
        self.assertFalse(os.path.exists(self.lock_path()))

    def test_empty_diff_refused(self):
        r = self.run_helper(base_ref="atc2")  # the merge-base is HEAD itself
        self.assert_usage(r)
        self.assertIn("empty-diff", r.stderr)

    def test_unknown_base_ref_refused(self):
        r = self.run_helper(base_ref="no-such-ref")
        self.assert_usage(r)
        self.assertIn("merge-base-failed", r.stderr)

    def test_git_that_cannot_run_is_usage_not_a_pass(self):
        r = self.run_helper(path="/nonexistent")
        self.assert_usage(r)
        self.assertIn("rev-parse-failed", r.stderr)

    def test_cd_that_is_not_a_repo_refused(self):
        plain = os.path.join(self.root, "plain")
        os.makedirs(plain)
        cmd = self.command()
        cmd[cmd.index("--cd") + 1] = plain
        self.prepare_stub("ok", "final", None, self.c1, "auto")
        r = subprocess.run(cmd, capture_output=True, text=True, env=self.environment())
        self.assert_usage(r)
        self.assertIn("rev-parse-failed", r.stderr)

    def test_expect_base_must_equal_the_merge_base(self):
        r = self.run_helper(base=self.c2)
        self.assert_usage(r)
        self.assertIn("wrong-base", r.stderr)
        r = self.run_helper(base=self.c1)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_final_base_is_the_merge_base_of_the_ref(self):
        r = self.run_helper(base_ref="side")  # side is a commit off c1: the base is c1, not side
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log()[0]["base"], self.c1)
        self.assertNotEqual(self.log()[0]["base"], self.side)

    def test_followup_without_initial_refused(self):
        self.at(self.c3)
        r = self.run_helper(rnd="followup")
        self.assert_usage(r)
        self.assertIn("followup-without-initial", r.stderr)
        self.assert_usage(self.run_helper(subject="spec", rnd="followup", spec=self.spec()))

    def test_followup_after_a_fallback_row_is_allowed(self):
        # Codex was tried and hit its limit. The lead reviewed with Opus and recorded a fallback row.
        self.assertEqual(self.run_helper(stub="limit").returncode, 4)
        self.at(self.c3)
        r = self.run_helper(rnd="followup")
        self.assert_usage_keep_rows(r)  # an attempt row alone does not allow a follow-up
        self.assertIn("followup-without-initial", r.stderr)
        self.assertEqual(self.fallback(head=self.c2).returncode, 0)
        r = self.run_helper(rnd="followup")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log()[-1]["base"], self.c2)
        r = self.run_helper(rnd="followup")
        self.assertEqual(r.returncode, 8)  # the follow-up is spent: never a third round
        self.assertEqual(json.loads(r.stdout)["status"], "budget-spent")
        self.assertEqual([x["round"] for x in self.log() if x["status"] == "ok"], ["followup"])

    def test_initial_after_a_fallback_row_is_refused(self):
        self.assertEqual(self.run_helper(stub="limit").returncode, 4)
        self.assertEqual(self.fallback(head=self.c2).returncode, 0)
        before = len(self.all_rows())
        r = self.run_helper(rnd="initial", head=self.c2)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("initial-after-fallback", r.stderr)
        self.assertEqual(len(self.all_rows()), before)  # no launch, no new attempt row
        self.at(self.c3)
        r = self.run_helper(rnd="followup")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ok_followup_refuses_any_later_round(self):
        self.assertEqual(self.run_helper(stub="limit").returncode, 4)
        self.assertEqual(self.fallback(head=self.c2).returncode, 0)
        self.at(self.c3)
        self.assertEqual(self.run_helper(rnd="followup").returncode, 0)
        before = len(self.all_rows())
        r = self.run_helper(rnd="initial", head=self.c3)
        self.assertEqual(r.returncode, 8)
        self.assertEqual(json.loads(r.stdout)["status"], "budget-spent")
        self.assertEqual(len(self.all_rows()), before)  # no launch, no new attempt row

    def test_spec_followup_after_a_fallback_row_is_allowed(self):
        one = self.spec("one\n")
        self.assertEqual(self.run_helper(stub="fail", subject="spec", spec=one).returncode, 6)
        r = self.run_helper(subject="spec", rnd="followup", spec=self.spec("two\n"))
        self.assertEqual(r.returncode, 2, r.stderr)  # only an attempt row so far
        self.assertEqual(self.fallback("spec", head="sha256:" + sha256_of(one)).returncode, 0)
        r = self.run_helper(subject="spec", rnd="followup", spec=self.spec("two\n"))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_followup_with_an_attempt_row_only_is_usage(self):
        self.at(self.c3)
        r = self.run_helper(rnd="followup")
        self.assert_usage(r)
        self.assertIn("followup-without-initial", r.stderr)
        # an initial attempt alone, and a follow-up attempt alone, are not a finished initial round
        self.at(self.c2)
        self.assertEqual(self.run_helper(stub="fail", rnd="initial", head=self.c2).returncode, 6)
        self.at(self.c3)
        self.assertEqual(self.run_helper(rnd="followup").returncode, 2)
        self.assertEqual(self.run_helper(stub="fail", rnd="followup").returncode, 2)

    def test_final_followup_base_must_be_initial_head(self):
        self.assertEqual(self.run_helper().returncode, 0)
        self.at(self.c3)
        for other in (self.c1, self.c3):
            r = self.run_helper(rnd="followup", base=other)
            self.assertEqual(r.returncode, 2)
            self.assertIn("wrong-base", r.stderr)
        self.assertEqual(self.run_helper(rnd="followup", base=self.c2).returncode, 0)
        self.assertEqual(self.log()[-1]["base"], self.c2)

    def test_final_followup_base_is_the_fallback_head(self):
        self.assertEqual(self.fallback(head=self.c1).returncode, 0)
        self.at(self.c3)
        r = self.run_helper(rnd="followup", base=self.c2)
        self.assertIn("wrong-base", r.stderr)
        r = self.run_helper(rnd="followup", report_base=self.c1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log()[-1]["base"], self.c1)

    def test_final_followup_base_that_is_not_an_ancestor_refused(self):
        self.assertEqual(self.fallback(head=self.side).returncode, 0)
        self.at(self.c3)
        r = self.run_helper(rnd="followup")
        self.assertEqual(r.returncode, 2)
        self.assertIn("base-not-ancestor", r.stderr)

    def test_final_followup_with_an_unknown_base_object_is_usage_not_a_pass(self):
        self.assertEqual(self.fallback(head="9" * 40).returncode, 0)
        self.at(self.c3)
        r = self.run_helper(rnd="followup")
        self.assertEqual(r.returncode, 2)
        self.assertIn("merge-base-failed", r.stderr)

    def test_final_followup_on_the_same_head_is_an_empty_diff(self):
        self.assertEqual(self.run_helper().returncode, 0)
        r = self.run_helper(rnd="followup", head=self.c2)
        self.assertEqual(r.returncode, 2)
        self.assertIn("empty-diff", r.stderr)

    def test_spec_followup_same_hash_refused(self):
        self.assertEqual(self.run_helper(subject="spec", spec=self.spec("same\n")).returncode, 0)
        r = self.run_helper(subject="spec", rnd="followup", spec=self.spec("same\n"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("empty-diff", r.stderr)

    def test_spec_followup_new_hash_ok(self):
        self.assertEqual(self.run_helper(subject="spec", spec=self.spec("one\n")).returncode, 0)
        r = self.run_helper(subject="spec", rnd="followup", spec=self.spec("two\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([x["round"] for x in self.log()], ["initial", "followup"])

    def test_same_out_refused_on_retry(self):
        out = self.out()
        self.assertEqual(self.run_helper(stub="badjson", out=out).returncode, 7)
        r = self.run_helper(out=out)
        self.assertEqual(r.returncode, 2)
        self.assertIn("out-exists", r.stderr)


class RunBoundary(Base):
    def start_run(self, when):
        write_text(os.path.join(self.factory, "run.json"), json.dumps({"started": when, "phase": "start"}))

    def test_rows_of_an_earlier_run_do_not_count(self):
        self.assertEqual(self.run_helper(subject="spec", spec=self.spec("one\n")).returncode, 0)
        self.assertEqual(self.run_helper().returncode, 0)
        shutil.rmtree(os.path.join(self.factory, "tmp"))  # cleanup keeps codex-runs.jsonl
        os.makedirs(os.path.join(self.factory, "tmp"))
        self.start_run("2999-01-01T00:00:00Z")  # a second run starts after every row above
        r = self.run_helper(subject="spec", spec=self.spec("two\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(len([x for x in self.log() if x["status"] == "ok"]), 4)

    def test_rows_of_this_run_still_count(self):
        self.start_run("2000-01-01T00:00:00Z")
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.run_helper().returncode, 8)

    def test_attempts_of_an_earlier_run_do_not_count(self):
        self.assertEqual(self.run_helper(stub="fail").returncode, 6)
        self.assertEqual(self.run_helper(stub="fail").returncode, 6)
        self.start_run("2999-01-01T00:00:00Z")
        self.assertEqual(self.run_helper().returncode, 0)

    def test_followup_needs_an_initial_row_from_this_run(self):
        self.assertEqual(self.run_helper().returncode, 0)
        self.start_run("2999-01-01T00:00:00Z")
        self.at(self.c3)
        r = self.run_helper(rnd="followup")
        self.assert_usage_keep_rows(r)
        self.assertIn("followup-without-initial", r.stderr)

    def test_no_run_json_or_no_started_counts_every_row(self):
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.run_helper().returncode, 8)
        write_text(os.path.join(self.factory, "run.json"), json.dumps({"phase": "start"}))
        self.assertEqual(self.run_helper().returncode, 8)
        write_text(os.path.join(self.factory, "run.json"), "not json")
        self.assertEqual(self.run_helper().returncode, 8)


class Budget(Base):
    def test_budget_spent(self):
        self.assertEqual(self.run_helper().returncode, 0)
        r = self.run_helper()
        self.assertEqual(r.returncode, 8)
        self.assertEqual(json.loads(r.stdout)["status"], "budget-spent")
        self.assertEqual(r.stderr.strip(), "codex_task: budget-spent")
        self.assertEqual(len(self.log()), 1)

    def test_exit8_prints_prior_row(self):
        first = self.out()
        self.assertEqual(self.run_helper(out=first).returncode, 0)
        line = json.loads(self.run_helper().stdout)
        self.assertEqual((line["prior_out"], line["prior_head"], line["prior_base"]), (first, self.c2, self.c1))
        self.assertEqual(self.log()[0]["out"], first)

    def test_exit8_reusable_only_on_same_target(self):
        first = self.out()
        self.assertEqual(self.run_helper(out=first).returncode, 0)
        self.assertTrue(json.loads(self.run_helper().stdout)["reusable"])
        self.at(self.c3)  # a new head: the prior report is for another target
        line = json.loads(self.run_helper(head=self.c3).stdout)
        self.assertEqual((line["exit"], line["reusable"], line["prior_head"]), (8, False, self.c2))
        self.at(self.c2)
        os.remove(first)
        self.assertFalse(json.loads(self.run_helper().stdout)["reusable"])

    def test_ok_row_has_out_sha256(self):
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.log()[0]["out_sha256"], sha256_of(self.out_path))

    def test_exit8_not_reusable_when_out_file_changed(self):
        first = self.out()
        self.assertEqual(self.run_helper(out=first).returncode, 0)
        write_text(first, '{"tampered": true}\n')
        line = json.loads(self.run_helper().stdout)
        self.assertEqual((line["exit"], line["reusable"]), (8, False))

    def test_effort_must_be_a_known_word(self):
        for bad in ('x"', "extreme", ""):
            self.assert_usage(self.run_helper(extra=["--effort", bad]))
        self.assertEqual(self.run_helper(extra=["--effort", "max"]).returncode, 0)
        self.assertIn('model_reasoning_effort="max"', self.argv())

    def test_exit8_reusable_for_spec_only_with_same_file(self):
        self.assertEqual(self.run_helper(subject="spec", spec=self.spec("a\n")).returncode, 0)
        self.assertTrue(json.loads(self.run_helper(subject="spec", spec=self.spec("a\n")).stdout)["reusable"])
        line = json.loads(self.run_helper(subject="spec", spec=self.spec("changed\n")).stdout)
        self.assertEqual((line["exit"], line["reusable"]), (8, False))
        self.assertTrue(line["prior_head"].startswith("sha256:"))

    def test_lock_is_freed_after_ok_and_after_failure(self):
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertFalse(os.path.exists(self.lock_path()))  # the log, not the lock, holds the spent budget
        self.assertEqual(self.run_helper(stub="fail", subject="spec", spec=self.spec()).returncode, 6)
        self.assertFalse(os.path.exists(self.lock_path("spec")))

    def test_retry_with_fresh_out_after_bad_output(self):
        self.assertEqual(self.run_helper(stub="badjson").returncode, 7)
        self.assertFalse(os.path.exists(self.lock_path()))
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual([x["status"] for x in self.log()], ["bad-output", "ok"])

    def test_attempts_capped_at_two(self):
        self.assertEqual(self.run_helper(stub="badjson").returncode, 7)
        self.assertEqual(self.run_helper(stub="badjson").returncode, 7)
        os.remove(os.path.join(self.stubdir, "args.txt"))
        r = self.run_helper()
        self.assertEqual(r.returncode, 8)
        self.assertEqual(json.loads(r.stdout)["status"], "attempts-spent")  # no ok row: not budget-spent
        self.assertEqual(r.stderr.strip(), "codex_task: attempts-spent")
        self.assertFalse(self.launched())  # args.txt is not written again
        self.assertEqual(len(self.attempts()), 2)
        self.assertFalse(os.path.exists(self.lock_path()))

    def test_attempts_count_every_result(self):
        self.assertEqual(self.run_helper(stub="fail").returncode, 6)
        self.assertEqual(self.run_helper(stub="wrongtarget").returncode, 9)
        r = self.run_helper()
        self.assertEqual(r.returncode, 8)
        self.assertEqual(json.loads(r.stdout)["status"], "attempts-spent")

    def test_exit8_names_one_status(self):
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(json.loads(self.run_helper().stdout)["status"], "budget-spent")
        os.makedirs(os.path.join(self.factory, "tmp", "budget"), exist_ok=True)
        write_text(self.lock_path("spec"), "")
        self.assertEqual(json.loads(self.run_helper(subject="spec", spec=self.spec()).stdout)["status"], "busy")

    def test_existing_young_lock_is_busy(self):
        os.makedirs(os.path.join(self.factory, "tmp", "budget"))
        write_text(self.lock_path(), "")
        r = self.run_helper()
        self.assertEqual(r.returncode, 8)
        self.assertEqual(json.loads(r.stdout)["status"], "busy")
        self.assertIsNone(json.loads(r.stdout)["prior_head"])
        self.assertFalse(self.launched())
        self.assertEqual(self.all_rows(), [])

    def write_owner(self, pid, host=None, start=None, timeout=540, child_pgid=None):
        os.makedirs(os.path.join(self.factory, "tmp", "budget"), exist_ok=True)
        owner = {"pid": pid, "host": host or socket.gethostname(), "start": time.time() if start is None else start,
                 "timeout": timeout}
        if child_pgid is not None:
            owner["child_pid"] = owner["child_pgid"] = child_pgid
        write_text(self.lock_path(), json.dumps(owner))

    def dead_pid(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid

    def start_hanging_round(self, **kw):
        """Start a round that hangs. Return the process and the lock owner once the child runs."""
        self.set_mode("hang")
        proc = subprocess.Popen(self.command(**kw), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=self.environment())
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        for _ in range(100):
            try:
                owner = json.loads(read_text(self.lock_path()))
            except (OSError, ValueError):
                owner = {}
            if owner.get("child_pgid") and os.path.exists(os.path.join(self.stubdir, "pid")):
                return proc, owner
            time.sleep(0.1)
        self.fail("the round did not start")

    def stop(self, proc):
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=10)

    def test_lock_holds_pid_host_start_and_timeout(self):
        proc, owner = self.start_hanging_round(extra=["--timeout", "77"])
        self.stop(proc)
        self.assertEqual(sorted(owner), ["child_pgid", "child_pid", "host", "pid", "start", "timeout"])
        self.assertEqual((owner["host"], owner["timeout"]), (socket.gethostname(), 77))
        self.assertEqual(owner["child_pgid"], owner["child_pid"])

    def test_one_lock_per_subject_not_per_round(self):
        proc, _ = self.start_hanging_round()
        try:
            self.assertEqual(os.listdir(os.path.join(self.factory, "tmp", "budget")), [lock_name("final")])
            self.at(self.c3)
            r = self.run_helper(rnd="followup")  # the initial round still runs
            self.assertEqual(r.returncode, 8)
            self.assertEqual(json.loads(r.stdout)["status"], "busy")
            self.assertEqual([x["status"] for x in self.all_rows()], ["attempt"])  # the follow-up logged nothing
        finally:
            self.stop(proc)
        self.assertEqual([x["status"] for x in self.all_rows()], ["attempt", "cancelled"])
        r = self.run_helper(subject="spec", spec=self.spec())  # another subject has its own lock
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_followup_while_the_initial_lock_is_held_is_busy(self):
        os.makedirs(os.path.join(self.factory, "tmp", "budget"))
        write_text(self.lock_path(), "")
        self.at(self.c3)
        r = self.run_helper(rnd="followup")
        self.assertEqual(r.returncode, 8)
        self.assertEqual(json.loads(r.stdout)["status"], "busy")
        self.assertEqual(self.all_rows(), [])
        self.assertFalse(self.launched())

    def test_lock_file_is_named_by_the_sha1_of_the_subject(self):
        self.assertEqual(lock_name("final"), hashlib.sha1(b"final").hexdigest() + ".lock")
        os.makedirs(os.path.join(self.factory, "tmp", "budget"))
        write_text(os.path.join(self.factory, "tmp", "budget", "final-initial.lock"), "")  # the old name is not a lock
        self.assertEqual(self.run_helper().returncode, 0)

    def test_foreign_lock_is_not_removed(self):
        self.write_owner(os.getpid())  # a live pid that is not the helper
        r = self.run_helper()
        self.assertEqual(r.returncode, 8)
        self.assertEqual(json.loads(r.stdout)["status"], "busy")
        self.assertTrue(os.path.exists(self.lock_path()))
        self.assertEqual(json.loads(read_text(self.lock_path()))["pid"], os.getpid())
        self.assertEqual(self.all_rows(), [])

    def test_killed_helper_leaves_a_stale_lock_that_is_reclaimed(self):
        self.set_mode("hang")
        proc = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=self.environment())
        pgid = None
        try:
            for _ in range(100):
                try:
                    pgid = json.loads(read_text(self.lock_path())).get("child_pgid")
                except (OSError, ValueError):
                    pgid = None
                if pgid and os.path.exists(os.path.join(self.stubdir, "pid")):
                    break
                time.sleep(0.1)
            self.assertTrue(pgid)
            proc.kill()
            proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
        self.assertTrue(os.path.exists(self.lock_path()))  # SIGKILL skipped the cleanup
        self.assertEqual(self.run_helper().returncode, 8)  # the Codex group still runs, so the lock is live
        os.killpg(pgid, signal.SIGKILL)
        for _ in range(100):
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        r = self.run_helper()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("codex_task: stale-lock", r.stderr)
        self.assertEqual([x["status"] for x in self.log()], ["stale-lock", "ok"])
        self.assertEqual(self.run_helper().returncode, 8)

    def test_lock_of_a_live_helper_is_not_stale(self):
        self.write_owner(os.getpid())
        self.assertEqual(self.run_helper().returncode, 8)
        self.assertEqual(self.log(), [])

    def test_lock_older_than_timeout_plus_120_is_stale(self):
        self.write_owner(os.getpid(), start=time.time() - 77 - 121, timeout=77)
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.log()[0]["status"], "stale-lock")

    def test_lock_inside_timeout_plus_120_is_kept(self):
        self.write_owner(os.getpid(), start=time.time() - 77 - 60, timeout=77)
        self.assertEqual(self.run_helper().returncode, 8)

    def test_dead_pid_on_another_host_is_not_stale_by_pid(self):
        self.write_owner(self.dead_pid(), host="other-host.invalid")
        self.assertEqual(self.run_helper().returncode, 8)

    def test_dead_pid_on_this_host_is_stale(self):
        self.write_owner(self.dead_pid())
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.log()[0]["status"], "stale-lock")

    def test_dead_helper_with_live_child_group_keeps_the_lock(self):
        child = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: (child.kill(), child.wait()))
        self.write_owner(self.dead_pid(), child_pgid=child.pid)
        self.assertEqual(self.run_helper().returncode, 8)
        self.assertEqual(self.all_rows(), [])
        self.assertFalse(self.launched())

    def test_dead_helper_with_dead_child_group_is_stale(self):
        self.write_owner(self.dead_pid(), child_pgid=self.dead_pid())
        self.assertEqual(self.run_helper().returncode, 0)

    def test_live_child_group_does_not_outlast_timeout_plus_120(self):
        child = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: (child.kill(), child.wait()))
        self.write_owner(self.dead_pid(), start=time.time() - 77 - 121, timeout=77, child_pgid=child.pid)
        self.assertEqual(self.run_helper().returncode, 0)

    def run_in_process(self, injected):
        """Run the helper in this process. `injected` runs between the first log read and the lock."""
        spec = importlib.util.spec_from_file_location("codex_task_under_test", HELPER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        real_reserve = mod.reserve

        def reserve(*a, **k):
            injected()
            return real_reserve(*a, **k)

        out, err = io.StringIO(), io.StringIO()
        self.prepare_stub("ok", "final", None, self.c1, "auto")
        with mock.patch.object(mod, "reserve", reserve), mock.patch.object(sys, "argv", self.command()[1:]), \
                mock.patch.dict(os.environ, self.environment(), clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main()
        return code, out.getvalue(), err.getvalue()

    def append_ok_row(self):
        row = {"status": "ok", "subject": "final", "round": "initial", "head": "c" * 40, "base": "d" * 40, "provider": "codex"}
        with open(os.path.join(self.factory, "codex-runs.jsonl"), "a") as f:
            f.write(json.dumps(row) + "\n")

    def test_ok_row_after_first_read_stops_the_launch(self):
        code, out, err = self.run_in_process(self.append_ok_row)
        self.assertEqual(code, 8, err)
        self.assertEqual((json.loads(out)["status"], json.loads(out)["prior_head"]), ("budget-spent", "c" * 40))
        self.assertFalse(self.launched())
        self.assertEqual(len(self.all_rows()), 1)  # only the injected row
        self.assertFalse(os.path.exists(self.lock_path()))

    def test_ok_row_after_reclaiming_a_stale_lock_stops_the_launch(self):
        self.write_owner(self.dead_pid())
        code, out, err = self.run_in_process(self.append_ok_row)
        self.assertEqual(code, 8, err)
        self.assertFalse(self.launched())
        self.assertEqual([x["status"] for x in self.all_rows()], ["ok"])

    def test_two_calls_on_a_stale_lock_launch_once(self):
        self.write_owner(self.dead_pid())
        self.assert_one_launch()

    def test_two_parallel_calls_launch_once(self):
        self.assert_one_launch()

    def assert_one_launch(self):
        self.prepare_stub("slow", "final", None, self.c1, "auto")
        env = self.environment()
        procs = [subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                 for _ in range(2)]
        results = [p.communicate(timeout=60) + (p.returncode,) for p in procs]
        self.assertEqual(sorted(r[2] for r in results), [0, 8])
        self.assertEqual([x["status"] for x in self.log()].count("ok"), 1)

    def test_followup_after_initial_then_spent(self):
        self.assertEqual(self.run_helper().returncode, 0)
        self.at(self.c3)
        self.assertEqual(self.run_helper(rnd="followup").returncode, 0)
        self.assertEqual(self.run_helper(rnd="followup").returncode, 8)
        self.assertEqual(len(self.log()), 2)

    def test_other_subject_is_separate(self):
        self.assertEqual(self.run_helper(subject="spec", spec=self.spec()).returncode, 0)
        self.assertEqual(self.run_helper().returncode, 0)

    def test_sigterm_kills_child_and_frees_lock(self):
        self.set_mode("hang")
        proc = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=self.environment())
        pid_file = os.path.join(self.stubdir, "pid")
        try:
            for _ in range(100):
                if os.path.exists(pid_file) and read_text(pid_file).strip():
                    break
                time.sleep(0.1)
            stub_pid = int(read_text(pid_file))
            self.assertTrue(os.path.exists(self.lock_path()))
            proc.send_signal(signal.SIGTERM)
            _, err = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(err.strip(), "codex_task: stopped")
        for _ in range(50):
            try:
                os.kill(stub_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("the stub is still running")
        self.assertFalse(os.path.exists(self.lock_path()))
        self.assertEqual([x["status"] for x in self.all_rows()], ["attempt", "cancelled"])


if __name__ == "__main__":
    unittest.main()
