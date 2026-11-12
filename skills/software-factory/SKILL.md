---
name: software-factory
description: Use when the user asks for the software factory, to token-max, or to throw a team of agents at a feature, bug fix, or greenfield build where speed matters more than token cost. Also use when the user says resume, continue, or where were we while `.factory/run.json` shows an unfinished run.
---

# Software Factory

You are the **lead**. You dispatch, pass context between teams, and verify. Product code is written by agents. These are defaults, not strict rules, so adapt them to the repo. This table is the full team for L work; section 2 scales it down.

| Team | Model and effort | Count | Output |
|---|---|---|---|
| Research | `sonnet` `medium` for the code, web, and environment angles; `opus` `medium` for spec, security, and architecture | one per open question, up to about 20 | `research/` |
| Spec | architect and critics: `opus` on M; `fable` on L. Architect `medium`, critics `high` | 1 architect, 1-2 critics | `SPEC.md` |
| Implement | the worker ladder (section 5): `haiku` first by default, `sonnet` when a package keeps failing | one per package and fix attempt, ideally 100+ over the run | commits on package branches |
| Test | `sonnet` `medium` | paired 1:1 with implementers | severity-rated reports with proof |
| Merge + accept | `opus` `medium` to merge, `opus` `high` to judge, `sonnet` `medium` to fix | 1 merger per wave, 2-5 testers | a feature branch and a PR for developer review |

**Set `model` and `effort` on every `agent()` call.** Omitted means they inherit the session's, so builders can silently run on Opus or Fable. Use `Workflow` for loops and fan-out, and load `workflow-authoring` before writing the script. If `Workflow` isn't available, run the same phases with background `Agent` calls and set `model` and `effort` on each. When a check fails, raise effort one level before moving up a model, and start the bigger model at a lower effort.

**Workers:** builders start on Haiku by default. If the user asks for Sonnet workers, such as "use sonnet workers", set `workers: "sonnet"` in `run.json` and in the build workflow's `args`; every build cycle then runs on Sonnet. A change mid-run applies from the next workflow. The `haiku` alias means Haiku 5.5 only on the Anthropic API with Claude Code 2.1.293 or later; on Bedrock, Google Cloud, Microsoft Foundry, or Claude Platform on AWS it still means Haiku 4.5, so suggest Sonnet workers there.

**Review:** by default one provider does every review (`review: "single"`), and the factory never calls Codex. If the user asks for a mixed or cross review, such as "cross review" or "review with codex", set `review: "cross"` in `run.json`. Then the other provider reviews the spec draft (the Spec gate) and the final diff (the Final gate), and does the one follow-up after a fix. In Claude Code the other provider is Codex, so Codex has the last review. If the skill runs in Codex, Claude is the reviewer. You still set a verdict on every finding and decide draft or ready. You run both gates in your own session, not in a workflow. The call, the limits, and the fallback are in [references/cross-provider.md](references/cross-provider.md).

**Every lane prompt starts with:** "`cd <lane>` and run `git rev-parse --show-toplevel`. If it doesn't print `<lane>`, stop and report it." Agents start in the user's checkout, so this keeps them out of it. **Every agent prompt ends with:** "Before each step, read `<repo>/.factory/STEER.md`. Its entries override the spec. Keep each shell command to one simple line. Put anything longer, or anything with nested quotes, `$(...)`, or JSON, in a script under `<repo>/.factory/tmp/` and run it with `bash <path>`. Send JSON request bodies from files with `curl -d @<file>`." Claude Code asks before running a command it can't parse, even in permissive modes, and that prompt stalls the agent until the user answers.

## Keep the user in the loop

**TL;DR:** at most six lines: the size and why; what's being built, on which branch; packages, waves, and lanes; what's running now and when it should finish; decisions waiting, each with its default; and "Reply with a change at any time." Post it in your first reply, after the spec, whenever a workflow returns, and whenever the user asks for status.

**Steering:** save every later message from the user to `BRIEF.md`, replacing any secret in it with `<secret: NAME>` and moving the value to `.secrets.env` (section 3). If it changes the work, add a numbered, timestamped entry to `.factory/STEER.md` with its scope (`spec`, `package <id>`, or `wave <n>`), and edit `SPEC.md` and `packages.json` to match. Tell the user when it takes effect. Queued packages and running agents pick it up from `STEER.md` at their next step. Don't stop the workflow: list packages already built against the old plan as `stale` in `STEER.md`, and rebuild them on their own branches in a follow-up workflow. On S, send corrections straight to the builder.

