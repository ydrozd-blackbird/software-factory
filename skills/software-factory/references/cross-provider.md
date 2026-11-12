# Cross review

Read this page only when `run.json` has `review: "cross"`. With `single`, ignore it.

## When

Use it only when the user asks for a mixed or cross review, and `run.json` has `review: "cross"`. Then the other provider reviews. In Claude Code that is Codex. If this skill runs in Codex, Claude reviews. You run both gates in your own session, not in a workflow.

## What goes to OpenAI

Codex spends another account's limits and sends data to another company. You build a bundle file for each gate. The Spec bundle is `SPEC.md` and `packages.json`. It has no `BRIEF.md` unless the user agrees. The Final bundle is the diff file, the last 60 lines of the test output, and the open findings by id. Codex can also read the repo, because the sandbox limits writes, not reads. When cross is on, your first reply lists the exact contents of both bundles and says this.

Before each gate, on either transport, run the same env-file and secret check as section 7 step 2, on the repo and on the lanes. A lane `.secrets.env` or a tracked env file stops cross review for that gate until the user confirms. Then, before every send (the initial round and the follow-up), read the exact bundle you will send, the test-output tail included. A test can print a token, a connection string or a customer record. Cut such lines from the bundle, or stop cross review for that gate until the user confirms.

## Detection

1. A review bridge MCP tool that the user's `CLAUDE.md` names.
2. Else `command -v codex` and `codex --version`.

If neither works, use `single` and say why in your first reply. In `single`, detect nothing.

## Gates

| Gate | When | Input |
|---|---|---|
| Spec | Alongside the critics. Only later waves change. | `.factory/tmp/spec-initial.md`: `SPEC.md` then `packages.json`, a snapshot of the architect's draft. The helper copies it to `.factory/tmp/spec-<sha256>.md`. |
| Final | After the acceptance fixes and after the env-file and secret check in section 7. Before the push. | `.factory/tmp/final.diff`, full SHAs from `git rev-parse` and `git merge-base $BASE HEAD`, the last 60 lines of the test output, the open findings by id |

- The Spec gate is advisory. Its findings go to `BUGS.md`, and the architect revises once. It never blocks ready.
- The Final gate decides. The last Final round must be a Codex review on the current head with no critical or high finding that you did not reject.
- Each gate gets one follow-up. Run it only after you confirmed a critical or high finding and a fix landed. Give it the fix diff and the open finding ids. For Spec, the fix diff is `diff -u` of the two snapshots. The Final follow-up's base is the head that the initial `ok` row or the `fallback` row recorded. Any other `--expect-base` is exit 2 `wrong-base`.
- A follow-up starts only when the initial round is finished: an `ok` initial row, or a `fallback` row for that subject in this run. An `attempt` row alone does not count.
- Use one transport per gate. If the initial round used the CLI helper, the follow-up uses the helper too. If it used a bridge, the follow-up uses the bridge. Before you start any round, check both records: `codex-runs.jsonl` for the helper and `run.json` `reviews` for a bridge. If the evidence of a gate cannot be checked, the gate stays a draft. Before a follow-up, check that the `transport` and the `tool` name in `run.json` `reviews` for the initial round are the same as for the follow-up. If they differ, the gate stays a draft.
- Every finding has an `id` (F1, F2 and so on), made of letters, digits and `_.:-`, 40 characters at most, so `--open-ids` can name it. Findings keep their ids across rounds. The follow-up prompt lists the open ids. The follow-up report answers each one in `answers`: `{id, status: fixed|open|rejected, evidence}`. An id it does not answer stays open. An initial report has `answers` as an empty array. The reviewer leaves `verdict` null on every finding; you set verdicts in `BUGS.md`.
- On size S, only the Final gate runs.
- Ready needs lane 0's head to equal the head of the last Final round.

## Verdicts and status

- Set `confirmed`, `rejected` or `unverified` on every finding in `BUGS.md`. Keep the original text.
- Gate status is one of five values. `clean`: the initial round is `ok` with no open critical or high finding. `fixed`: as `clean`, after a fix and an `ok` follow-up that answers every open critical or high id as `fixed` or `rejected`, and you agree. `fallback`: Opus `high` reviewed it. If the initial round was a fallback and the Codex follow-up is `ok` on the current head, the status is `fixed` when the follow-up answers every open critical or high id as `fixed` or `rejected` and you agree; else it stays `fallback`. `unverified`: no usable review, or lane 0's head moved after the last round. `blocked`: the follow-up still has an open critical or high finding.
- A bridge review counts as cross only when the bridge tool's own metadata names the other provider. The report does not count, because a reviewer fills `provider` itself. Else it is a `fallback`.
- For the CLI, take provider and model from the helper's log row. Never take them from the reviewer's JSON.
- A `fallback` last round keeps the PR a draft. So do `unverified` and `blocked`.
- There is no third round. Codex is never the only final authority: you set the verdicts and you decide draft or ready.

