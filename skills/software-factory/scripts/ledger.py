#!/usr/bin/env python3
"""Build an agent ledger for a software factory run from Claude Code's own transcripts.

Reads only labels, models, timings, token counts, and tool-call counts. It never
copies prompts, agent output, or tool arguments into the ledger.

    python3 ledger.py --factory .factory --title "Agent Ledger"
"""
import argparse, datetime as dt, glob, html, json, os, re, subprocess, sys

ROLE_COLORS = {"fable": "--fable", "opus": "--opus", "sonnet": "--sonnet", "haiku": "--haiku"}
# Claude API list prices in USD per million tokens for Haiku 5.5, Sonnet 5.5, Opus 5.5,
# and Fable 5.2: input, output, cache read. Haiku's second row applies to prompts over
# 100K tokens. Cache writes are priced at 1.25x input. Edit these if prices change.
PRICES = {
    "haiku": ((0.10, 0.50, 0.01), (0.50, 2.50, 0.05)),
    "sonnet": ((2.0, 10.0, 0.10),),
    "opus": ((4.0, 20.0, 0.20),),
    "fable": ((8.0, 40.0, 0.20),),
}
# OpenAI API list prices from each model's page, read 2026-11-12 (see docs/model-comparison.md).
# Subscription logins use plan limits instead. USD per million tokens: input, output, cached input.
# The over-272K tier (2x input and cache, 1.5x output) is not modelled: Codex usage is summed per
# turn, so the size of each prompt is unknown. Costs of very long prompts are too low.
CODEX_PRICES = {
    "gpt-6-astra": (10.0, 50.0, 1.00),
    "gpt-6.1-sol": (2.0, 10.0, 0.10),
    "gpt-6.1-terra": (1.0, 5.0, 0.05),
    "gpt-6-luna": (0.10, 0.50, 0.01),
}
LONG_PROMPT = 100_000
IDLE_MINUTES = 10
FIRST_COMMIT_TARGETS = {"S": 15, "M": 30}


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def find_sessions(args, run):
    """Session folders that ran this factory: named ones, the lead's own, and any holding its workflows."""
    if args.session_dir:
        return [args.session_dir]
    config = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
    root = os.path.join(config, "projects")
    ids = list(args.session_id or [])
    if not ids:
        # run.json lists every session that led the run; without it, assume the lead runs this script.
        ids = [s for s in run.get("sessions") or [] if isinstance(s, str)] or [os.environ.get("CLAUDE_CODE_SESSION_ID", "")]
    found = []
    def add(folder):
        if os.path.isdir(folder) and folder not in found:
            found.append(folder)
    for sid in ids:
        if re.fullmatch(r"[A-Za-z0-9-]+", sid or ""):
            for folder in glob.glob(os.path.join(root, "*", sid)):
                add(folder)
    for workflow in run.get("workflows") or []:
        run_id = workflow.get("runId") if isinstance(workflow, dict) else None
        if re.fullmatch(r"[A-Za-z0-9_-]+", run_id or ""):
            for folder in glob.glob(os.path.join(root, "*", "*", "subagents", "workflows", run_id)):
                add(os.path.dirname(os.path.dirname(os.path.dirname(folder))))
    if found:
        return found
    # Last resort: the transcript written most recently for this directory, which may be another session.
    project = os.path.join(root, re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()))
    logs = sorted(glob.glob(project + "/*.jsonl"), key=os.path.getmtime)
    if not logs:
        sys.exit(f"No session found for this run in {root}. Pass --session-dir or --session-id.")
    print(f"Warning: no session recorded for this run; using the newest one in {project}. "
          "Pass --session-id if that's wrong.", file=sys.stderr)
    return [logs[-1][: -len(".jsonl")]]


