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
  orchestrators that need to survive session ends. Also covers the standing
  prompt itself going stale: a loop prompt is written once and re-read on every
  wake, and it asserts in the present tense whatever was true when it was
  written, so re-verify its own factual claims (what is authorised, what is
  capped, what is blocked) at the top of each wake before dispatching anything.
author: Claude Code
version: 1.1.0
date: 2026-08-06
---

# Schedule-Poll Orchestrator Pattern

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

This skill applies when ALL of these are true:

- You have 2+ parallel tracks/subagents that run 3–12h each.
- Track completion time has high variance (50%+ spread between fastest and
  slowest possible finish).
- You're dispatching via Claude Code's `schedule` skill or equivalent
  (RemoteTrigger / CronCreate), NOT via in-session foreground Agent calls.
- The orchestrator work (consolidation, HTML rendering, PR open) must run
  AFTER all tracks complete.
- You want "fire ASAP when ready" semantics, not "wake up at a fixed hour."

If the tracks are in-session subagents (`Agent` tool calls), use
`successor-handoff` instead — that's the in-session pattern.

## Solution

### Pattern: tracks at t+0, orchestrator polls + self-reschedules

```
Dispatch plan (via Skill(schedule) or equivalent):

  Trigger 1: "track-A"         — fire at t+0,  wallclock 4–6h
  Trigger 2: "track-B"         — fire at t+0,  wallclock 4–6h
  Trigger 3: "track-C"         — fire at t+0,  wallclock 4–6h
  Trigger 4: "track-D"         — fire at t+0,  wallclock 4–6h
  Trigger 5: "orchestrator"    — fire at t+3h  (first poll; tune to
                                  shortest-possible track completion time)
```

Each track writes to a shared state directory on completion:

```
state/status.json:
{
  "dispatch_epoch": <unix_ts>,
  "track_a": { "phase": "complete", "completed_at": <ts>, ... },
  "track_b": { "phase": "running",  ... },
  "track_c": { "phase": "complete", ... },
  "track_d": { "phase": "tapped_out", "reason": "...", ... }
}
```

### Orchestrator trigger protocol

The orchestrator runs the following check on EVERY fire (not just the first):

```python
import json, time
from pathlib import Path

status = json.load(open("state/status.json"))
tracks = ["track_a", "track_b", "track_c", "track_d"]
phases = {t: status.get(t, {}).get("phase") for t in tracks}
done = all(p in ("complete", "tapped_out") for p in phases.values())
elapsed_h = (time.time() - status["dispatch_epoch"]) / 3600

# Log the poll outcome
Path("state/orchestrator_poll_log.jsonl").write_text(
    json.dumps({"ts": time.time(), "phases": phases, "elapsed_h": elapsed_h}) + "\n",
    mode="a",
)

HARD_CEILING_HOURS = 14  # or whatever your max-patience is

if done:
    # All tracks report terminal state (complete or tapped_out)
    # Proceed to consolidation inline, stay alive through completion
    run_consolidation_and_pr()
elif elapsed_h < HARD_CEILING_HOURS:
    # Re-schedule self for t+30min and exit cleanly
    schedule_self_at(minutes_from_now=30)
    exit(0)
else:
    # Force-proceed: tap-out any incomplete tracks, run anyway
    for t in tracks:
        if phases[t] not in ("complete", "tapped_out"):
            status[t]["phase"] = "tapped_out"
            status[t]["reason"] = "[TAPPED — wallclock ceiling hit]"
    json.dump(status, open("state/status.json", "w"))
    run_consolidation_and_pr()
```

Key properties:

- **First poll at t+3h** (or shortest-possible-track-complete time). Earlier
  polls waste trigger budget; later polls delay consolidation.
- **Re-schedule interval = 30 min.** Small enough that worst-case delay is
  modest (30 min after last track finishes); large enough that the
  orchestrator_poll_log stays human-scannable.
- **Hard ceiling = 14h** (or 2× expected max track duration). Prevents
  runaway polling if a track deadlocks.
- **Each poll is idempotent** — the protocol is the same code whether it's
  poll #1 or poll #6. Orchestrator trigger doesn't need to know its own
  iteration count.

### Dispatch trigger sketch (`Skill(schedule)` call)

```
Skill(schedule) with:
  - "v5-track-A"      at t+0,   run track_prompts/A.md, max wallclock 6h
  - "v5-track-B"      at t+0,   run track_prompts/B.md, max wallclock 4h
  - "v5-track-C"      at t+0,   run track_prompts/C.md, max wallclock 4h
  - "v5-track-D"      at t+0,   run track_prompts/D.md, max wallclock 6h
  - "v5-orchestrator" at t+3h,  run RESUME_MORNING.md  (implements the
                                 poll protocol above; may re-schedule self)
```

### Re-verify the standing prompt's own claims on every wake

The poll loop above is idempotent by design — every wake runs the same code. The **prompt**
that wake reads is idempotent too, and that is the problem. It was written once, at
dispatch, and it asserts in the present tense whatever was true then: what is authorised,
what is capped, what is blocked, what the owner has not answered yet. Nothing expires it and
nothing timestamps it, so hours later the loop is still relaying yesterday's world to every
agent it dispatches, with full confidence and no visible staleness.

**This is not hypothetical and it is not rare.** In one run the standing prompt was wrong
three times: it told agents a deploy was **unauthorised after the owner had authorised it**,
and it told them **not to raise a cap the owner had asked to have raised**. Each agent read
the prompt, believed it, and behaved correctly given a false premise. Nothing failed, so
nothing surfaced.

