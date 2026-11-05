---
name: software-factory
description: Use when the user asks for the software factory, to token-max, or to throw a team of agents at a feature, bug fix, or greenfield build where speed matters more than token cost.
---

# Software Factory

You are the **lead**. You dispatch, pass context between teams, and verify. Product code is written by agents. These are defaults, not strict rules, so adapt them to the repo. This table is the full team for L work; section 2 scales it down.

| Team | Model | Count | Output |
|---|---|---|---|
| Research | `opus` | 5-20 | `SPEC.md` |
| Implement | `haiku` | one per package and fix attempt, ideally 100+ over the run | commits on package branches |
| Test | `sonnet` (`opus` when depth beats turnaround) | paired 1:1 with implementers | severity-rated reports with proof |
| Merge + accept | `opus` to merge and judge, `haiku` to fix | 1 merger per wave, 2-5 testers | a feature branch and a PR for developer review |

**Set `model` on every `agent()` call.** Omitted means it inherits the session's model, so builders can silently run on Opus. Use `Workflow` for loops and fan-out, and load `workflow-authoring` before writing the script. If `Workflow` isn't available, run the same phases with background `Agent` calls and set `model` on each.

**Every lane prompt starts with:** "`cd <lane>` and run `git rev-parse --show-toplevel`. If it doesn't print `<lane>`, stop and report it." Agents start in the user's checkout, so this keeps them out of it.

## 1. Start

1. If this isn't a git repo, run `git init` and make an empty first commit.
2. Add `.factory/` to `.git/info/exclude`.
3. Run `date -u +%Y-%m-%dT%H:%M:%SZ > .factory/started-at`.

**Git Flow:** run `git fetch origin` if there's a remote, then set the branch names, following any conventions the repo already uses.

| Work | `$FEATURE` | `$BASE` |
|---|---|---|
| Feature or greenfield | `feature/<name>` | `origin/develop`, or the default branch if there is no `develop` |
| Bug fix | `bugfix/<name>` | same as above |
| Production hotfix | `hotfix/<name>` | `origin/main`; note in the PR that it also needs merging to `develop` |

With no remote, use the local branch as `$BASE`.

## 2. Size the work

Speed is the goal, so run the smallest factory that fits. Before dispatching any agent, spend at most 3 minutes: run `git grep -l <term> $BASE` for the components the request names, and note any new schema, route, UI, or subsystem. Ignore how long the request is. Count only existing files the change edits, not new ones. Live-data checks means testing against real third-party or production data.

| Size | When | Research and spec | Build and test | Acceptance |
|---|---|---|---|---|
| **S** | Up to 5 existing files, no schema change, no live-data checks | None: no `BRIEF.md`, research, or `SPEC.md` | 1 Opus builder in lane 0, 1 Sonnet tester, 1 Opus critic, max 3 cycles | The tester and critic |
| **M** | Up to 15 existing files; schema changes and live-data checks are fine | 2-3 researchers, an architect, 1 critic. `SPEC.md` is 1,500 words at most | 1-2 lanes plus lane 0, Haiku and Sonnet pairs, `MAX = 3` | 1 Opus tester |
| **L** | More than 15 existing files, a new subsystem, or greenfield | Full research team and spec | Full loop | 2-5 Opus testers |

Write the size and one reason to `.factory/SIZE.md`, as `S: <reason>`, and state both in your first reply. The user can override the size with one word, but don't wait for an answer.

**On M and L,** save the user's original request to `.factory/BRIEF.md` word for word, without summarizing or tidying it. Append their later messages about scope or requirements. Never commit it.

**Only size up.** Any agent that finds a migration on S, a new subsystem, or more files than the size allows must report it. Append the new size to `SIZE.md`, save `BRIEF.md` if it doesn't exist yet, keep the work done so far, and run the phases that were skipped. Size down only if the user says to.

**Running S:**

