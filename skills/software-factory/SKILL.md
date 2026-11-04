---
name: software-factory
description: Use when the user asks for the software factory, to token-max, or to throw a team of agents at a feature, bug fix, or greenfield build where speed matters more than token cost.
---

# Software Factory

An autonomous train: spend tokens to compress calendar time. Good research makes cheap agents reliable, so the plan quality decides the output.

You are the **lead**. You dispatch, pass context between teams, and verify. Product code is written by agents. Invoking this skill is the user's opt-in to Workflow at this scale. These are defaults, not strict rules, so adapt them to the repo.

| Team | Model | Count | Output |
|---|---|---|---|
| Research | `opus` | 5-20 | `SPEC.md` |
| Implement | `haiku` | one per package and fix attempt, ideally 100+ over the run | commits in lanes |
| Test | `sonnet` (`opus` when depth beats turnaround) | paired 1:1 with implementers | severity-rated bug reports |
| Merge + accept | `opus` to merge and judge, `haiku` to fix | 1 merger per wave, 2-5 testers | a feature branch and a PR for developer review |

**Set `model` on every `agent()` call.** Omitted means it inherits the session's model, so builders can silently run on Opus. `model` in `meta.phases` is only a display label. Use `Workflow` for loops and fan-out, and load `workflow-authoring` before writing the script. If `Workflow` isn't available, run the same phases with background `Agent` calls and set `model` on each.

## 1. Research (Opus, parallel)

**Before dispatching anything:** add `.factory/` to `.git/info/exclude`, then save the user's original request to `.factory/BRIEF.md` word for word, without summarizing or tidying it. Append their later messages about scope or requirements as they arrive. Keep it out of commits, because it may contain anything they said aloud.

Dispatch one agent per angle, with more for large tasks. Every researcher reads `BRIEF.md` by absolute path. Each writes its findings to `.factory/research/<angle>.md` and returns a summary.

- **Code:** existing patterns, tools, and the files this touches
- **Spec:** requirements, gaps, acceptance criteria
- **Web:** libraries, docs, known pitfalls
- **Security:** vulnerabilities in the proposed solution
- **Architect:** scalability, only as far as this task needs it
- **Environment:** capacity and lane setup (below)

An architect then writes `.factory/SPEC.md` from the brief and the findings. 1-2 critics attack it for safety and buildability, and check that everything in the brief is either covered or explicitly out of scope. Revise once. The spec must contain:

1. Decisions, reusing the repo's existing stack and patterns; anything new needs a stated reason
2. Foundation: shared contracts, schema, and types, built first
3. Work packages small enough for Haiku: exact files owned, no file in two parallel packages, dependency waves, and done-checks
4. Lane plan: count, ports, databases, mocks
5. Secrets the user must provide; ask for them now, then keep running

## 2. Lanes

A lane is one git worktree with its own env, port, and database. Each pair shares a lane so the tester sees the implementer's work. Use `isolation: 'worktree'` only for code-only agents that don't run the app.

**Measure capacity:** RAM and CPU (`sysctl hw.memsize hw.ncpu` and `vm_stat` on macOS, `free -h` and `nproc` on Linux), then `df -h` and `docker info`. Is the repo containerized? Start one lane and measure its real RAM.

`lanes = min(available RAM / measured lane RAM, free disk / lane build cache)`

The factory sets no limit on agents. Run as many at once as the lanes support. If one `Workflow` run queues agents while lanes sit idle, split the lanes across several concurrent runs or background `Agent` calls. Reach large counts by splitting along real seams and by fix cycles. A small task uses fewer agents; never pad the count.

**Reduce per-lane cost:**
- Start the minimum infrastructure the feature touches and mock the rest.
- Use one Postgres with one database per lane (`CREATE DATABASE lane1 TEMPLATE base`), not one container per lane. The template needs zero open connections; raise `max_connections` if lanes exhaust it.
- If the app can't run in many copies, lanes run typecheck and unit tests, and one shared integration lane runs the app.

**Dependencies stay in one location:**
- Turbopack refuses a symlinked `node_modules`, so `lane.sh` makes a copy-on-write clone: APFS on macOS, reflink on Linux filesystems that support it, and a full copy otherwise. Clones share disk blocks. `du` overstates them, so check `df`.
- Symlink instead for toolchains that accept it, after confirming one lane builds.
- If `package.json` diverges, install in that lane only.

**Git Flow:** run `git fetch origin`, then set the branch names, following any conventions the repo already uses.

| Work | `$FEATURE` | `$BASE` |
|---|---|---|
| Feature or greenfield | `feature/<name>` | `origin/develop`, or the default branch if there is no `develop` |
| Bug fix | `bugfix/<name>` | same as above |
| Production hotfix | `hotfix/<name>` | `origin/main` |

Lane 0 is the integration lane, on `$FEATURE`, so the user's checkout stays untouched. Other lanes branch from it after the foundation lands.