**Decisions:** when the factory needs the user, such as a tester's `decision` or a missing secret, ask at once with a safe default, record it in `STEER.md` as `pending`, and keep other packages running. Asking ends your turn, so first start a `Monitor` that wakes you in 30 minutes. If there's no answer by then, apply the default and record it. A default that changes the package reruns it. A default never waives a safety rule or a `CLAUDE.md` rule; it leaves that package and its dependents out and says so in the PR.

## 1. Start

1. If this isn't a git repo, run `git init` and make an empty first commit.
2. Add `.factory/` to `.git/info/exclude`.
3. If `.factory/run.json` exists and its `phase` isn't `complete`, this is a resume: go to section 10. Otherwise write `run.json` with `started` (from `date -u +%Y-%m-%dT%H:%M:%SZ`), `feature`, `base`, `workers` (`haiku` unless the user asked for Sonnet), `review` (`single` unless the user asked for a cross review), `sessions: ["${CLAUDE_SESSION_ID}"]`, and `phase: "start"`. The ledger uses `sessions` to find every agent in the run. With `cross`, check for Codex now. If it's missing, use `single` and say why in your first reply. If it's there, say in your first reply what each gate's bundle holds, that the spec and the diff go to OpenAI, and that Codex can also read the repo, because its sandbox limits writes, not reads.

**Pre-flight:** agents work in `<repo>-lanes/`, outside the folder the session opened in, so their edits there can prompt too. Check `.claude/settings.local.json` and `~/.claude/settings.json`. If the lanes folder isn't an allowed directory, ask the user once in your first reply to add this to `.claude/settings.local.json`, with real absolute paths and the repo's package manager. Never edit permission settings yourself. Start research while you wait, since it only reads the repo and writes to `.factory/`.

**Data outside the repo:** list every path outside the repo and the lanes folder that agents will read, such as attached files, a data export, or a database copy, and add each to `additionalDirectories` in the same request. Auto mode can still refuse agents access to data that looks like customer records, so don't point testers at it. Load a database copy into the `base` database during setup, so lanes clone it and testers only query their own lane's database. Copy other test files into `<repo>-lanes/data/`, and tell the user what you copied, since cleanup deletes it.

```json
{
  "permissions": {
    "additionalDirectories": ["/abs/path/<repo>-lanes"],
    "allow": ["Bash(bash /abs/path/<repo>/.factory/tmp/:*)", "Bash(git:*)", "Bash(npm:*)", "Bash(npx:*)"]
  }
}
```

Update `run.json` at every phase change (`start`, `research`, `build`, `accept`, `pr`, `cleanup`, `complete`) and whenever a workflow starts or returns: set `phase` and `updated`, and keep a `workflows` list of `{runId, scriptPath, state}`, plus `pr`, `pending` decisions, the lane count, and `services`: the `base` database and any containers the factory started.

**`.factory/` layout:** the lead writes `run.json`, `SIZE.md`, `BRIEF.md`, `STEER.md`, `BUGS.md`, `results.jsonl`, `SUMMARY.md`, and the ledger. The architect writes `SPEC.md` and `packages.json`, and researchers write `research/`. `lane.sh` writes `manifest`, agents put scratch output and evidence in `tmp/`, and with `cross` the review helper appends `codex-runs.jsonl`. Cleanup deletes `manifest` and `tmp/` and keeps the rest, including `codex-runs.jsonl`. A new run counts only the rows from its own `started` time. Secrets never go in `.factory/` (section 3).

**Git Flow:** run `git fetch origin` if there's a remote, then set the branch names, following any conventions the repo already uses.

| Work | `$FEATURE` | `$BASE` |
|---|---|---|
| Feature or greenfield | `feature/<name>` | `origin/develop`, or the default branch if there is no `develop` |
| Bug fix | `bugfix/<name>` | same as above |
| Production hotfix | `hotfix/<name>` | `origin/main`; note in the PR that it also needs merging to `develop` |

With no remote, use the local branch as `$BASE`.

## 2. Size the work

