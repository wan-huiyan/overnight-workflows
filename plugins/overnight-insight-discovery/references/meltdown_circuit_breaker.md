# Meltdown circuit breaker (per-track)

**Added v1.4.0.** Each parallel track runs behind a circuit breaker. If a track
burns through tool calls or wallclock without producing a new finding artifact,
it gets aborted and respawned with a fresh context.

Evidence basis:
- [Beyond pass@1 arXiv 2603.29231](https://arxiv.org/html/2603.29231v1) —
  frontier models exhibit "meltdown behavior" (looping, self-contradiction,
  hallucinated tool output) in up to 19% of very-long-horizon episodes.
  Checkpoint-and-restart at subtask boundaries is the validated fix; episodic
  memory scaffolds did not help.
- [ZenML 1200 production deployments](https://www.zenml.io/blog/what-1200-production-deployments-reveal-about-llmops-in-2025)
  — GetOnStack lost $47K to an 11-day undetected agent loop; DoorDash enforces
  hard step + wall-clock budgets.
- [SwarmClaw autonomous missions](https://github.com/swarmclawai/swarmclaw) —
  joint USD + token + turn + wallclock budgets.

## Thresholds

Per track, whichever fires first:

| Signal | Threshold | Source |
|---|---|---|
| Tool calls without new finding | 50 | heuristic; tune per track type |
| Wallclock minutes without new finding | 90 min | slow enough to allow BQ batches, fast enough to catch thrash |
| Cumulative tool calls | 400 | overall guardrail |
| Cumulative wallclock | 6.5 hr | overall guardrail (soft) / 7 hr (hard) |

A "new finding" means a new file committed to `state/findings/NNN.md` with a
claim block (see `sql_reexecution_gate.md`). Retractions don't count.

Track-type overrides live in `scoping/config.yaml`:

```yaml
circuit_breaker:
  track_b:
    calls_per_finding: 50
    minutes_per_finding: 90
  track_c:
    # Track C is deterministic-heavy; looser thresholds because most
    # compute is Python scans, not LLM tool calls.
    calls_per_finding: 30
    minutes_per_finding: 120
```

## Detection implementation

Each track's orchestrator reads `state/<track>/heartbeat.jsonl` every 5 min:

```jsonl
{"ts": "...", "tool_calls_total": 127, "findings_total": 3, "last_finding_ts": "..."}
```

The heartbeat writer is a lightweight wrapper around the tool-use event stream;
each tool call appends a line. Finding commits also append a line.

Circuit-breaker checker (runs every 5 min from parent orchestrator):

```python
def check_meltdown(track_name: str) -> Optional[str]:
    hb = load_jsonl(f"state/{track_name}/heartbeat.jsonl")
    if not hb:
        return None
    latest = hb[-1]
    last_finding_ts = latest["last_finding_ts"] or hb[0]["ts"]
    idle_calls = latest["tool_calls_total"] - count_calls_up_to(hb, last_finding_ts)
    idle_min = minutes_between(last_finding_ts, latest["ts"])

    if idle_calls > cfg["calls_per_finding"]:
        return f"IDLE_CALLS={idle_calls}"
    if idle_min > cfg["minutes_per_finding"]:
        return f"IDLE_MINUTES={idle_min}"
    if latest["tool_calls_total"] > 400:
        return "TOTAL_CALLS_OVER_400"
    if cumulative_wallclock(hb) > 7 * 60:
        return "WALLCLOCK_HARD_CAP"
    return None
```

## Abort procedure

When `check_meltdown` returns non-None for a track:

1. **Write abort artifact:** `state/<track>/MELTDOWN_ABORT.json` with the
   reason, heartbeat tail (last 20 lines), last 5 tool calls for forensics.
2. **Send SIGTERM** (or kill background agent via Agent SDK's TaskStop) to
   the track's subagent. Not SIGKILL — allow 30s for graceful file flush.
3. **Checkpoint what's salvageable:** any finding in `state/findings/` is
   retained. In-flight work in `state/<track>/scratch/` is preserved but
   labeled `INCOMPLETE_AT_MELTDOWN` in status.json.
4. **Decision: respawn or give up.** If `respawn_budget > 0` (default 1 per
   track), dispatch a fresh successor per `phase_a_tracks.md` §"Successor
   handoff" — fresh subagent reads checkpoint + findings + pending, excludes
   the meltdown scratch. If budget exhausted, the track ships with whatever
   findings it has, flagged `[MELTDOWN_PARTIAL]` in the consolidation.

## What counts as "progress"?

The tightest definition: a committed finding file with a claim block. This is
intentionally strict. A track that's "exploring productively" but not yet
committing findings will trip the breaker — and that's the right behavior. If
exploration is worth doing, it's worth checkpointing into
`state/<track>/hypothesis_log.md` frequently, which prevents loss on abort
while still forcing the track to converge on committed findings.

Loose alternative (configurable): count hypothesis-log entries as progress
with a 3× weighting — i.e. 3 log entries = 1 finding's worth of credit.
Enable via `circuit_breaker.count_hypotheses: true`. Off by default because
the stricter policy catches thrash faster.

## False-positive mode

Very large BQ queries can legitimately take 45+ min. If a track is genuinely
waiting on a single long-running query, it should NOT count as idle. Two
mitigations:

1. **Query-in-flight flag:** when a track dispatches a BQ query, it writes
   `state/<track>/in_flight_query.json` with `{job_id, started_ts}`. The
   breaker excludes time since `started_ts` from the idle calculation.
2. **Job-based heartbeat:** every 10 min of a long-running BQ job, the track's
   wrapper emits a heartbeat line `{kind: "bq_polling", job_id: "..."}` so
   the track isn't silent from the parent's perspective.

## Cost + observability

The meltdown check itself is nearly free (reads a JSONL tail, writes to
status). The overhead is in heartbeat emission — one JSONL line per tool
call — which adds ~0.1 ms per call, negligible.

The morning_summary.md §1 (Headline) MUST surface any track that hit
`MELTDOWN_ABORT` even if the run otherwise succeeded. Silent meltdowns that
ship partial-brief-without-disclosure are the worst-case outcome.

## What this does NOT catch

- **Tight infinite loops inside a single tool call.** If a BQ query runs
  forever due to a bad join, the BQ-level timeout (30 min default) kicks in
  before the meltdown breaker. Configure BQ `maximum_execution_time` in the
  job creation.
- **Semantically-wrong progress.** A track committing finding-after-finding
  that are all garbage looks like progress to the breaker. The SQL
  re-execution gate + panel catch this downstream.
- **Silent auth rot mid-flight.** Separate system; see
  `phase_0_preparation.md` §0.-1.1 Mid-run auth probe.