```bash
# FEATURE=feature/x BASE=origin/develop bash lane.sh <n>: run from the repo root; safe to re-run
set -euo pipefail
: "${FEATURE:?set FEATURE}" "${BASE:?set BASE}"
N=$1; R="$PWD"; W="$(dirname "$R")/$(basename "$R")-lanes/lane$N"; PORT=$((3200 + N*10))
if [ "$N" = 0 ]; then B=$FEATURE; FROM=$BASE; else B=factory/lane$N; FROM=$FEATURE; fi
mkdir -p .factory "$(dirname "$W")"
grep -qs "^lane$N	" .factory/manifest || printf 'lane%s\t%s\t%s\t%s\n' "$N" "$W" "$PORT" "$B" >> .factory/manifest
[ -d "$W" ] || git worktree add -q -b "$B" "$W" "$FROM"
if [ -d node_modules ] && [ ! -d "$W/node_modules" ]; then
  cp -cR node_modules "$W/node_modules" 2>/dev/null || { rm -rf "$W/node_modules"; cp -R --reflink=auto node_modules "$W/node_modules" 2>/dev/null; } || { rm -rf "$W/node_modules"; cp -R node_modules "$W/node_modules"; }
fi
for f in .env .env.local; do if [ -f "$f" ]; then cp "$f" "$W/"; fi; done
```

After creating a lane, run the project's codegen in it (such as `prisma generate`).

**Lane env:** Pass `PORT` and `DATABASE_URL` as real environment variables on every command, such as `DATABASE_URL=... npx prisma migrate deploy` and `npx next dev -p $PORT`. Next.js ignores `PORT` in env files, and the Prisma CLI reads `.env`, not `.env.local`, so a file override alone can hit the shared database.

**Env safety:** lanes use local or sandbox services only. If a copied env points at production, replace that value with a local one or a mock. Agents never print env values.

## 3. Build and test loop

Run the foundation package through the loop below in lane 0, then migrate the `base` database and create the other lanes. Then run each wave: lanes pull packages from a shared queue, so two packages never share a lane at once.

```js
const MAX = 5  // 3, 5, or 10 by task size
const blocking = r => r.bugs.some(b => ['critical', 'high'].includes(b.severity))
const runPackage = async (p, lane) => {
  let report = null, cycle = 0
  while (cycle < MAX) {
    cycle++
    const built = await agent(buildPrompt(p, lane, report), { model: 'haiku', label: `${p.id} build ${cycle}`, phase: 'Build' })
    if (built === null) return { id: p.id, cycle, status: 'unverified' }
    report = await agent(testPrompt(p, lane), { model: 'sonnet', schema: BUGS, label: `${p.id} test ${cycle}`, phase: 'Test' })
    if (!report) return { id: p.id, cycle, status: 'unverified' }
    if (!blocking(report)) return { id: p.id, cycle, status: 'clean', report }
  }
  return { id: p.id, cycle, status: 'blocked', report }
}
const results = []
for (const wave of waves) {
  const queue = [...wave]
  await parallel(lanes.map(lane => async () => {
    while (queue.length) results.push(await runPackage(queue.shift(), lane))
  }))
  await agent(mergePrompt(wave), { model: 'opus', label: 'merge wave', phase: 'Merge' })  // section 4
}
```

- **Implementer prompt:** absolute paths to `SPEC.md` and the lane, the package text, owned files, the done-check, the tester's last report, and "write tests for your package; commit in your lane; edit only your files."
- **Tester prompt:** absolute lane path, port, acceptance criteria, and "restart the app on the latest commit, run the tests and drive the feature, reproduce each bug twice, cite evidence, do not edit code."
- **`BUGS` schema:** `{bugs: [{title, severity: critical|high|medium|low, repro, expected, actual, evidence, files}]}`
- **Severity:** Haiku fixes critical and high. Log medium and low to `.factory/BUGS.md` without fixing them.
- **Exit:** no critical or high bugs, or max cycles, whichever comes first. `unverified` and `blocked` packages go in the final report and never count as clean.
- **Outages:** `agent()` returns `null` when an agent dies. Run a follow-up workflow over the `unverified` packages; work already on disk survives.

## 4. Merge and accept

**After each wave:** one Opus merger works in lane 0. It merges lane branches into `$FEATURE` one at a time, resolves conflicts against the spec, and runs the build and tests after each merge. It then merges `$FEATURE` back into every lane so the next wave has the code.

**After the last wave:** Opus testers run acceptance against `SPEC.md` and `BRIEF.md` in lane 0, with Haiku fixing under the same loop and exit rule.

## 5. Open the pull request

The factory's deliverable is a PR a developer can review. Invoking this skill authorizes pushing `$FEATURE` and opening that PR. A developer merges it; the factory never does.

1. Review `git diff $BASE...$FEATURE`. Confirm it has no env files, secrets, or stray artifacts.
2. Push `$FEATURE` and open a PR into the Git Flow base with the repo's tool (`gh pr create`, `glab mr create`).
3. Mark it ready for review when every package is `clean`. Open it as a draft if anything is `blocked` or `unverified`.

The PR description must stand alone, because `.factory/` isn't committed. Include what changed and why, key decisions from the spec, how it was tested with evidence, open medium and low bugs, anything `blocked` or `unverified`, and secrets or env vars to provision.

If the push or PR fails, keep the branch and put the exact error in the report.

## 6. Clean up and report

Run cleanup even after a failed run, using only what `.factory/manifest` lists:

1. Stop lane servers and drop lane databases and containers.
2. For lanes 1 and up: `git worktree remove <path>`, then `git -C <lane0 path> branch -d factory/lane<n>`. If either refuses, inspect it, keep it, and report it.
3. Remove lane 0 last, keeping the `$FEATURE` branch.
4. Confirm with `git worktree list` and `df -h`.

Leave pre-existing worktrees alone.

**Report:** the PR link, what was built, agents by model, cycles, verification evidence, open medium and low bugs, anything `blocked` or `unverified`, and secrets still needed.