## The call

```text
python3 <skill base directory>/scripts/codex_task.py --cd DIR --prompt-file F --out F --factory REPO/.factory --label TEXT --schema F
    --subject spec|final --round initial|followup
    [--spec-file F | --expect-head SHA --base-ref REF [--expect-base SHA]]
    [--open-ids F1,F3]
    [--model gpt-6.1-sol] [--effort high] [--timeout 540]
python3 <skill base directory>/scripts/codex_task.py --record-fallback --factory REPO/.factory
    --subject spec|final --head SHA_OR_HASH [--base SHA]
```

- Spec: the helper copies the bundle to `.factory/tmp/spec-<sha256>.md`. It also appends the exact bundle bytes to the prompt, under a fixed heading, so the reviewed input is the hashed input. A report with a `base` that is not null is exit 9. The report's `reviewed_head` must equal the sha256.
- Final initial: `--base-ref REF` is required, for example `origin/main`. The helper runs `git merge-base REF HEAD` and uses the result as the base. `--expect-base`, if you give it, must equal that base, else exit 2 `wrong-base`.
- Final follow-up: the base is the head of the initial `ok` row or `fallback` row. See the Gates section.
- A failed `git diff`, `git merge-base` or `git rev-parse` call is exit 2. It is never a pass.
- `--open-ids F1,F3` is required on a follow-up. It may be empty only when no id is open. An initial round refuses it. The follow-up report must answer exactly the listed ids: one answer per id, no repeat, no extra id. Else it is exit 7 `bad-output`, and the round is not spent.
- `--record-fallback` launches nothing. Call it after an Opus fallback review of an initial round. It adds one `fallback` row for the subject, and that row lets the follow-up start. It takes the subject lock like a review round. A held lock is exit 8 `busy`: wait, then reread both records. Under the lock it reads the log again. It refuses with exit 2 `initial-already-ok` when the subject has an `ok` initial row. The same fallback again (same subject, head and base) is exit 0 and adds no row. A fallback with another head or base is exit 2 `conflicting-fallback`.

Spec:

```text
python3 codex_task.py --cd REPO --factory REPO/.factory --subject spec --round initial --spec-file REPO/.factory/tmp/spec-initial.md --prompt-file REPO/.factory/tmp/spec-prompt.md --label "spec initial" --schema <skill base directory>/references/bugs.schema.json --out REPO/.factory/tmp/codex-spec-initial-1.json
```

Final:

```text
python3 codex_task.py --cd LANE0 --factory REPO/.factory --subject final --round initial --expect-head HEAD_SHA --base-ref origin/main --prompt-file REPO/.factory/tmp/final-prompt.md --label "final initial" --schema <skill base directory>/references/bugs.schema.json --out REPO/.factory/tmp/codex-final-initial-1.json
```

Run it as a background command and wait with `Monitor`. For a large diff, set a bigger `--timeout`.

| Exit | Status | Meaning |
|---|---|---|
| 0 | ok | Valid output. |
| 1 | crash | The helper failed. |
| 2 | usage | Bad call, empty diff, a failed git call, or a base or head that does not fit (`wrong-base`). A Codex `--round initial` for a subject that already has a `fallback` row in this run is exit 2 `initial-after-fallback`: the next round is the follow-up. The round reads the log again under the lock, so a fallback that appears just before the lock stops it too. `--record-fallback` also gives exit 2 `initial-already-ok` and `conflicting-fallback`. A follow-up with no `ok` initial row and no `fallback` row is also exit 2, even when an `attempt` row exists. Your bug: fix the call. |
| 3 | missing | Codex not found. |
| 4 | usage-limit | Codex hit a usage or rate limit, also when it only reports it in an `error` event. |
| 5 | timeout | Killed after `--timeout`. |
| 6 | error | Any other Codex failure. An `error` or `turn.failed` event in the JSON stream counts, even when Codex exits 0. So does a helper that cannot record the Codex child in the lock: it stops Codex first. |
| 7 | bad-output | Free text, a missing key, an empty `scope.checked`, a finding with no `id` or an id that `--open-ids` would refuse, a finding with a `verdict` that is not null, an initial report with non-empty `answers`, a follow-up with no `answers`, `answers` that do not match `--open-ids`, or an id that is in `bugs` and also answered `fixed` or `rejected`. |
| 8 | budget-spent, attempts-spent or busy | Stdout names one. `budget-spent`: an `ok` row exists. `attempts-spent`: two attempts ran and none was `ok`. `busy`: a live lock holds the subject, so another review of this gate is running. `--record-fallback` also gives `busy` when the lock is held. |
| 9 | wrong-target | `target_ok` is false, or the head, base or Spec hash is not the expected one. |