def read_agent(path):
    """Summarize one agent transcript. Repeated message ids count once."""
    start = end = model = None
    tools = 0
    usage = {}
    for line in open(path, errors="ignore"):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        stamp = row.get("timestamp")
        if stamp:
            start = start or stamp
            end = stamp
        message = row.get("message") or {}
        if row.get("type") != "assistant":
            continue
        model = message.get("model") or model
        if message.get("id") and message.get("usage"):
            usage[message["id"]] = message["usage"]
        content = message.get("content")
        if isinstance(content, list):
            tools += sum(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
    return {
        "start": start,
        "end": end,
        "model": model or "unknown",
        "tool_calls": tools,
        "output_tokens": sum(u.get("output_tokens", 0) for u in usage.values()),
        "cost": cost(model or "", usage.values()),
    }


def cost(model, usages):
    """Estimated USD at list prices, or None for a model with no price."""
    tiers = PRICES.get(family(model))
    if not tiers:
        return None
    total = 0.0
    for u in usages:
        fresh = u.get("input_tokens", 0) or 0
        written = u.get("cache_creation_input_tokens", 0) or 0
        read = u.get("cache_read_input_tokens", 0) or 0
        prompt = fresh + written + read
        rate_in, rate_out, rate_read = tiers[1 if len(tiers) > 1 and prompt > LONG_PROMPT else 0]
        total += (fresh * rate_in + written * rate_in * 1.25 + read * rate_read + (u.get("output_tokens", 0) or 0) * rate_out) / 1e6
    return round(total, 4)


def codex_cost(model, input_tokens, cached, output):
    """Estimated USD at OpenAI list prices, or None for a model with no price.

    Reasoning tokens are already inside output_tokens, so they are not added again.
    """
    prices = CODEX_PRICES.get(model or "")
    if not prices:
        return None
    rate_in, rate_out, rate_cached = prices
    cached = min(cached, input_tokens)
    return round(((input_tokens - cached) * rate_in + cached * rate_cached + output * rate_out) / 1e6, 10)


def read_codex(factory, since):
    """Rows from <factory>/codex-runs.jsonl that started at or after `since`.

    Bad lines, stale-lock rows, attempt rows and fallback rows are skipped. A cancelled row still counts as a run. Only label, model, phase, times,
    token counts and status are kept. The paths and hints in a row never leave here.
    """
    path = os.path.join(factory, "codex-runs.jsonl") if factory else None
    if not path or not os.path.exists(path):
        return []
    cutoff = parse_time(since) if since else None
    rows = []
    for line in open(path, errors="ignore"):
        try:
            entry = json.loads(line)
            if entry.get("status") in ("stale-lock", "attempt", "fallback"):
                continue  # a note about a lock, a launch or an Opus fallback, not a Codex run; the run has its own row
            begin, finish = entry["start"], entry.get("end") or entry["start"]
            minutes = round((parse_time(finish) - parse_time(begin)).total_seconds() / 60, 1)
            if cutoff and parse_time(begin) < cutoff:
                continue
            tokens = [int(entry.get(k) or 0) for k in ("input_tokens", "cached_input_tokens", "output_tokens")]
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        model = str(entry.get("model") or "unknown")
        rows.append({
            "start": begin,
            "end": finish,
            "model": model,
            "tool_calls": 0,
            "input_tokens": tokens[0],
            "cached_input_tokens": tokens[1],
            "output_tokens": tokens[2],
            "cost": codex_cost(model, *tokens),
            "label": str(entry.get("label") or entry.get("task_id") or "codex"),
            "phase": str(entry.get("phase") or "Review"),
            "minutes": minutes,
            "provider": "codex",
            "status": str(entry.get("status") or ""),
        })
    return rows


def money(value):
    return "n/a" if value is None else f"${value:,.2f}"


def total_cost(rows):
    priced = [r["cost"] for r in rows if r.get("cost") is not None]
    return sum(priced) if priced else None


def collect(sessions, since):
    agents = {}
    journals = [j for session in sessions for j in glob.glob(session + "/subagents/workflows/*/journal.jsonl")]
    directs = [a for session in sessions for a in glob.glob(session + "/subagents/agent-*.jsonl")]
    for journal in journals:
        folder = os.path.dirname(journal)
        for line in open(journal, errors="ignore"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") != "started" or not row.get("agentId"):
                continue
            path = os.path.join(folder, f"agent-{row['agentId']}.jsonl")
            if os.path.exists(path):
                agents[path] = (row.get("label") or row["agentId"], row.get("phase") or "Workflow")
    for path in directs:
        meta = path[: -len(".jsonl")] + ".meta.json"
        label = os.path.basename(path)[6:-6]
        try:
            label = json.load(open(meta)).get("description") or label
        except (OSError, ValueError):
            pass
        agents.setdefault(path, (label, "Direct"))

    cutoff = parse_time(since) if since else None
    rows = []
    for path, (label, phase) in agents.items():
        row = read_agent(path)
        if not row["start"] or (cutoff and parse_time(row["start"]) < cutoff):
            continue
        row.update(label=label, phase=phase)
        row["minutes"] = round((parse_time(row["end"]) - parse_time(row["start"])).total_seconds() / 60, 1)
        rows.append(row)
    return sorted(rows, key=lambda r: r["start"])


def read_sizes(path):
    """Sizes in the order they were declared, such as ["S", "M"] after a size-up."""
    return [size for size, _ in read_size_lines(path)]


def read_size_lines(path):
    """(size, reason) pairs from SIZE.md lines like 'M: 9 files, one migration'."""
    if not path or not os.path.exists(path):
        return []
    return re.findall(r"^([SML]):[ \t]*(.*)$", open(path, errors="ignore").read(), re.M)


def read_summary(path):
    """The lead's plain-language summary: paragraphs, and lines starting with '- ' as bullets."""
    if not path or not os.path.exists(path):
        return ""
    return open(path, errors="ignore").read().strip()


def git(*args):
    done = subprocess.run(["git", *args], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def changes(base, feature):
    """Commits, files, lines, and the top areas the feature branch changed against its base."""
    if not (base and feature):
        return None
    numstat = git("diff", "--numstat", f"{base}...{feature}")
    commits = git("rev-list", "--count", f"{base}..{feature}")
    if numstat is None or commits is None:
        return None
    added = removed = 0
    areas = {}
    files = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        added += int(parts[0]) if parts[0].isdigit() else 0
        removed += int(parts[1]) if parts[1].isdigit() else 0
        path = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", parts[2]).split(" => ")[-1].replace("//", "/")
        segments = path.split("/")
        area = "/".join(segments[:2]) if len(segments) > 2 else (segments[0] if len(segments) > 1 else "(root)")
        areas[area] = areas.get(area, 0) + 1
    top = sorted(areas.items(), key=lambda item: (-item[1], item[0]))
    return {
        "commits": int(commits.strip() or 0),
        "files": files,
        "added": added,
        "removed": removed,
        "areas": [{"area": a, "files": n} for a, n in top[:8]],
        "more_areas": max(len(top) - 8, 0),
    }


def read_results(path):
    """A JSON list, or JSON lines where the latest line for each package wins."""
    if not path or not os.path.exists(path):
        return None
    text = open(path, errors="ignore").read()
    if not path.endswith(".jsonl"):
        return json.loads(text)
    latest = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            latest.pop(row.get("id"), None)
            latest[row.get("id")] = row
    return list(latest.values())


def first_commit_minutes(base, feature, since):
    """Minutes from the run's start to its earliest commit, including unmerged package branches."""
    if not (base and feature and since):
        return None
    done = subprocess.run(
        ["git", "log", "--format=%ct", feature, "--branches=factory/*", "^" + base],
        capture_output=True,
        text=True,
    )
    if done.returncode:
        return None
    begin = parse_time(since).timestamp()
    stamps = [int(line) for line in done.stdout.split() if int(line) >= begin]
    return round((min(stamps) - begin) / 60, 1) if stamps else None


def family(model):
    lowered = model.lower()
    for name in ROLE_COLORS:
        if name in lowered:
            return name
    return "other"


def lane(row):
    """Colour key for a row: 'codex' for Codex rows, else the Claude model family."""
    return "codex" if row.get("provider") == "codex" else family(row["model"])


def timeline(rows):
    """Phases as swimlanes on one time axis, with overlapping agents stacked.

    Idle gaps over IDLE_MINUTES, such as outages or overnight pauses, are cut from
    the axis so they don't squash the work into a sliver.
    """
    begin = parse_time(rows[0]["start"])
    spans = sorted((parse_time(r["start"]), parse_time(r["end"])) for r in rows)
    gaps, reach = [], spans[0][1]
    for start, end in spans:
        if (start - reach).total_seconds() > IDLE_MINUTES * 60:
            gaps.append((reach, start))
        reach = max(reach, end)

    def active(stamp):
        when = parse_time(stamp)
        cut = sum((min(when, b) - a).total_seconds() for a, b in gaps if when > a)
        return ((when - begin).total_seconds() - cut) / 60

    span = max(active(max(r["end"] for r in rows)), 1)
    left, right, bar, gap = 130, 1090, 6, 2
    x = lambda minutes: left + minutes / span * (right - left)
    phases = list(dict.fromkeys(r["phase"] for r in rows))
    y = 30
    marks = []
    for phase in phases:
        ends = []
        top = y + 8
        for row in (r for r in rows if r["phase"] == phase):
            a, b = active(row["start"]), active(row["end"])
            x0, x1 = x(a), max(x(b), x(a) + 2)
            slot = next((i for i, e in enumerate(ends) if e < x0 - 1), None)
            if slot is None:
                slot = len(ends)
                ends.append(0)
            ends[slot] = x1
            tip = f"{row['label']}: {row['model']}, {row['minutes']} min, {row['tool_calls']} tool calls"
            marks.append(
                f'<rect x="{x0:.1f}" y="{top + slot * (bar + gap)}" width="{x1 - x0:.1f}" height="{bar}" '
                f'class="m-{lane(row)}"><title>{html.escape(tip)}</title></rect>'
            )
        height = max(len(ends) * (bar + gap), 14) + 16
        marks.append(f'<text x="12" y="{y + 20}" class="lane">{html.escape(phase[:18])}</text>')
        marks.append(f'<line x1="0" x2="1100" y1="{y + height}" y2="{y + height}" class="rule"/>')
        y += height
    step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 240, 480) if span / s <= 8), 960)
    ticks = []
    for minute in range(0, int(span) + 1, step):
        ticks.append(f'<line x1="{x(minute):.1f}" x2="{x(minute):.1f}" y1="24" y2="{y}" class="rule"/>')
        ticks.append(f'<text x="{x(minute):.1f}" y="16" class="tick">{minute} min</text>')
    return (
        f'<svg viewBox="0 0 1100 {y + 6}" role="img" aria-label="Agents by phase over {span:.0f} minutes">'
        + "".join(ticks + marks)
        + "</svg>",
        span,
        len(gaps),
    )


