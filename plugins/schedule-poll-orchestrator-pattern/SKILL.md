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
version: 1.0.0
date: 2026-04-21
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

v5 Barry University ah-ha insight run (2026-04-21):

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

- [`successor-handoff`](~/.claude/skills/successor-handoff/SKILL.md) —
  sibling skill for in-session parent orchestrators.
- [`claude-code-delayed-execution`](~/.claude/skills/claude-code-delayed-execution/SKILL.md)
  — choosing between CronCreate and RemoteTrigger dispatch mechanisms.
- [`overnight-insight-discovery`](~/.claude/skills/overnight-insight-discovery/SKILL.md)
  — reference consumer of this pattern (Phase F / RESUME_MORNING.md).
- Pattern origin: Barry University v5 overnight run session S98 (2026-04-21).