Speed is the goal, so run the smallest factory that fits. Before dispatching any agent, spend at most 3 minutes: run `git grep -l <term> $BASE` for the components the request names, and note any new schema, route, UI, or subsystem. A subsystem means a new service, an external integration, or a cross-cutting area such as auth, billing, or sync; a new page, form, model, or report inside the existing app isn't one. Read any files, examples, or data the user attached, because they count as spec. Ignore how long the request is. Count only existing files the change edits, not new ones. Live-data checks means testing against real third-party or production systems; using the user's sample files as test fixtures isn't one.

| Size | When | Research and spec | Build and test | Acceptance |
|---|---|---|---|---|
| **S** | Up to 5 existing files, no schema change, no live-data checks | None: no `BRIEF.md`, research, or `SPEC.md` | 1 Opus builder in lane 0, 1 Sonnet tester, 1 Opus critic, max 3 cycles | The tester and critic |
| **M** | Up to 15 existing files; schema changes and live-data checks are fine | One researcher per angle with open questions, up to 3; an Opus architect; 1 Opus critic. `SPEC.md` is 1,500 words at most | 1-2 lanes plus lane 0, worker and Sonnet tester pairs, `MAX = 3` | 1 Opus tester |
| **L** | More than 15 existing files, a new subsystem, or greenfield | Researchers for open questions, and the full spec from a Fable architect and Fable critics | Full loop | 2-5 Opus testers |

**Open questions:** list the questions that the request and its attached files don't answer. Anything the user's files or examples answer is closed. A question only the user can answer, such as a business rule, numbering scheme, or preference, goes to the user as a decision with a default (see Keep the user in the loop), not to research. Research covers only the open questions that remain; with two or fewer, dispatch no researchers, and the architect reads the files, answers them, and writes the spec. Put the list in your first reply.

The table is a starting point, not a checklist. If the work is clearly simpler than its row suggests, choose the smaller size and say why.

Write the size and one shareable reason to `.factory/SIZE.md`, as `S: <reason>` (such as `M: 9 existing files and one migration`), and state both in your first reply. The user can override the size with one word, but don't wait for an answer.

**On M and L,** save the user's original request to `.factory/BRIEF.md` word for word, without summarizing or tidying it. Append their later messages about scope or requirements. Never commit it.

**Only size up.** Any agent that finds a migration on S, a new subsystem, or more files than the size allows must report it. Append the new size to `SIZE.md`, save `BRIEF.md` if it doesn't exist yet, keep the work done so far, and run the phases that were skipped. Size down only if the user says to.

**Running S:**

1. Create lane 0 with `lane.sh` (section 4), with its own database if the app uses one. There's no Environment researcher on S, so the builder checks for other shared services and follows the env safety rules.
2. One Opus agent at `medium`, labeled `solo build`, gets the user's request word for word in its prompt. It reads the code the request names, builds the change with tests, and commits to `$FEATURE`.
3. In parallel and read-only, one Sonnet tester at `medium`, labeled `solo test`, and one Opus critic at `high`, labeled `solo critic`, each return the `BUGS` schema (section 5). Both get the request word for word. The critic reviews the diff against it, looking for unsafe behavior and unproven claims.
4. If there are blocking bugs, the same Opus agent, continued with `SendMessage` where available or restarted with the report, fixes them on `$FEATURE`, then both reviewers run again. Stop after 3 cycles and open a draft PR if anything is still blocking.
5. Log medium and low bugs to `.factory/BUGS.md`, then continue at section 7.

## 3. Research (parallel)

Dispatch one agent per angle below that has open questions from section 2, labeled `research <angle>`, and skip the rest. Code, Web, and Environment run on `sonnet`; Spec, Security, and Architect on `opus`; all at `medium`. The spec writer is labeled `architect` and critics `critic`, on `opus` for M and `fable` for L. Every researcher reads `BRIEF.md` and the user's files by absolute path. Each writes at most 800 words to `.factory/research/<angle>.md`: findings and decisions with sources, not a survey. It returns a summary.

- **Code:** existing patterns, tools, and the files this touches
- **Spec:** requirements, gaps, acceptance criteria
- **Web:** libraries, docs, known pitfalls
- **Security:** vulnerabilities in the proposed solution
- **Architect:** scalability, only as far as this task needs it
- **Environment:** capacity, shared services, and lane setup (section 4); on L, always run this one

An architect then writes `.factory/SPEC.md` from the brief and the findings. 1-2 critics attack it for safety and buildability, and check that everything in the brief is either covered or explicitly out of scope. Revise once. With `cross`, also run the Spec gate on a snapshot of the architect's draft. It runs alongside the critics and, like them, only changes later waves. The critic or architect applies the findings you confirm. On S there is no spec, so only the Final gate runs. On M, the one critic edits the spec directly, and there's no separate revision. Don't wait for the critics: once the architect's draft exists, create lanes 0 and 1 and start the foundation build (section 5). Critics run alongside it and only change later waves. The spec must contain:

1. Decisions, reusing the repo's existing stack and patterns; anything new needs a stated reason
2. Foundation: shared contracts, schema, and types, built first
3. Work packages small enough for the first rung of the worker ladder: id, exact files owned, no file in two parallel packages, dependency waves, `deps`, and done-checks. Also write them to `.factory/packages.json` as `{"waves": [[package, ...], ...]}`; the build workflow takes this, plus `workers`, as its `args`
4. Lane plan: count, ports, databases, mocks, and any other shared service each lane must not touch
5. Secrets needed, and the mock to use until they exist

**Secrets:** don't stop to ask or take them in chat. List each one in the spec. They go in `<repo>-lanes/.secrets.env` with mode 600, never in `.factory/`, and cleanup deletes the file. A `DB_URL` with a password goes there too. Agents load it with `set -a; . <file>; set +a` and never print it. Continue with mocks, and name what's missing in the PR.

## 4. Lanes

A lane is one git worktree with its own env, port, and database. Each pair shares a lane so the tester sees the implementer's work.

**Measure capacity (L only; S and M use the lane counts in section 2):** RAM and CPU (`sysctl hw.memsize hw.ncpu` and `vm_stat` on macOS, `free -h` and `nproc` on Linux), then `df -h` and `docker info`. Is the repo containerized? To measure a lane's RAM, run the app once from a temporary worktree on a spare port with its own database, then remove it. Never measure against the user's database.

`lanes = min(available RAM / measured lane RAM, free disk / lane build cache)`

The factory sets no limit on agents, and never pads the count. `Workflow` has its own per-run concurrency limit, so start enough concurrent runs or background `Agent` calls to keep every lane busy, with separate lanes and packages in each.

**Reduce per-lane cost:**
- Start the minimum infrastructure the feature touches and mock the rest.
- Use one Postgres with one database per lane (`CREATE DATABASE lane1 TEMPLATE base`), not one container per lane. The template needs zero open connections; raise `max_connections` if lanes exhaust it.
- If the app can't run in many copies, lanes run typecheck and unit tests, and one shared integration lane runs the app.

**Dependencies stay in one location:** Turbopack refuses a symlinked `node_modules`, so `lane.sh` makes a copy-on-write clone where the filesystem supports it. `du` overstates clones, so check `df`. If `package.json` diverges, install in that lane only.

Lane 0 is the integration lane, on `$FEATURE`, so the user's checkout stays untouched. Other lanes are detached worktrees that switch to a branch per package.

```bash
# FEATURE=feature/x BASE=origin/develop DB_URL='postgresql://u:p@localhost:5432/lane%N' bash lane.sh <n>
# Run from the repo root; safe to re-run. %N becomes the lane number. Omit DB_URL if there's no database.
set -euo pipefail
: "${FEATURE:?set FEATURE}" "${BASE:?set BASE}"
N=$1; R="$(pwd -P)"; W="$(dirname "$R")/$(basename "$R")-lanes/lane$N"; PORT=$((3200 + N*10))
DB="${DB_URL:-}"; DB="${DB//%N/$N}"
mkdir -p .factory "$(dirname "$W")"
grep -qs "^lane$N	" .factory/manifest || printf 'lane%s\t%s\t%s\t%s\n' "$N" "$W" "$PORT" "${DB:+lane$N}" >> .factory/manifest
if [ ! -d "$W" ]; then
  if [ "$N" != 0 ]; then git worktree add -q --detach "$W" "$FEATURE"
  elif git show-ref -q "refs/heads/$FEATURE"; then git worktree add -q "$W" "$FEATURE"
  else git worktree add -q --no-track -b "$FEATURE" "$W" "$BASE"; fi
fi
if [ -d node_modules ] && [ ! -d "$W/node_modules" ]; then
  cp -cR node_modules "$W/node_modules" 2>/dev/null || { rm -rf "$W/node_modules"; cp -R --reflink=auto node_modules "$W/node_modules" 2>/dev/null; } || { rm -rf "$W/node_modules"; cp -R node_modules "$W/node_modules"; }
fi
for f in .env .env.local; do
  if [ -f "$f" ]; then
    if [ -z "$DB" ] && grep -qE '^(export )?DATABASE_URL=' "$f"; then echo "$f sets DATABASE_URL: pass DB_URL so lanes don't share it" >&2; exit 1; fi
    { grep -vE '^(export )?(PORT|DATABASE_URL)=' "$f" || true; echo "PORT=$PORT"; [ -z "$DB" ] || echo "DATABASE_URL=$DB"; } > "$W/$f"
  fi
done
```