SIZE_NAMES = {
    "S": ("Small", "up to 5 existing files, no schema change, no live-data checks"),
    "M": ("Medium", "up to 15 existing files"),
    "L": ("Large", "more than 15 existing files, a new subsystem, or greenfield"),
}


def duration(minutes):
    return f"{minutes:.0f} minutes" if minutes < 120 else f"{minutes / 60:.1f} hours"


def summary_html(text):
    esc = html.escape
    out, bullets = [], []
    for block in re.split(r"\n\s*\n", text):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if lines and all(l.startswith(("- ", "* ")) for l in lines):
            out.append("<ul>" + "".join(f"<li>{esc(l[2:])}</li>" for l in lines) + "</ul>")
        elif lines:
            out.append(f"<p>{esc(' '.join(lines))}</p>")
    return "".join(out)


def tldr(rows, results, size_lines, change, summary, pr, span):
    esc = html.escape
    facts = []
    if size_lines:
        letters = " to ".join(s for s, _ in size_lines)
        size, reason = size_lines[-1]
        name, rule = SIZE_NAMES[size]
        detail = f"{name}: {rule}." + (f" Sized this way because {reason.rstrip('.')}." if reason else "")
        if len(size_lines) > 1:
            detail += " Sized up during the run."
        facts.append(("Size", letters, detail))
    if change:
        facts.append((
            "Changes",
            f"{change['files']:,} files",
            f"+{change['added']:,} / −{change['removed']:,} lines in {change['commits']:,} commit{'s' if change['commits'] != 1 else ''}",
        ))
    if results:
        clean = sum(1 for p in results if p.get("status") == "clean")
        facts.append(("Packages", f"{clean} of {len(results)} clean", "Built and tested in parallel lanes"))
    priced = total_cost(rows)
    facts.append(("Effort", f"{len(rows)} agents", f"{duration(span)} of active time" + (f", about {money(priced)} at API list prices" if priced is not None else "")))
    cells = "".join(f"<div><span>{esc(k)}</span><b>{esc(v)}</b><small>{esc(d)}</small></div>" for k, v, d in facts)
    areas = ""
    if change and change["areas"]:
        chips = "".join(f"<li><code>{esc(a['area'])}</code> {a['files']}</li>" for a in change["areas"])
        more = f"<li>and {change['more_areas']} more</li>" if change["more_areas"] else ""
        areas = f'<div class="areas"><span>Areas changed, by files</span><ul>{chips}{more}</ul></div>'
    link = ""
    if pr and re.match(r"^https?://", str(pr)):
        link = f'<p class="pr"><a href="{esc(str(pr))}">Pull request</a></p>'
    body = summary_html(summary) if summary else '<p class="muted">No summary was written for this run.</p>'
    return f'<section class="tldr"><h2>TL;DR</h2><div class="summary">{body}</div>{link}<div class="facts">{cells}</div>{areas}</section>'