Stdout depends on the exit:

- A launched round prints one JSON line with `exit`, `status`, `out`, `task_id` and `tokens`.
- Exit 8 prints one JSON line with `exit`, `status`, `out`, `prior_out`, `prior_head`, `prior_base` and `reusable`. `reusable` is true only when the prior `ok` row is for the same subject and round, on the same head and base, its out file still has the logged sha256, and the report passes every check a new report gets, with the current `--open-ids`. So a follow-up report that does not answer a newly open id is never reusable.
- Exit 2 prints nothing on stdout. It prints one `codex_task: usage <reason>` line on stderr.
- `--record-fallback` prints its own one-line JSON with `exit`, `status`, `subject`, `head` and `base`, also on `busy`. If it removes a dead lock, it adds a `stale-lock` row first.
- Exit 6 prints `codex_task: error <auth|network|model|other>`, never raw text.

The helper appends rows to `.factory/codex-runs.jsonl`. Before each launch it writes an `attempt` row (`start`, `subject`, `round`). A launch that ends adds one result row, for every exit except 2 and 8. A helper crash (exit 1 `crash`) leaves only its `attempt` row. A signal adds a `cancelled` row (exit 1). A dead lock that it removes adds a `stale-lock` row. `--record-fallback` adds one `fallback` row (`subject`, `round` initial, `head`, `base`, `status`). A result row has `start`, `end`, `provider`, `model`, `effort`, `label`, `phase`, `subject`, `round`, `head`, `base`, `out`, `out_sha256`, `cd`, `exit`, `status`, `task_id`, the token counters and `hint`. `out_sha256` is the sha256 of the out file, on `ok` rows. The file holds no prompt or output text. The label is a gate name such as `final followup`.

Budget rules:

- One `ok` row per subject and round.
- At most two launched attempts per subject and round. A third is exit 8 `attempts-spent`.
- The budget counts only rows that started at or after `started` in `run.json`. Rows left by an earlier run in the same repo do not count. With no `run.json` or no `started`, every row counts.
- One lock guards each gate subject, not each round: `.factory/tmp/budget/<sha1 of the subject>.lock`. While the initial round runs, a follow-up of that subject gets exit 8 `busy`. Only the owner removes the lock. SIGTERM kills the Codex group and frees the lock.
- The owner holds an OS file lock (`flock`) on the lock file from before the file appears until the owner exits. While it is held, the lock is never stale, even when the owner was paused for a long time, for example when the laptop slept. When the owner dies, the OS drops it. Then the lock is stale if its pid and Codex group are gone on this host. The age rule (timeout + 120 s) applies only where `flock` cannot tell, such as a lock from another host. Keep `.factory/` on a local disk.
- The three exit 8 statuses: `budget-spent` (an `ok` row exists), `attempts-spent` (two attempts ran and none was `ok`), `busy` (a live lock; another review of this gate is running: wait for it and reread both records). Only one of them is named.
- Exits 1, 5, 6 and 7 retry once, with a fresh `--out`. An existing `--out` is exit 2.

## Reviewer prompt

Put these lines in every prompt:

- Do not open `.env*`, `.secrets.env`, `~/.claude/`, or `~/.codex/`. Never quote a secret or env value.
- Final: check that `git rev-parse HEAD` equals the head. If not, set `target_ok` to false and return no bugs.
- Final: review `git diff <base>...<head>` yourself. The diff file is only a copy. Return `base` and `reviewed_head`.
- Spec: read only the `spec-<sha256>.md` file the prompt names. Return `base` as null and `reviewed_head` as that sha256. Else the helper exits 9.
- List what you checked in `scope.checked` and what you did not in `scope.unchecked`.
- Each finding needs an `id` (such as F1), a severity, a failing scenario and evidence. Set `verdict` to null on every finding.
- Initial: return `answers` as `[]`. Follow-up: the prompt lists the open ids. Answer each one in `answers` with a status and evidence.

## Fallback

The run never fails because of Codex.