1. Create lane 0 with `lane.sh` (section 4), with its own database if the app uses one. There's no Environment researcher on S, so the builder checks for other shared services and follows the env safety rules.
2. One Opus agent, labeled `solo build`, gets the user's request word for word in its prompt. It reads the code the request names, builds the change with tests, and commits to `$FEATURE`.
3. In parallel and read-only, one Sonnet tester, labeled `solo test`, and one Opus critic, labeled `solo critic`, each return the `BUGS` schema (section 5). Both get the request word for word. The critic reviews the diff against it, looking for unsafe behavior and unproven claims.
4. If there are blocking bugs, the same Opus agent, continued with `SendMessage` where available or restarted with the report, fixes them on `$FEATURE`, then both reviewers run again. Stop after 3 cycles and open a draft PR if anything is still blocking.
5. Log medium and low bugs to `.factory/BUGS.md`, then continue at section 7.

## 3. Research (Opus, parallel)

Dispatch one agent per angle, with more for large tasks and only the angles the brief needs on M. Every researcher reads `BRIEF.md` by absolute path. Each writes its findings to `.factory/research/<angle>.md` and returns a summary.

- **Code:** existing patterns, tools, and the files this touches
- **Spec:** requirements, gaps, acceptance criteria
- **Web:** libraries, docs, known pitfalls
- **Security:** vulnerabilities in the proposed solution
- **Architect:** scalability, only as far as this task needs it
- **Environment:** capacity, shared services, and lane setup (section 4)

An architect then writes `.factory/SPEC.md` from the brief and the findings. 1-2 critics attack it for safety and buildability, and check that everything in the brief is either covered or explicitly out of scope. Revise once. On M, the one critic edits the spec directly, and there's no separate revision. The spec must contain:

1. Decisions, reusing the repo's existing stack and patterns; anything new needs a stated reason
2. Foundation: shared contracts, schema, and types, built first
3. Work packages small enough for Haiku: id, exact files owned, no file in two parallel packages, dependency waves, `deps`, and done-checks
4. Lane plan: count, ports, databases, mocks, and any other shared service each lane must not touch
5. Secrets needed, and the mock to use until they exist

**Secrets:** don't stop to ask or take them in chat. List each one with where it goes, such as `.factory/secrets.env`, continue with mocks, and name what's missing in the PR.

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

Create lanes 0 and 1 and run the foundation as the first wave, alone in lane 1. After it merges, migrate the `base` database from lane 0 and create the other lanes. Each lane works through a shared queue, so two packages never share a lane at once.

```js
const MAX = 5  // 3 on M; 5 or 10 on L
const proven = r => r.ran.app_started || r.ran.tests.passed + r.ran.tests.failed > 0
const blocking = r => r.ran.tests.failed > 0 || r.bugs.some(b => ['critical', 'high'].includes(b.severity))
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
    const built = await agent(buildPrompt(p, lane, reports.at(-1)), { model: 'haiku', label: `${p.id} build ${cycle}`, phase: 'Build' })
    if (built === null) return finish('unverified')
    const report = await agent(testPrompt(p, lane), { model: 'sonnet', schema: BUGS, label: `${p.id} test ${cycle}`, phase: 'Test' })
    if (!report) return finish('unverified')
    reports.push(report)
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
  if (clean.length) await agent(mergePrompt(clean), { model: 'opus', label: 'merge wave', phase: 'Merge' })  // section 6
}
return results
```

After each run, write the returned results to `.factory/results.json`, adding to any earlier runs. Write every medium and low bug from every report to `.factory/BUGS.md`.

**Package start.** On the first cycle, the implementer runs this, then resets the lane's own database and runs migrations and codegen:

```text
git add -u; git diff --cached --quiet || git commit -qm "wip: interrupted"
git switch -q factory/<package> 2>/dev/null || git switch -qc factory/<package> "$FEATURE"
```