def render(rows, results, title, sizes=(), first_commit=None, top=""):
    esc = html.escape
    chart, span, skipped = timeline(rows)
    counts = {}
    codex = [r for r in rows if r.get("provider") == "codex"]
    for row in rows:
        if row.get("provider") != "codex":
            counts[family(row["model"])] = counts.get(family(row["model"]), 0) + 1
    stats = [
        (len(rows), "agents"),
        (*[(counts.get(n, 0), n.capitalize() + " agents") for n in ("fable", "opus", "sonnet", "haiku") if counts.get(n) or n != "fable"],),
        *([(len(codex), "Codex runs")] if codex else []),
        (f"{span:.0f} min", "active time"),
        (f"{sum(r['tool_calls'] for r in rows):,}", "tool calls"),
        (f"{sum(r['output_tokens'] for r in rows):,}", "output tokens"),
        (money(total_cost(rows)), "estimated cost"),
    ]
    flat = []
    for stat in stats:
        flat.extend(stat if isinstance(stat[0], tuple) else [stat])
    if first_commit is not None:
        target = FIRST_COMMIT_TARGETS.get(sizes[-1]) if sizes else None
        flat.insert(0, (f"{first_commit:.0f} min", f"to first commit (target {target})" if target else "to first commit"))
    tiles = "".join(f"<div><b>{esc(str(a))}</b><span>{esc(b)}</span></div>" for a, b in flat)

    # Agents that never replied have no model, so they can't be judged here.
    off_model = [
        r
        for r in rows
        if re.search(r"\b(build|fix) \d+$", r["label"], re.I) and family(r["model"]) in ("opus", "fable")
    ]
    warning = (
        f'<p class="warn">{len(off_model)} build or fix agents ran on Opus or Fable instead of Haiku or Sonnet. Check that their model was set.</p>'
        if off_model
        else ""
    )
    wrong = [r for r in codex if r.get("status") == "wrong-target"]
    if wrong:
        warning += f'<p class="warn">{len(wrong)} Codex review{"s" if len(wrong) != 1 else ""} had the wrong target (wrong-target). Those reviews do not count as passes.</p>'

    def package_cost(pid):
        pattern = re.compile(rf"^{re.escape(str(pid))} (build|test) \d+$")
        return total_cost([r for r in rows if pattern.match(r["label"])])

    by_phase = {}
    for row in rows:
        by_phase.setdefault(row["phase"], []).append(row)
    phase_cells = "".join(
        f'<tr><td>{esc(name)}</td><td class="n">{len(group)}</td><td class="n">{sum(r["output_tokens"] for r in group):,}</td>'
        f'<td class="n">{money(total_cost(group))}</td></tr>'
        for name, group in by_phase.items()
    )
    phases = (
        '<section><h2>Cost by phase</h2><p>Estimated at Claude API list prices from each agent\'s token counts. Plan pricing may differ.</p>'
        '<div class="scroll"><table><thead><tr><th>Phase</th><th class="n">Agents</th><th class="n">Output tokens</th><th class="n">Estimated cost</th></tr></thead>'
        f"<tbody>{phase_cells}</tbody></table></div></section>"
    )

    packages = ""
    if results:
        cells = "".join(
            f'<tr><td>{esc(str(p.get("id", "")))}</td><td><span class="s-{esc(str(p.get("status", "")))}">{esc(str(p.get("status", "")))}</span></td>'
            f'<td class="n">{esc(str(p.get("cycle", "")))}</td>'
            f'<td class="n">{sum(len((r or {}).get("bugs", [])) for r in p.get("reports", []))}</td>'
            f'<td class="n">{money(package_cost(p.get("id", "")))}</td></tr>'
            for p in results
        )
        tally = {}
        for p in results:
            tally[p.get("status")] = tally.get(p.get("status"), 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(tally.items(), key=str))
        packages = (
            f"<section><h2>Packages</h2><p>{esc(summary)}</p><div class=\"scroll\"><table><thead><tr><th>Package</th><th>Outcome</th>"
            f'<th class="n">Cycles</th><th class="n">Bugs reported</th><th class="n">Build and test cost</th></tr></thead><tbody>{cells}</tbody></table></div></section>'
        )

    def flag(r):
        return f' <span class="s-blocked">{esc(r["status"])}</span>' if r.get("status") == "wrong-target" else ""

    provider_head = "<th>Provider</th>" if codex else ""
    roster = "".join(
        f'<tr><td>{esc(r["label"])}{flag(r)}</td><td>{esc(r["phase"])}</td>'
        + (f'<td><span class="p-{lane(r)}">{esc(r.get("provider", "claude"))}</span></td>' if codex else "")
        + f'<td><span class="p-{lane(r)}">{esc(r["model"])}</span></td>'
        f'<td class="t">{esc(r["start"][11:19])}</td><td class="n">{r["minutes"]}</td><td class="n">{r["tool_calls"]}</td><td class="n">{r["output_tokens"]:,}</td><td class="n">{money(r.get("cost"))}</td></tr>'
        for r in rows
    )
    note = (
        "<p>Codex costs use OpenAI API list prices read 2026-11-12. Cached input uses the cached price. "
        "Prompts over 272K input tokens cost more than shown. Subscription logins use plan limits instead. "
        "A model with no price shows n/a.</p>"
        if codex
        else ""
    )
    codex_key = '<span><i style="background:var(--codex)"></i>Codex</span>' if codex else ""
    # Codex colours and rules appear only when Codex rows exist.
    var_light = "--codex:#1F6F2B;" if codex else ""
    var_dark = "--codex:#7FD68A;" if codex else ""
    rule_m = ".m-codex{fill:var(--codex)}" if codex else ""
    rule_p = ".p-codex{color:var(--codex)}" if codex else ""
    day = esc(rows[0]["start"][:10])
    gap_note = f" {skipped} idle gaps over {IDLE_MINUTES} minutes are cut from the axis." if skipped else ""
    return f"""<title>{esc(title)}</title>
<meta charset="utf-8">
<meta name="claude:viewport-fit-sides" content="cover">
<style>
:root{{--bg:#EDF1F3;--panel:#F8FAFB;--fg:#14232B;--muted:#4F626D;--line:#C9D3D9;--fable:#8A2F7A;--opus:#3D42A8;--haiku:#B05A00;--sonnet:#0A7570;--other:#6B7780;{var_light}--ok:#23723F;--bad:#B7322C}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#10181D;--panel:#172228;--fg:#E6EDF0;--muted:#9DAFB9;--line:#2E3E47;--fable:#E58BD6;--opus:#9CA1F2;--haiku:#EFA650;--sonnet:#55CFC8;--other:#9DAFB9;{var_dark}--ok:#6FCF8F;--bad:#F2857F;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#10181D;--panel:#172228;--fg:#E6EDF0;--muted:#9DAFB9;--line:#2E3E47;--fable:#E58BD6;--opus:#9CA1F2;--haiku:#EFA650;--sonnet:#55CFC8;--other:#9DAFB9;{var_dark}--ok:#6FCF8F;--bad:#F2857F;color-scheme:dark}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0}}
.wrap{{max-width:1160px;margin:0 auto;padding-block:36px 56px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:flex;flex-direction:column;gap:32px}}
h1{{font-size:32px;line-height:1.1;margin:0 0 6px}}h2{{font-size:19px;margin:0}}p{{margin:0;color:var(--muted)}}
section{{display:flex;flex-direction:column;gap:10px;min-width:0}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
.tiles div{{background:var(--panel);outline:1px solid var(--line);padding:12px 14px;display:flex;flex-direction:column;min-width:0}}.tiles b{{font-size:22px;font-variant-numeric:tabular-nums;white-space:nowrap}}.tiles span{{font-size:13px;color:var(--muted)}}
.warn{{color:var(--bad);font-weight:600}}
.tldr{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:20px 22px;gap:14px}}
.tldr .summary{{display:flex;flex-direction:column;gap:8px}}.tldr .summary p,.tldr .summary li{{color:var(--fg);font-size:16px}}.tldr .summary ul{{margin:0;padding-left:20px}}
.tldr .muted{{color:var(--muted)}}.tldr a{{color:var(--opus);font-weight:600}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}
.facts div{{display:flex;flex-direction:column;gap:2px;border-top:2px solid var(--line);padding-top:8px;min-width:0}}
.facts span,.areas>span{{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}.facts b{{font-size:21px;font-variant-numeric:tabular-nums}}.facts small{{font-size:13px;color:var(--muted)}}
.areas ul{{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:6px}}.areas li{{font-size:13px;border:1px solid var(--line);border-radius:999px;padding:2px 10px;color:var(--muted)}}.areas code{{color:var(--fg);overflow-wrap:anywhere}}
.scroll{{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow-x:auto}}
svg{{display:block;width:100%;min-width:760px;height:auto;padding:8px 0}}
svg text{{fill:var(--muted);font-size:11px}}svg .lane{{fill:var(--fg);font-size:12px;font-weight:600}}svg .rule{{stroke:var(--line)}}
.m-fable{{fill:var(--fable)}}.m-opus{{fill:var(--opus)}}.m-haiku{{fill:var(--haiku)}}.m-sonnet{{fill:var(--sonnet)}}.m-other{{fill:var(--other)}}{rule_m}
.key{{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px;color:var(--muted)}}.key i{{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px}}
table{{border-collapse:collapse;width:100%;font-size:13px;min-width:700px}}th,td{{padding:7px 11px;text-align:left;border-bottom:1px solid var(--line)}}th{{font-size:12px;color:var(--muted)}}
.n{{text-align:right;font-variant-numeric:tabular-nums}}.t{{font-variant-numeric:tabular-nums;white-space:nowrap}}
.p-fable{{color:var(--fable)}}.p-opus{{color:var(--opus)}}.p-haiku{{color:var(--haiku)}}.p-sonnet{{color:var(--sonnet)}}.p-other{{color:var(--other)}}{rule_p}
.s-clean{{color:var(--ok);font-weight:600}}.s-blocked,.s-unverified,.s-needs-decision{{color:var(--bad);font-weight:600}}
</style>
<div class="wrap">
<header><h1>{esc(title)}</h1><p>Started {day} at {esc(rows[0]["start"][11:16])} UTC. Taken from the agents' own records; the lead session isn't counted.</p></header>
{top}
<div class="tiles">{tiles}</div>
{warning}
<section><h2>Timeline</h2><p>Each bar is one agent, grouped by phase. Hover a bar for details.{gap_note}</p>
<div class="scroll">{chart}</div>
<div class="key"><span><i style="background:var(--fable)"></i>Fable</span><span><i style="background:var(--opus)"></i>Opus</span><span><i style="background:var(--haiku)"></i>Haiku</span><span><i style="background:var(--sonnet)"></i>Sonnet</span><span><i style="background:var(--other)"></i>Other</span>{codex_key}</div></section>
{phases}
{packages}
<section><h2>Every agent</h2><div class="scroll"><table><thead><tr><th>Agent</th><th>Phase</th>{provider_head}<th>Model</th><th>Start (UTC)</th><th class="n">Minutes</th><th class="n">Tool calls</th><th class="n">Output tokens</th><th class="n">Estimated cost</th></tr></thead><tbody>{roster}</tbody></table></div>{note}</section>
</div>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--factory", help="A .factory folder: fills --since, --base, --feature, --pr, --size-file, --summary, --results, --out, and --json")
    parser.add_argument("--session-dir", help="One session folder to read instead of finding them")
    parser.add_argument("--session-id", action="append", help="A session id to read; repeat for several. Defaults to run.json sessions, the current session, and sessions holding the run's workflows")
    parser.add_argument("--since", help="ISO time; ignore agents that started earlier")
    parser.add_argument("--results", help="JSON list of package results from the build loop")
    parser.add_argument("--title", default="Agent Ledger")
    parser.add_argument("--out")
    parser.add_argument("--json", help="Also write the rows as JSON")
    parser.add_argument("--size-file", help="SIZE.md with lines like 'S: reason'")
    parser.add_argument("--base", help="Base ref, used with --feature and --since to time the first commit")
    parser.add_argument("--feature", help="Feature branch")
    parser.add_argument("--summary", help="SUMMARY.md: the lead's plain-language account of the work, shown in the TL;DR")
    parser.add_argument("--pr", help="Pull request URL for the TL;DR")
    args = parser.parse_args()
    run = {}
    if args.factory:
        try:
            run = json.load(open(os.path.join(args.factory, "run.json")))
        except (OSError, ValueError):
            pass
        args.since = args.since or run.get("started")
        args.base = args.base or run.get("base")
        args.feature = args.feature or run.get("feature")
        args.size_file = args.size_file or os.path.join(args.factory, "SIZE.md")
        args.results = args.results or os.path.join(args.factory, "results.jsonl")
        args.out = args.out or os.path.join(args.factory, "LEDGER.html")
        args.json = args.json or os.path.join(args.factory, "ledger.json")
        args.summary = args.summary or os.path.join(args.factory, "SUMMARY.md")
        args.pr = args.pr or (run.get("pr") if isinstance(run.get("pr"), str) else None)
    args.out = args.out or ".factory/LEDGER.html"

    sessions = find_sessions(args, run)
    # Codex rows count only in cross mode, and only from this run (at or after `since`). In single mode the ledger is as before.
    cross = run.get("review") == "cross"
    rows = collect(sessions, args.since)
    if cross:
        for row in rows:
            row["provider"] = "claude"
        rows += read_codex(args.factory, args.since)
        rows.sort(key=lambda r: r["start"])
    if not rows:
        sys.exit(f"No agents found in {', '.join(sessions)}" + (f" since {args.since}" if args.since else ""))
    results = read_results(args.results)
    sizes = read_sizes(args.size_file)
    first_commit = first_commit_minutes(args.base, args.feature, args.since)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as handle:
        size_lines = read_size_lines(args.size_file)
        change = changes(args.base, args.feature)
        summary = read_summary(args.summary)
        span = timeline(rows)[1]
        top = tldr(rows, results, size_lines, change, summary, args.pr, span)
        handle.write(render(rows, results, args.title, sizes, first_commit, top))
    if args.json:
        with open(args.json, "w") as handle:
            json.dump({"summary": summary, "changes": change, "pr": args.pr, "size": sizes, "first_commit_minutes": first_commit, "estimated_cost_usd": total_cost(rows), "agents": rows, "packages": results}, handle, indent=1)
    print(f"{len(rows)} agents -> {args.out}")


if __name__ == "__main__":
    main()