- Exits 3 and 4: run Opus `high` now. Label it `(fallback, same-provider)`.
- Exits 1, 5, 6 and 7: retry once, then fall back.
- Exit 9: do not retry. Check your SHAs, then fall back.
- Exit 2: your bug. Fix the call.
- Exit 8 `busy`: another review of this gate is running. Wait for it and reread `codex-runs.jsonl` and `run.json` `reviews`. Never start a fallback on `busy`.
- Exit 8 `budget-spent`: reuse the earlier report only when `reusable` is true. If `reusable` is false, fall back.
- Exit 8 `attempts-spent`: fall back.
- Only `budget-spent` with `reusable` false, `attempts-spent`, or a provider failure (exits 1, 3, 4, 5, 6, 7 or 9) leads to a fallback.
- If the initial round was a fallback, call `--record-fallback` with the head the Opus review covered. Then the follow-up may be Codex. An `attempt` row alone does not allow it. For Final, the follow-up base is the head in the `fallback` row. A Codex `ok` on the current head resolves the fallback to `fixed` when it answers every open critical or high id as `fixed` or `rejected` and you agree; else the status stays `fallback`. After an `ok` follow-up the helper refuses any later round of that subject with exit 8 `budget-spent`. There is never a third round. The next gate tries Codex again.

## Environment

The child gets a short allow-list of variables, not your whole environment. It has `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_ALL`, `TERM`, `TMPDIR` and `CODEX_HOME`. When set, it also gets `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `CODEX_API_KEY`, and the proxy and CA variables.

- A ChatGPT login works through `HOME`.
- An `OPENAI_API_KEY` reaches Codex, as when you run it yourself. Proxy users work too.
- `ANTHROPIC_API_KEY`, `DATABASE_URL` and all other variables never pass.

## Sandbox

The sandbox limits writes. A review run writes nothing. The sandbox does not limit reads. Codex can read any file you can read, and it sends what it reads to OpenAI. Not opening secret files is only a rule in the prompt. Nothing stops Codex from breaking it.

So refuse cross for a gate while a repo or lane `.env*` or `.secrets.env` file holds real values, or while an env file is tracked, unless the user confirms. Check this before each gate, on the CLI and on a bridge.

## Bridge

A bridge review has the same budget and the same statuses. The helper does not log it, so you record each round in `run.json` `reviews`: the provider (from the bridge tool's own metadata), the target (`head` and `base`), the status and the `task_id`. Before you start any round, read both records, the helper log and `reviews`. A gate that began on a bridge continues on the bridge. A gate that began on the CLI helper continues on the helper. Each round in `reviews` records `transport` (`cli` or `bridge`) and `tool` (the bridge tool name, null for the CLI). Before a follow-up, check that both are the same as in the initial round. If they are not, the gate stays a draft. The ledger has no rows for bridge reviews.

A bridge round follows the same contract as the helper, done by hand:

1. Before you send it, run the same checks as the helper for its gate. A failed check means you do not send it.
   - Spec: take the sha256 of the exact bundle file. It is the round's `head`, and `base` is null. A follow-up needs a new bundle, so its sha256 must differ from the initial round's.
   - Final: full SHAs from `git rev-parse`, a base from `git merge-base $BASE HEAD` (for a follow-up, the initial round's head), and `git diff --quiet base head` must report a difference.
2. Write the round to `reviews` with status `started` before you launch it, and add its `task_id` as soon as the bridge returns one. A `started` row counts as one attempt. At most two attempts per round.
3. On a resume, a `started` row with a `task_id` means: ask the bridge for that task's result. Do not launch a new round. A `started` row with no `task_id` is a spent attempt.
4. Accept the report only when every check below passes, the same list the helper runs. A report that fails is `bad-output` (or `wrong-target` for the last two checks), as with the helper.
   - It is JSON that fits [the schema](bugs.schema.json), with a non-empty `scope.checked` and a Boolean `target_ok`.
   - Every finding has a unique id that `--open-ids` would accept, and its `verdict` is null.
   - Initial: `answers` is empty. Follow-up: exactly one answer per open id, no repeat, no extra id, and no id that is both listed in `bugs` and answered `fixed` or `rejected`.
   - `target_ok` is true.
   - Spec: `base` is null and `reviewed_head` is the bundle's sha256. Final: `reviewed_head` and `base` are the round's head and base.

`run.json` fields:

```json
{"review": "single|cross",
 "providers": {"codex": "cli|bridge:<tool>|missing"},
 "reviews": [{"gate": "spec|final", "round": "initial|followup", "transport": "cli|bridge", "tool": "", "provider": "", "model": "", "status": "", "head": "", "base": "", "task_id": "", "out": ""}]}
```

A usage limit is a gate status, not a run-wide value.

## Ledger

The ledger reads `.factory/codex-runs.jsonl` only when `run.json` has `review: "cross"`. With `single`, its HTML and JSON are as before and have no `provider` key. In cross mode, a cost computed from a price that the model page marks "unverified" shows "estimate, price unverified" next to it. The TL;DR cost line says so when any such row exists. Treat that cost as an estimate, not as a fact.

See [the schema](bugs.schema.json) and [the model comparison](../../../docs/model-comparison.md).