So make re-verification the first step of every wake, before any dispatch:

1. **Enumerate the prompt's factual claims** when you write it — permissions, caps, blockers,
   "the owner has not yet decided X", any number. Keep them in one clearly-marked block
   rather than scattered through the instructions, so the next wake knows what to re-check.
2. **Re-check each claim against live state at the top of the wake** — the current
   authorisation record, the current cap, the current issue or PR state — and correct the
   working copy before dispatching. A claim you cannot re-check cheaply should not be in a
   standing prompt at all.
3. **Keep the claims in a dated addendum file the loop reads, not in the trigger's prompt
   text.** Editing the trigger changes every future call signature and can force a rebuild;
   an appended, dated addendum reaches later agents without disturbing running ones. Same
   mechanism as `overnight-multi-issue-implementation`'s "amend a running orchestration
   through a file on disk, not the script", applied to the loop's own instructions.
4. **Log the re-verification result in the poll log** alongside the phase snapshot, so a
   morning reader can see which wake was still operating on a stale premise.

The wider rule, worth stating in the prompt itself: **an instruction not to do something is a
statement about a moment, and it goes stale in the permissive direction as often as the
restrictive one.** A loop that keeps refusing a thing the owner has since approved looks
exactly like a loop that is behaving.

## Verification

The pattern is working correctly when:

1. **First poll logs `done: False` with some tracks still running.** Visible
   in `state/orchestrator_poll_log.jsonl`. Orchestrator exited without
   running consolidation.
2. **Next scheduled run of the orchestrator appears in the schedule list.**
   `Skill(schedule) list` shows a new trigger at `first_poll + 30min`.
3. **When all tracks report `phase: complete`, the next poll runs
   consolidation.** `state/orchestrator_poll_log.jsonl` shows the `done:
   True` entry, and PR-open + HTML-render run that same pass.
4. **Total wallclock is close to `max(track_durations) + 30 min + consolidation`.**
   Not close to the hard ceiling unless a track genuinely ran long.

## Example

v5 the client ah-ha insight run (2026-04-21):

- 4 tracks dispatched at t+0: Track B (LLM-autonomous, 6h budget), Track C
  (deterministic scan, 4h budget), Track D (sub-code decomposition, 4h),
  Track E (engagement vocab sweep, 6h).
- Orchestrator first poll at t+3h. Expected path:
  - Poll at t+3h: Track C done, B/D/E running → re-schedule.
  - Poll at t+3.5h: C done, D done, B/E running → re-schedule.
  - Poll at t+4h: C/D done, B done, E running → re-schedule.
  - Poll at t+5.5h: all 4 done → proceed to Track F + consolidation + PR.
- Total wallclock: ~8h for the full workflow vs. 10h had we used fixed t+10h
  timer. Orchestrator + consolidation fires at first 30-min boundary after
  last track finishes.

## Notes

- **This pattern assumes tracks report completion atomically** to
  `state/status.json`. If a track crashes without writing status, the
  orchestrator will keep polling until hard-ceiling. Mitigation: wrap track
  main() in `try/finally` that always writes `phase: tapped_out` with a
  reason on exit.
- **The first-poll delay tuning matters.** Too early and you waste polls on
  certain-not-done state. Too late and you delay consolidation. Pick the
  95th-percentile shortest-track-completion as a rule of thumb.
- **Don't use this for in-session subagents.** If your tracks are `Agent`
  tool calls in a single Claude Code session, use `successor-handoff`
  instead. This skill is specifically for scheduled-trigger dispatch where
  the orchestrator session may not be alive between polls.
- **Polling infrastructure is cheap but not free.** Each poll consumes one
  trigger invocation + a small amount of BigQuery / status-read cost.
  Typical cost: 2–6 polls per run → negligible.
- **Hard ceiling is a safety net, not a target.** If you routinely hit it,
  your track budgets are wrong; fix those instead of raising the ceiling.

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

- **v1.1.0** (2026-08-17) — Adds **"Re-verify the standing prompt's own claims on every
  wake"**. The poll protocol was already idempotent; the *prompt* each wake reads is
  idempotent too, and that is the defect — it was written once at dispatch and asserts in
  the present tense whatever was true then, with no timestamp and nothing to expire it. On
  one run the standing prompt was wrong three times: it told agents a deploy was
  unauthorised **after the owner had authorised it**, and told them not to raise a cap the
  owner **had asked to have raised**. Every agent read it, believed it, and behaved
  correctly on a false premise, so nothing failed and nothing surfaced. The new section
  requires the prompt's factual claims to be enumerated in one marked block when it is
  written, re-checked against live state at the top of each wake before any dispatch, kept
  in a dated addendum file rather than in the trigger's own text (the loop-level form of
  `overnight-multi-issue-implementation`'s "amend through a file on disk, not the script"),
  and the re-verification result logged next to the phase snapshot. The line worth carrying
  out of it: **a restriction goes stale in the permissive direction as often as the
  restrictive one**, and a loop still refusing something the owner has since approved looks
  exactly like a loop that is behaving.
- **v1.0.1** (2026-08-06) — The three References entries above were markdown
  links to `~/.claude/skills/<name>/SKILL.md`. That path only exists when a
  skill was copied in by hand; a plugin install puts it under
  `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, so the links
  pointed at a file most readers cannot open. There is no local path that
  resolves under every install method, so they are now plain skill names plus
  a GitHub URL where the source repo is known.
- **v1.0.0** (2026-04-21) — Initial release.