**Env safety:** lanes use local or sandbox services only. `lane.sh` rewrites `PORT` and `DATABASE_URL`. Set any other shared service the Environment researcher lists, such as Redis or queues, to a lane-local value or a mock, and do the same for anything pointing at production. Also pass `PORT` on the start command, such as `npx next dev -p $PORT`. Agents never print env values.

## 5. Build and test loop

Create lanes 0 and 1 and run the foundation as the first wave, alone in lane 1. After it merges, migrate the `base` database from lane 0 and create the other lanes. Each lane works through a shared queue, so two packages never share a lane at once. `waves` comes from `packages.json` through the workflow's `args`, along with `workers` from `run.json`.

**Worker ladder:** each build cycle is a fresh agent, so a package that keeps failing moves up a rung: Haiku, then Sonnet, then Sonnet at `high`. It skips Haiku at `high` because a wasted cycle costs more time than Sonnet costs tokens. Easy packages stay on the cheapest rung.

```js
const MAX = 5  // 3 on M; 5 or 10 on L
const LADDER = args.workers === 'sonnet'
  ? [['sonnet', 'medium'], ['sonnet', 'high']]
  : [['haiku', 'medium'], ['sonnet', 'medium'], ['sonnet', 'high']]
const rung = cycle => LADDER[Math.min(cycle, LADDER.length) - 1]
const proven = r => r.ran.app_started || r.ran.tests.passed + r.ran.tests.failed > 0
const severe = b => ['critical', 'high'].includes(b.severity)
const blocking = r => r.ran.tests.failed > 0 || r.bugs.some(severe)
const results = [], failed = new Set()
const runPackage = async (p, lane) => {
  const reports = []
  let cycle = 0
  const finish = status => {
    if (status !== 'clean') failed.add(p.id)
    return { id: p.id, cycle, status, reports }
  }
  if ((p.deps || []).some(d => failed.has(d))) return finish('blocked')
  while (cycle < MAX) {
    cycle++
    const [model, effort] = rung(cycle)
    const built = await agent(buildPrompt(p, lane, reports.at(-1)), { model, effort, label: `${p.id} build ${cycle}`, phase: 'Build' })
    if (built === null) return finish('unverified')
    const report = await agent(testPrompt(p, lane, reports.at(-1)), { model: 'sonnet', effort: 'medium', schema: BUGS, label: `${p.id} test ${cycle}`, phase: 'Test' })
    if (!report) return finish('unverified')
    reports.push(report)
    if (report.bugs.some(b => b.decision && severe(b))) return finish('needs-decision')  // other questions don't stop the package
    if (blocking(report)) continue
    return finish(proven(report) ? 'clean' : 'unverified')
  }
  return finish('blocked')
}
for (const wave of waves) {
  const queue = [...wave]
  await parallel(lanes.map(lane => async () => {
    while (queue.length) results.push(await runPackage(queue.shift(), lane))
  }))
  const clean = results.filter(r => r.status === 'clean' && wave.some(p => p.id === r.id))
  if (clean.length) await agent(mergePrompt(clean), { model: 'opus', effort: 'medium', label: 'merge wave', phase: 'Merge' })  // section 6
}
return results
```

When a workflow returns, append one line per result to `.factory/results.jsonl` with its `runId`. The latest line for a package wins, so re-runs never need new files. Write every medium and low bug from every report to `.factory/BUGS.md`.

**Package start.** On the first cycle, the implementer runs this, then resets the lane's own database and runs migrations and codegen:

```text
git add -u; git diff --cached --quiet || git commit -qm "wip: interrupted"
git switch -q factory/<package> 2>/dev/null || git switch -qc factory/<package> "$FEATURE"
```