- **Implementer prompt:** absolute paths to `SPEC.md` and the lane, the package text, owned files, the done-check, the tester's last report, and "write tests for your package; commit only your files on the package branch."
- **Tester prompt:** absolute lane path, port, acceptance criteria, and "restart the app on the latest commit, run the tests and drive the feature, reproduce each bug twice, cite evidence, do not edit code. Report every command you ran. If the app won't start or tests can't run, report that as a critical bug."
- **`BUGS` schema:** `{ran: {app_started, commands: [], tests: {passed, failed, skipped}}, bugs: [{title, severity: critical|high|medium|low, repro, expected, actual, evidence, files}]}`
- **Severity:** Haiku fixes critical and high bugs and failing tests. Medium and low bugs are logged, not fixed.
- **Exit:** no blocking bugs with proof that testing ran, or max cycles, whichever comes first. A report with no proof is `unverified`. `unverified` and `blocked` packages never count as clean or get merged. Packages depending on them are `blocked` too.
- **Outages:** `agent()` returns `null` when an agent dies. Run a follow-up workflow over the `unverified` packages; each resumes on its own branch.

## 6. Merge and accept

**After each wave:** one Opus merger works in lane 0. It merges each `clean` package branch into `$FEATURE` one at a time, resolves conflicts against the spec, and runs the build and tests after each merge. New packages branch from the merged `$FEATURE`.

**After the last wave:** Opus testers run acceptance in lane 0 in parallel, read-only, against `SPEC.md` and `BRIEF.md`. One Haiku fixer at a time commits fixes to `$FEATURE` in lane 0, under the same exit rule, and testers retest after each round.

## 7. Open the pull request

The factory's deliverable is a PR a developer can review. A developer merges it; the factory never does. Pushing `$FEATURE` and opening the PR need no further confirmation from the user.

1. Run the repo's full build and test suite yourself in lane 0.
2. Review `git diff $BASE...$FEATURE`. Confirm it has no env files, secrets, or stray artifacts.
3. Run `git push -u origin $FEATURE` from lane 0 and open a PR into the Git Flow base with the repo's tool (`gh pr create`, `glab mr create`).
4. Mark it ready for review only when step 1 passed and every package is `clean`. Otherwise open it as a draft.

The PR description must stand alone, because `.factory/` isn't committed. Include what changed and why, key decisions from the spec, the end of the build and test output, open medium and low bugs, anything `blocked` or `unverified`, and secrets or env vars to provision.

If there's no remote, or the push or PR fails, keep the branch and put the reason in the report.

## 8. Clean up

Run cleanup even after a failed run, touching only what `.factory/manifest` lists (lane, path, port, database):

1. Stop lane servers with `lsof -ti tcp:<port> | xargs kill`, then drop lane databases and any containers the factory started.
2. For lanes 1 and up, run `git worktree remove <path>`. If it refuses, inspect the worktree, keep it, and report it.
3. Run `git -C <lane 0 path> branch -d factory/<package>` for each package. Git refuses unmerged branches; keep those and report them.
4. Remove lane 0 last, keeping the `$FEATURE` branch.

## 9. Ledger and report

Generate the agent ledger, even after a failed run. The script is in this skill's base directory:

```bash
python3 <skill base directory>/scripts/ledger.py --since "$(cat .factory/started-at)" --results .factory/results.json --title "<name> Agent Ledger" --out .factory/LEDGER.html --json .factory/ledger.json --size-file .factory/SIZE.md --base "$BASE" --feature "$FEATURE"
```

The ledger shows the size and the minutes from invocation to first product commit. Targets are 15 on S and 30 on M. Both size limits and targets are starting guesses, so adjust them when a repo's ledgers show they're off.

If the `Artifact` tool is available, publish `.factory/LEDGER.html` as it is and share the link. It's private until the user shares it. Otherwise, give the file's path.

**Report:** the PR link, the ledger link, what was built, verification evidence, open medium and low bugs, anything `blocked` or `unverified`, and secrets still needed.
