---
name: schedule-poll-orchestrator-pattern
description: |
  Fire-ASAP orchestrator pattern for multi-track autonomous workflows dispatched
  via scheduled triggers (RemoteTrigger / CronCreate) rather than in-session
  agents. Use when: (1) a multi-track overnight workflow's orchestrator
  trigger has a fixed `t+Nh` timer that wastes wallclock when tracks finish
  early, (2) an overnight run should run consolidation/merge AS SOON AS all
  parallel tracks report complete, not on a clock, (3) the parent orchestrator
  session is too context-heavy to stay alive 12-20h and needs a scheduled
  successor to pick up track outputs, (4) you're choosing between "fire all
  tracks + one fixed-timer consolidator" vs "fire tracks + self-rescheduling
  polling consolidator". The polling pattern replaces a fixed wait with a
  cheap re-schedule loop that exits to consolidation at the first poll where
  all tracks report `phase: complete`. Distinct from `successor-handoff`
  (in-session parent polling a subagent) — this is for scheduled-trigger
  orchestrators that need to survive session ends.
author: Claude Code
version: 1.0.4
date: 2026-08-09
---

# Schedule-Poll Orchestrator Pattern

## Contents

- [Problem](#problem)
- [Context and trigger conditions](#context--trigger-conditions)
- [Solution](#solution)
- [Verification](#verification)
- [Example](#example)
- [Notes and references](#notes)
- [Version history](#version-history)

## Problem

Multi-track autonomous workflows (overnight ah-ha runs, parallel research,
long-running experiments) need an orchestrator that:

1. Dispatches N parallel tracks that each run 3–8h.
2. Runs consolidation/merge ONCE all tracks complete.
3. Doesn't keep a human-driven session alive for the full 12–20h.

Naive pattern: dispatch tracks at `t+0`, dispatch orchestrator at fixed
`t+Nh` (say, t+10h) where N is a conservative upper bound on track wallclock.

Failure modes of the fixed-timer pattern:

- **Idle waste when tracks finish early.** If tracks finish at t+5h but
  orchestrator is scheduled at t+10h, consolidation sits idle for 5h.
- **Hard failure when tracks run long.** If a track takes 11h, orchestrator
  fires at t+10h, sees incomplete state, has to bail out. User wakes up to
  no consolidation + tracks still running + no retry scheduled.
- **No visibility between.** User has no signal of how tracks are progressing
  between dispatch and fixed-timer wake-up.

## Context / Trigger Conditions

Use this route only for two or more scheduled tracks whose completion time is
variable and whose successor must survive the original session ending. For
in-session agents, use `successor-handoff`.

Routing here grants no execution authority. Before each action, read the
recorded grant for that exact action. Commit, push, pull-request creation,
network access, paid calls, and every other external write are separate grants.
If a grant is absent or denied, record `MISSING_AUTHORITY` and stop at the
durable local handoff. Local consolidation and pull-request creation are
separate steps.

## Solution

### Use the installed state-machine helper

Resolve this workflow from the active umbrella loader and run the helper beside
this file:

```bash
POLL_HELPER="$(cd "$(dirname "$ACTIVE_WORKFLOW")" && pwd -P)/scripts/poll_orchestrator.py"
STATUS=/absolute/state/status.json
JOURNAL=/absolute/state/orchestrator-poll.jsonl

python3 -B "$POLL_HELPER" init \
  --status "$STATUS" \
  --journal "$JOURNAL" \
  --run-id v5-run \
  --dispatch-epoch 1786233600 \
  --hard-ceiling-seconds 50400 \
  --poll-interval-seconds 1800 \
  --track track-a --track track-b --track track-c --track track-d
```

Do not hand-edit `status.json`. Each track records its terminal result through
`mark-track`; the helper holds one journal-derived run lock, validates the
complete track set, atomically replaces status, and appends one locked journal
event. The exact-schema-2 status and initialization row bind the normalized
absolute status path, journal path, and full initialization configuration plus
its digest. Missing, corrupt, linked, truncated, aliased, copied, or undeclared
state fails closed before another operation can be claimed. A nonterminal
`running` heartbeat also requires a caller-supplied stable `--update-id`; an
exact retry reuses that ID and cannot split or overwrite the journal. Schema-1
poll state is not migrated: inspect it manually and initialize a reviewed new
run instead of carrying an old `CLAIMED` state forward.

```bash
python3 -B "$POLL_HELPER" mark-track \
  --status "$STATUS" --journal "$JOURNAL" --run-id v5-run \
  --track track-a --phase complete --reason "verified output complete"
```

Every scheduled orchestrator invocation has one stable trigger ID:

```bash
python3 -B "$POLL_HELPER" poll \
  --status "$STATUS" --journal "$JOURNAL" \
  --run-id v5-run --trigger-id v5-orchestrator-0001
```

Interpret the returned action literally:

- `RESCHEDULE`: the helper returns one stable `next_trigger_id` and time.
  Inspect the schedule for that ID before creating it. Creating or changing the
  trigger requires the separately recorded network and external-write grants.
  Before that API call, run `decide-action --action schedule-trigger`; without
  both grants, its durable decision is `MISSING_AUTHORITY` and the route hands
  off locally.
- `CONSOLIDATION_CLAIMED`: the helper durably recorded the one local
  consolidation operation ID. Run that local operation once. If it needs a
  commit or paid call, run `decide-action --action commit` or
  `decide-action --action paid-call` at that action point and stop unless the
  corresponding durable decision is `AUTHORIZED`.
- `RESUME_CONSOLIDATION_CLAIM`: do not run consolidation again. Inspect the
  stable output path, then either complete the existing claim with its exact
  evidence digest or stop for recovery.
- `LOCAL_COMPLETE_EXTERNAL_ACTIONS_NOT_AUTHORIZED`: consolidation is complete;
  no push, network call, or pull request has been authorized or attempted.
- `RESUME_PULL_REQUEST_CLAIM`: query the remote by the returned idempotency
  key. Do not create a second pull request.
- `COMPLETE`: both durable claims and their exact evidence receipts are
  complete.

After local consolidation, bind its output:

```bash
python3 -B "$POLL_HELPER" complete-consolidation \
  --status "$STATUS" --journal "$JOURNAL" --run-id v5-run \
  --operation-id <returned-operation-id> \
  --evidence-path /absolute/output.html --evidence-sha256 <sha256>
```

Every external action has a separate create-once decision. For example:

```bash
python3 -B "$POLL_HELPER" decide-action \
  --run-id v5-run --action push \
  --decision-output /absolute/state/push-authority-decision.json \
  --authority-receipt /absolute/authority.json
```

Only a result whose `callable` field is `true` permits that exact action. Run
the command separately for `schedule-trigger`, `commit`, `push`,
`pull-request`, `paid-call`, or `external-write`; one decision never authorizes
another action. The CLI performs no external action. A loaded caller must pass
the decision, exact action name, and its injected action callback through
`run_guarded_action`; the guard rereads the authority receipt and calls the
callback once only while every required grant remains present. Do not invoke a
callback merely by branching on the serialized `callable` field.

Pull-request creation is optional and separately authorized. The authority
receipt has exact schema 1, record type
`schedule_external_action_authority`, this run ID, an owner and UTC time, and
exact Boolean grants for `commit`, `push`, `pull_request`, `network`,
`paid_call`, and `external_write`. The PR action requires pull-request, network,
and external-write grants. Commit and push each require their own prior action
decision; a route choice or overnight request cannot supply any grant.

```bash
python3 -B "$POLL_HELPER" claim-pr \
  --status "$STATUS" --journal "$JOURNAL" --run-id v5-run \
  --decision-output /absolute/state/pull-request-authority-decision.json \
  --authority-receipt /absolute/authority.json
# Inspect or create exactly one PR using the returned idempotency key.
python3 -B "$POLL_HELPER" complete-pr \
  --status "$STATUS" --journal "$JOURNAL" --run-id v5-run \
  --operation-id <returned-operation-id> \
  --receipt-path /absolute/pr-receipt.json --receipt-sha256 <sha256>
```

The create-once PR receipt must be nonempty exact-schema JSON with record type
`schedule_pull_request_receipt`, this run ID, the returned operation ID as both
`operation_id` and `idempotency_key`, provider, `owner/repository`, provider PR
identity, HTTPS URL, `OPEN` or `EXISTING_OPEN` state, and UTC time. Empty,
wrong-operation, or drifted receipts do not complete the latch.

At the hard ceiling, the helper records every incomplete track as
`tapped_out` with a reason before claiming consolidation. It never indexes a
missing track and never truncates status. A retry after any crash reads the
durable `CLAIMED` or `COMPLETE` state and returns the existing operation;
outside the injected `run_guarded_action` boundary,
the helper itself never consolidates, commits, pushes, schedules, spends, or
opens a pull request.

### Dispatch plan

Dispatch tracks at t+0 and the first orchestrator poll near the shortest
plausible track completion. Thirty minutes is a useful default poll interval;
the hard ceiling should be explicit and finite. Every RemoteTrigger or
CronCreate call still needs its separately recorded external-action grant.

## Verification

Run:

```bash
python3 -m unittest scripts.test_schedule_poll_orchestrator -v
```

The controls cover first poll, missing and corrupt track state, hard ceiling,
duplicate and concurrent triggers, crash after claim, crash after local
completion, crash after remote PR creation, denied authority, exact replay,
linked or partial journals, and one durable consolidation/PR claim. Also check:

1. A running track returns `RESCHEDULE` without a consolidation claim.
2. All-terminal or ceiling state creates one `CONSOLIDATION_CLAIMED` record.
3. A duplicate consolidation trigger returns the stable operation as a resume,
   never a second instruction to run consolidation.
4. Denied commit, push, PR, network, paid-call, scheduling, or external-write
   grants produce durable `MISSING_AUTHORITY` decisions and no denied call.
5. Reintroducing an inline consolidation/PR call, an unlocked/truncating write,
   or the invalid `Path.write_text(..., mode="a")` example fails the source
   checker and tests.
6. Alternate or copied status files sharing a journal cannot initialize or
   claim; status/journal/lock aliases fail before any control-file write.
7. A crash after the initialized state is written but before its journal row
   retries to exactly one initialization row and a usable poll state.

## Example

For four tracks, initialize all four names, schedule the first poll at t+3h,
and use a 30-minute interval. If the last track completes at t+5h, the next
poll claims consolidation at or before t+5.5h. If a track never reports, the
hard-ceiling poll records it as `tapped_out` and claims the same single
consolidation operation. No PR is implied.

## Notes

- Track writers must use `mark-track`; direct writes do not participate in
  the lock or exact expected-track contract.
- Poll infrastructure consumes external trigger calls. Record a finite trigger
  and cost budget before dispatch.
- An expired timer does not authorize takeover of a claimed operation. Inspect
  the durable claim and its evidence before any recovery decision.

## References

- The **`successor-handoff`** skill — sibling skill for in-session parent
  orchestrators. Source:
  [wan-huiyan/context-baton](https://github.com/wan-huiyan/context-baton/blob/main/plugins/successor-handoff/SKILL.md).
- The **`claude-code-delayed-execution`** skill — choosing between CronCreate
  and RemoteTrigger dispatch mechanisms.
- The **`overnight-insight-discovery`** skill — reference consumer of this
  pattern (Phase F / RESUME_MORNING.md). Source:
  [wan-huiyan/overnight-workflows](https://github.com/wan-huiyan/overnight-workflows/blob/main/plugins/overnight-insight-discovery/SKILL.md).
- Pattern origin: the client v5 overnight run session S98 (2026-04-21).

## Version history

- **v1.0.4** (2026-08-09) — Bound exact-schema-2 state and initialization to
  one normalized status path, journal path, full configuration digest, and
  journal-derived run lock. Alternate, copied, aliased, and legacy claimed
  state now fails closed; initialization can repair its one missing crash row.
- **v1.0.3** (2026-08-09) — Replaced the non-executable polling sketch with
  the installed, mutation-tested state machine. It atomically records one
  consolidation/PR claim and requires separate authority for every external
  action.
- **v1.0.2** (2026-08-09) — Added the schedule-poll route to the Codex umbrella
  candidate.
- **v1.0.1** (2026-08-06) — The three References entries above were markdown
  links to `~/.claude/skills/<name>/SKILL.md`. That path only exists when a
  skill was copied in by hand; a plugin install puts it under
  `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, so the links
  pointed at a file most readers cannot open. There is no local path that
  resolves under every install method, so they are now plain skill names plus
  a GitHub URL where the source repo is known.
- **v1.0.0** (2026-04-21) — Initial release.