- **Implementer prompt:** absolute paths to `SPEC.md` and the lane, the package text, owned files, the done-check, the tester's last report, and "write tests for your package; commit only your files on the package branch. Finish within 20 minutes. Never wait more than 10 minutes on one command: run it in the background with its output in `.factory/tmp/`, commit what you have, and say what's still running. Commit after each working step, since an outage loses anything uncommitted."
- **Tester prompt:** absolute lane path, port, acceptance criteria, and "restart the app on the latest commit, run the tests and drive the feature, reproduce each bug twice, cite evidence, do not edit code. Report every command you ran. If the app won't start or tests can't run, report that as a critical bug. Report within 5 minutes of the done-check passing, and drive the UI at most twice. If a blocking bug from the last report is still there, or fixing it needs a `CLAUDE.md` rule changed, a safety test removed, or work outside the package, set `decision` on that bug to a question for the user with a safe default. If a permission prompt or auto mode refuses a command, don't work around it: report a critical bug with the refused command as evidence and a `decision` asking the user to allow it."
- **`BUGS` schema:** `{ran: {app_started, commands: [], tests: {passed, failed, skipped}}, bugs: [{title, severity: critical|high|medium|low, repro, expected, actual, evidence, files, decision?}]}`
- **Severity:** the next build cycle fixes critical and high bugs and failing tests. Medium and low bugs are logged, not fixed.
- **Exit:** no blocking bugs with proof that testing ran, or max cycles, whichever comes first. A report with no proof is `unverified`. `unverified`, `needs-decision`, and `blocked` packages never count as clean or get merged. Packages depending on them are `blocked` too. A `decision` on a medium or low bug never stops a package: the package finishes on the default, and the lead adds the question to `STEER.md` as `pending` and asks the user. If the answer differs from the default, steering reruns that package.
- **Hung agents and outages:** `agent()` returns `null` only after the API gives up, which has taken 48-141 minutes in a network outage. While a workflow runs, check every 15 minutes with `Monitor`. Transcripts are in `~/.claude/projects/<cwd with each non-alphanumeric as ->/<session id>/subagents/workflows/<runId>/`: one `agent-*.jsonl` per agent, and `journal.jsonl` with one JSON line per agent start and result. An agent whose transcript hasn't changed in 15 minutes is hung, so `TaskStop` the workflow and relaunch it with `resumeFromRunId`. Finished agents come back from cache, and only running ones restart. The same check reads new test results in `journal.jsonl` and asks the user about any `decision` right away. Afterwards, run a follow-up workflow over `unverified` and answered `needs-decision` packages; each resumes on its own branch.

## 6. Merge and accept

**After each wave:** one Opus merger works in lane 0. It merges each `clean` package branch into `$FEATURE` one at a time, resolves conflicts against the spec, and runs the build and tests after each merge. New packages branch from the merged `$FEATURE`.

**After the last wave:** Opus testers at `high` run acceptance in lane 0 in parallel, read-only, against `SPEC.md` and `BRIEF.md`. One Sonnet fixer at `medium` at a time, since it edits the integrated branch, commits fixes to `$FEATURE` in lane 0, under the same exit rule, and testers retest after each round.

Integration and acceptance never run as one long agent. Split the checks into packages of about 20 minutes, run them in parallel across idle lanes, and run anything longer, such as load tests, as a background command that a fresh tester reads back.

## 7. Open the pull request

The factory's deliverable is a PR a developer can review. A developer merges it; the factory never does. Pushing `$FEATURE` and opening the PR need no further confirmation from the user.

1. Run the repo's full build and test suite yourself in lane 0.
2. Review `git diff $BASE...$FEATURE`. Confirm it has no env files, secrets, or stray artifacts.
3. With `cross`, run the secret check of step 2 on the repo and the lanes before each gate. A lane `.secrets.env` or a tracked env file stops cross review for that gate until the user confirms. Then run the Final gate on `git diff $BASE...$FEATURE`. Have the Sonnet fixer fix each critical or high finding you didn't reject, on `$FEATURE` in lane 0. Then run steps 1 and 2 again. Run the one follow-up only after a confirmed critical or high finding was fixed. It answers each open finding id, and it uses the same transport and the same bridge tool name as the initial round (see `run.json` `reviews`), else the gate stays a draft.
4. Run `git push -u origin $FEATURE` from lane 0 and open a PR into the Git Flow base with the repo's tool (`gh pr create`, `glab mr create`).
5. Mark it ready for review only when step 1 passed, every package is `clean`, and with `cross` the Final gate is `clean` or `fixed`, its last round was a Codex review, and lane 0's head is the head of that round. Otherwise open it as a draft.

