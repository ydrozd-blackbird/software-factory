#!/usr/bin/env python3
"""Build an agent ledger for a software factory run from Claude Code's own transcripts.

Reads only labels, models, timings, token counts, and tool-call counts. It never
copies prompts, agent output, or tool arguments into the ledger.

    python3 ledger.py --since "$(cat .factory/started-at)" --results .factory/results.json \
        --out .factory/LEDGER.html --json .factory/ledger.json
"""
import argparse, datetime as dt, glob, html, json, os, re, subprocess, sys

ROLE_COLORS = {"opus": "--opus", "sonnet": "--sonnet", "haiku": "--haiku"}
IDLE_MINUTES = 10
FIRST_COMMIT_TARGETS = {"S": 15, "M": 30}


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def find_session(args):
    if args.session_dir:
        return args.session_dir
    config = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
    project = os.path.join(config, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.getcwd()))
    if args.session_id:
        return os.path.join(project, args.session_id)
    # The running session is the transcript written to most recently.
    logs = sorted(glob.glob(project + "/*.jsonl"), key=os.path.getmtime)
    if not logs:
        sys.exit(f"No session transcripts in {project}. Pass --session-dir.")
    return logs[-1][: -len(".jsonl")]


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
    }


def collect(session, since):
    agents = {}
    for journal in glob.glob(session + "/subagents/workflows/*/journal.jsonl"):
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
    for path in glob.glob(session + "/subagents/agent-*.jsonl"):
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
    if not path or not os.path.exists(path):
        return []
    return re.findall(r"^([SML]):", open(path, errors="ignore").read(), re.M)


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
                f'class="m-{family(row["model"])}"><title>{html.escape(tip)}</title></rect>'
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


