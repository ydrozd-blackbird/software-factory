#!/usr/bin/env python3
"""SessionStart hook: tell Claude about an unfinished software factory run in this repo.

Prints nothing unless .factory/run.json exists with a phase other than "complete".
It never fails the session: any error means silence.
"""
import json, os, sys


def find_run(start):
    folder = os.path.abspath(start)
    while True:
        path = os.path.join(folder, ".factory", "run.json")
        if os.path.isfile(path):
            return folder, path
        if os.path.isdir(os.path.join(folder, ".git")) or os.path.dirname(folder) == folder:
            return None, None
        folder = os.path.dirname(folder)


def package_counts(factory):
    total = 0
    try:
        waves = json.load(open(os.path.join(factory, "packages.json"))).get("waves", [])
        total = sum(len(wave) for wave in waves)
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    latest = {}
    try:
        for line in open(os.path.join(factory, "results.jsonl"), errors="ignore"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id"):
                latest[row["id"]] = row.get("status")
    except OSError:
        pass
    clean = sum(status == "clean" for status in latest.values())
    return clean, max(total, len(latest))


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    repo, path = find_run(event.get("cwd") or os.getcwd())
    if not path:
        return
    try:
        run = json.load(open(path))
    except (OSError, ValueError):
        run = {"phase": "unknown (run.json is unreadable)"}
    if not isinstance(run, dict) or run.get("phase") == "complete":
        return
    clean, total = package_counts(os.path.dirname(path))
    facts = [
        f"branch {run.get('feature') or 'unknown'}",
        f"stopped in phase {run.get('phase') or 'unknown'}",
        f"last updated {run.get('updated') or run.get('started') or 'unknown'}",
    ]
    if total:
        facts.append(f"{clean} of {total} packages clean")
    pending = run.get("pending") or []
    if pending:
        facts.append(f"{len(pending)} decision{'s' if len(pending) != 1 else ''} waiting for the user")
    if event.get("source") == "resume":
        how = "This is a resumed session, so its workflows can be relaunched with resumeFromRunId."
    else:
        how = (
            "If the run's original session still exists, `claude --continue` in this repo reopens it, "
            "so finished agents come back from cache. Otherwise, resuming here continues from the branches and results on disk."
        )
    message = (
        f"An unfinished software factory run exists in {repo}: " + ", ".join(facts) + ". "
        "In your first reply, tell the user in one or two lines and say they can reply 'resume' to continue it. "
        f"{how} If they say resume, use the blackbird:software-factory skill, section 10. Don't resume without being asked."
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": message}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