The PR description must stand alone, because `.factory/` isn't committed. Include what changed and why, key decisions from the spec, the end of the build and test output, open medium and low bugs, anything `blocked` or `unverified`, and secrets or env vars to provision. With `cross`, name each gate's reviewer, model, revision, and status.

If there's no remote, or the push or PR fails, keep the branch and put the reason in the report.

## 8. Clean up

Run cleanup once the PR is open, or when the run stops for good, but not while it's paused for a decision or an outage. Touch only what `.factory/manifest` lists (lane, path, port, database) and the `services` in `run.json`:

1. Stop lane servers with `lsof -ti tcp:<port> | xargs kill`, then drop the lane databases, the `base` database, and the containers listed in `services`.
2. For lanes 1 and up, delete the lane's `.env` copies, then run `git worktree remove <path>`. If it refuses, inspect the worktree, keep it, and report it.
3. Run `git -C <lane 0 path> branch -d factory/<package>` for each package. Git refuses unmerged branches; keep those and report them.
4. Remove lane 0 the same way, last, keeping the `$FEATURE` branch.
5. Delete `<repo>-lanes/.secrets.env` and `<repo>-lanes/data/`, remove the `<repo>-lanes/` folder if it's empty, then delete `.factory/tmp/` and `.factory/manifest`.

## 9. Ledger and report

Generate the agent ledger, even after a failed run. The ledger is meant to be shared, so it opens with a TL;DR. First write `.factory/SUMMARY.md` for a reader outside the team: 2-4 plain sentences on what was built or fixed, for whom, and why it matters, then up to 5 `- ` bullets of the main changes in product terms, not file names. Say plainly if the run ended blocked or as a draft. Include nothing you wouldn't put in the PR description: no secrets, credentials, customer data, internal hostnames, or quotes from `BRIEF.md`. Keep each `SIZE.md` reason shareable too, since the TL;DR shows it. The script adds the size and its definition, the commits, files, and lines changed against `$BASE`, the areas of the code touched, the package outcomes, the PR link from `run.json`, the time, and the cost. The script is in this skill's base directory:

```bash
python3 <skill base directory>/scripts/ledger.py --factory .factory --title "<name> Agent Ledger"
```

It reads agents from the sessions in `run.json` and from any session holding the run's workflows, so run it from the lead session; it falls back to the newest session in the folder only with a warning. With `cross`, it also adds Codex rows from `.factory/codex-runs.jsonl`.

The ledger shows the size, the minutes from invocation to first product commit, and the estimated cost at API list prices by phase, package, and agent. Use it to judge whether Sonnet workers or Fable specs pay off on a repo. Targets are 15 on S and 30 on M. Both size limits and targets are starting guesses, so adjust them when a repo's ledgers show they're off.

If the `Artifact` tool is available, publish `.factory/LEDGER.html` as it is and share the link. It's private until the user shares it. Otherwise, give the file's path.

**Report:** the PR link, the ledger link, what was built, verification evidence, open medium and low bugs, anything `blocked`, `unverified`, or `needs-decision`, and secrets still needed. Then set `phase` to `complete` in `run.json`. A run is complete when the PR is open, or the branch is kept with a stated reason, and cleanup and the ledger are done.

## 10. Resume

Resume on `/blackbird:software-factory resume`, when the user says resume or continue, or whenever the skill starts and `run.json` shows a `phase` other than `complete`. The plugin's start-up hook tells you when a session opens in a repo with an unfinished run. Post a TL;DR of where the run stopped, then:

1. Read `run.json`, `results.jsonl`, and `STEER.md`, and add `${CLAUDE_SESSION_ID}` to `sessions` if it's missing. Run `git for-each-ref refs/heads/factory/` and `git log $BASE..$FEATURE` to see which packages are committed and merged.
2. Run `git worktree prune`, then recreate the lanes in `manifest` with `lane.sh`, taking `DB_URL` from `.secrets.env`. Work that wasn't committed in a lost lane is gone.
3. In the same session, relaunch the last workflow with `resumeFromRunId`. In a new session, start the build workflow with only the waves and packages that aren't `clean`, plus `workers`, as its `args`, leaving `packages.json` as it is. A package that already has a branch continues on it, because the package-start commands switch to the existing branch. With `cross`, `codex-runs.jsonl` keeps the review budget, so a resume never repeats a finished round.
4. Update `run.json` before dispatching anything.