def render(rows, results, title, sizes=(), first_commit=None):
    esc = html.escape
    chart, span, skipped = timeline(rows)
    counts = {}
    for row in rows:
        counts[family(row["model"])] = counts.get(family(row["model"]), 0) + 1
    stats = [
        (len(rows), "agents"),
        (*[(counts.get(n, 0), n.capitalize() + " agents") for n in ("opus", "haiku", "sonnet")],),
        (f"{span:.0f} min", "active time"),
        (f"{sum(r['tool_calls'] for r in rows):,}", "tool calls"),
        (f"{sum(r['output_tokens'] for r in rows):,}", "output tokens"),
    ]
    flat = []
    for stat in stats:
        flat.extend(stat if isinstance(stat[0], tuple) else [stat])
    if first_commit is not None:
        target = FIRST_COMMIT_TARGETS.get(sizes[-1]) if sizes else None
        flat.insert(0, (f"{first_commit:.0f} min", f"to first commit (target {target})" if target else "to first commit"))
    if sizes:
        flat.insert(0, (" to ".join(sizes), "size"))
    tiles = "".join(f"<div><b>{esc(str(a))}</b><span>{esc(b)}</span></div>" for a, b in flat)

    # Agents that never replied have no model, so they can't be judged here.
    off_model = [
        r
        for r in rows
        if re.search(r"\b(build|fix) \d+$", r["label"], re.I) and family(r["model"]) not in ("haiku", "other")
    ]
    warning = (
        f'<p class="warn">{len(off_model)} build or fix agents did not run on Haiku. Check that their model was set.</p>'
        if off_model
        else ""
    )

    packages = ""
    if results:
        cells = "".join(
            f'<tr><td>{esc(str(p.get("id", "")))}</td><td><span class="s-{esc(str(p.get("status", "")))}">{esc(str(p.get("status", "")))}</span></td>'
            f'<td class="n">{esc(str(p.get("cycle", "")))}</td>'
            f'<td class="n">{sum(len((r or {}).get("bugs", [])) for r in p.get("reports", []))}</td></tr>'
            for p in results
        )
        tally = {}
        for p in results:
            tally[p.get("status")] = tally.get(p.get("status"), 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(tally.items(), key=str))
        packages = (
            f"<section><h2>Packages</h2><p>{esc(summary)}</p><div class=\"scroll\"><table><thead><tr><th>Package</th><th>Outcome</th>"
            f'<th class="n">Cycles</th><th class="n">Bugs reported</th></tr></thead><tbody>{cells}</tbody></table></div></section>'
        )

    roster = "".join(
        f'<tr><td>{esc(r["label"])}</td><td>{esc(r["phase"])}</td><td><span class="p-{family(r["model"])}">{esc(r["model"])}</span></td>'
        f'<td class="t">{esc(r["start"][11:19])}</td><td class="n">{r["minutes"]}</td><td class="n">{r["tool_calls"]}</td><td class="n">{r["output_tokens"]:,}</td></tr>'
        for r in rows
    )
    day = esc(rows[0]["start"][:10])
    gap_note = f" {skipped} idle gaps over {IDLE_MINUTES} minutes are cut from the axis." if skipped else ""
    return f"""<title>{esc(title)}</title>
<meta charset="utf-8">
<meta name="claude:viewport-fit-sides" content="cover">
<style>
:root{{--bg:#EDF1F3;--panel:#F8FAFB;--fg:#14232B;--muted:#4F626D;--line:#C9D3D9;--opus:#3D42A8;--haiku:#B05A00;--sonnet:#0A7570;--other:#6B7780;--ok:#23723F;--bad:#B7322C}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#10181D;--panel:#172228;--fg:#E6EDF0;--muted:#9DAFB9;--line:#2E3E47;--opus:#9CA1F2;--haiku:#EFA650;--sonnet:#55CFC8;--other:#9DAFB9;--ok:#6FCF8F;--bad:#F2857F;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#10181D;--panel:#172228;--fg:#E6EDF0;--muted:#9DAFB9;--line:#2E3E47;--opus:#9CA1F2;--haiku:#EFA650;--sonnet:#55CFC8;--other:#9DAFB9;--ok:#6FCF8F;--bad:#F2857F;color-scheme:dark}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0}}
.wrap{{max-width:1160px;margin:0 auto;padding-block:36px 56px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));display:flex;flex-direction:column;gap:32px}}
h1{{font-size:32px;line-height:1.1;margin:0 0 6px}}h2{{font-size:19px;margin:0}}p{{margin:0;color:var(--muted)}}
section{{display:flex;flex-direction:column;gap:10px;min-width:0}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
.tiles div{{background:var(--panel);padding:12px 14px;display:flex;flex-direction:column;min-width:0}}.tiles b{{font-size:23px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}}.tiles span{{font-size:13px;color:var(--muted)}}
.warn{{color:var(--bad);font-weight:600}}
.scroll{{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow-x:auto}}
svg{{display:block;width:100%;min-width:760px;height:auto;padding:8px 0}}
svg text{{fill:var(--muted);font-size:11px}}svg .lane{{fill:var(--fg);font-size:12px;font-weight:600}}svg .rule{{stroke:var(--line)}}
.m-opus{{fill:var(--opus)}}.m-haiku{{fill:var(--haiku)}}.m-sonnet{{fill:var(--sonnet)}}.m-other{{fill:var(--other)}}
.key{{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px;color:var(--muted)}}.key i{{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px}}
table{{border-collapse:collapse;width:100%;font-size:13px;min-width:700px}}th,td{{padding:7px 11px;text-align:left;border-bottom:1px solid var(--line)}}th{{font-size:12px;color:var(--muted)}}
.n{{text-align:right;font-variant-numeric:tabular-nums}}.t{{font-variant-numeric:tabular-nums;white-space:nowrap}}
.p-opus{{color:var(--opus)}}.p-haiku{{color:var(--haiku)}}.p-sonnet{{color:var(--sonnet)}}.p-other{{color:var(--other)}}
.s-clean{{color:var(--ok);font-weight:600}}.s-blocked,.s-unverified{{color:var(--bad);font-weight:600}}
</style>
<div class="wrap">
<header><h1>{esc(title)}</h1><p>Started {day} at {esc(rows[0]["start"][11:16])} UTC. Taken from the agents' own records; the lead session isn't counted.</p></header>
<div class="tiles">{tiles}</div>
{warning}
<section><h2>Timeline</h2><p>Each bar is one agent, grouped by phase. Hover a bar for details.{gap_note}</p>
<div class="scroll">{chart}</div>
<div class="key"><span><i style="background:var(--opus)"></i>Opus</span><span><i style="background:var(--haiku)"></i>Haiku</span><span><i style="background:var(--sonnet)"></i>Sonnet</span><span><i style="background:var(--other)"></i>Other</span></div></section>
{packages}
<section><h2>Every agent</h2><div class="scroll"><table><thead><tr><th>Agent</th><th>Phase</th><th>Model</th><th>Start (UTC)</th><th class="n">Minutes</th><th class="n">Tool calls</th><th class="n">Output tokens</th></tr></thead><tbody>{roster}</tbody></table></div></section>
</div>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session-dir", help="Session folder; defaults to the newest session for this directory")
    parser.add_argument("--session-id", help="Session id, when several sessions share this directory")
    parser.add_argument("--since", help="ISO time; ignore agents that started earlier")
    parser.add_argument("--results", help="JSON list of package results from the build loop")
    parser.add_argument("--title", default="Agent Ledger")
    parser.add_argument("--out", default=".factory/LEDGER.html")
    parser.add_argument("--json", help="Also write the rows as JSON")
    parser.add_argument("--size-file", help="SIZE.md with lines like 'S: reason'")
    parser.add_argument("--base", help="Base ref, used with --feature and --since to time the first commit")
    parser.add_argument("--feature", help="Feature branch")
    args = parser.parse_args()

    session = find_session(args)
    rows = collect(session, args.since)
    if not rows:
        sys.exit(f"No agents found in {session}" + (f" since {args.since}" if args.since else ""))
    results = None
    if args.results and os.path.exists(args.results):
        results = json.load(open(args.results))
    sizes = read_sizes(args.size_file)
    first_commit = first_commit_minutes(args.base, args.feature, args.since)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as handle:
        handle.write(render(rows, results, args.title, sizes, first_commit))
    if args.json:
        with open(args.json, "w") as handle:
            json.dump({"size": sizes, "first_commit_minutes": first_commit, "agents": rows, "packages": results}, handle, indent=1)
    print(f"{len(rows)} agents -> {args.out}")


if __name__ == "__main__":
    main()
