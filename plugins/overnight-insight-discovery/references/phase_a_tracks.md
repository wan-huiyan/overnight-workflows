# Phase A — Launch both tracks (parallel)

Tracks B and C run as independent subagents against the same scoping doc. They
don't coordinate; they produce separate briefs that the consolidator merges
later.

## Track B — LLM-autonomous

### Dispatch

One `Agent` tool call:
- `subagent_type`: `general-purpose` (or the project's equivalent for broad tool access)
- `model`: largest-context model available (Opus 4.7 1M at time of writing)
- `run_in_background`: `true` — parent monitors via `state/status.json`
- `description`: `Track B — overnight ah-ha insight exploration for <client>`

### Prompt shape

The prompt should include, in order:

1. **Role**: "You are Track B of the overnight ah-ha insight discovery run for <client>."
2. **Goal**: produce `track_b/brief.md` with 1–2 funnel leaks + 1–2 surprise
   patterns, each with chart + BQ query + stat support.
3. **Hard constraints** (each with a WHY — Opus respects explained rules, works
   around rigid ones):
   - Do not use `CURRENT_DATE()` — the run crosses midnight UTC.
   - Reject rediscovery — check `known_knowns_by_cohort.jsonl` before writing up each finding.
   - Reject percentile-bucket identity traps — see `analysis_principles.md`.
   - Reject trivial tautologies (e.g. "Accepted students enroll more than In-Process" is funnel definition, not finding).
4. **Evidence requirement**: every claim needs a BQ query in `track_b/queries/NNN_<slug>.sql` + chart in `track_b/charts/` + row in `track_b/findings/NNN.md`.
5. **Budget**: wall-clock cap + BQ cap (per-track share from scoping/config). Reference `scripts/bq_budget.py` wrapper as mandatory.
6. **File-first discipline**: every checkpoint lands in `state/`. If context pressure rises, set `state/status.json: {phase: "needs_successor"}` and the parent will dispatch a fresh subagent reading state/ files.
7. **Commit cadence**: per-hypothesis, per-finding, per-draft, per-self-review.
8. **Inputs to read first**: scoping files (config, canonical_numbers, known_knowns_by_cohort, new_feature_cte_snippets, feature_families), `analysis_principles.md`, any project discovery docs.
9. **Output signal**: when done, set `state/status.json: {phase: "complete"}` + `brief.md` + `self_review.md` (subagent's 3-weakest-claims list).

### Monitoring

Parent polls `state/status.json` periodically (e.g. every 15 min). Track B's run
is long (hours), so don't wait synchronously — do other work between polls.

### Meltdown circuit breaker (v1.4.0)

In addition to status polling, the parent runs a meltdown check every 5 min
against `state/<track>/heartbeat.jsonl`. Full contract in
`references/meltdown_circuit_breaker.md`. Summary:

- **Tool calls without new finding > 50** → `MELTDOWN_ABORT`, respawn with
  fresh context (1 respawn budget per track by default).
- **Wallclock minutes without new finding > 90** → same.
- **Cumulative tool calls > 400** or **wallclock > 7 hr** → hard abort, no
  respawn, ship partial brief with `[MELTDOWN_PARTIAL]` banner.

Long-running BQ queries are excluded from idle calculation via the
`state/<track>/in_flight_query.json` marker.

Track B's heartbeat writer wraps every tool call; Track C's wraps both Python
subprocesses and BQ job submissions. Configure thresholds per track in
`scoping/config.yaml:circuit_breaker`.

### Successor handoff

Triggers:
- Subagent self-flags `{phase: "needs_successor", reason: "..."}` in status.json
- OR parent counts > 200 tool events (proxy for context pressure)

Successor prompt:
> "You are the successor to track_b run NNN. First read `state/planning_board.md`,
> `state/checkpoint.md`, `state/pending.md`, and `state/findings/*.md`. Do NOT
> re-query BQ for things already in `state/queries/`. Continue from the top of
> `state/pending.md`. Commit before dispatching your next major step."

Max 3 hops per track. After 3, parent writes `status: capped_successors` and
submits whatever is in `brief.md` to the review loop.

### Failure modes accepted

Track B may discover nothing surprising ("everything was already in SHAP top-20
for its cohort"). That's a valid outcome — recorded in `workflow_learnings.md`.
Informs whether LLM-autonomous earns its keep for this domain in v2.

## Track C — Hybrid (deterministic + LLM narrator)

### Pipeline (5 stages, each a commit)

```
stage_1_scan  → candidates.parquet      (~1K–5K rows)
stage_2_rank  → ranked.parquet           (top 40 + surprise scores)
stage_3_prune → pruned.parquet           (novelty + diversification → 12–15)
     ↑ adaptive tuning loop wraps 1→3 up to 3 iterations
stage_4_draft → brief_draft_v1.md        (Opus narrates)
stage_5_refine→ brief_c_final.md         (Opus self-critiques → revises)
```

### Stage 1 — four parallel scans

Each scan writes a typed-schema parquet with columns:
`{scan, cohort_dim, cohort_value, feature_a, feature_b, magnitude, cohort_size, statistical_support, query_ref}`.

Default thresholds from `stage_config.yaml` (mutated by adaptive tuning).

| Scan | Emits |
|---|---|
| `scan_funnel_leak` | Per-cohort tenure-bucket enrollment-rate gradients (best/worst ratio). Threshold: gradient ≥ `lift_min` AND min-bucket cohort ≥ `cohort_min` AND at-risk population ≥ `at_risk_min`. |
| `scan_conditional_lift` | Binary feature pairs (A,B) where `P(enroll \| A∩B) / P(enroll \| A) ≥ 1.5` or `≤ 0.67`. Fisher's exact p < `p_max`. Limited to ~30 binary/bucketed features. |
| `scan_shap_interaction` | Pairs (feat_a, feat_b) with mean SHAP product above threshold. Limited to top-40 features by |SHAP| to bound combinatorics. |
| `scan_cohort_divergence` | (cohort, feature) where within-cohort SHAP rank differs from global rank by ≥ 5 positions. |

Every scan goes through `scripts/bq_budget.py`: dry-run → budget check → actual
run → log bytes scanned. Aborts if next scan would exceed the cap.

### Stage 2 — surprise ranking

```
surprise = log(cohort_size / threshold_size)
         × log(lift / 1.0)
         × (1 - novelty_similarity)           # from known-knowns lookup
         × directionality_score                # +1 unexpected, 0 expected, -0.5 contradicts prior
         × statistical_support                 # 0..1 from Fisher / bootstrap
         × (1 - recency_penalty_prior_briefs) # 0 for v1 runs
```

Top-40 by surprise → `ranked.parquet`.

### Stage 3 — prune to 12–15

Two filters:
- **Correlation cluster collapse**: Jaccard ≥ 0.6 + same direction → keep higher-ranked only. Writes `pruning_log.md` with every collapse + rationale.
- **Flavor balance**: guarantee ≥ 3 B-flavor (funnel_leak) + ≥ 3 C-flavor (pattern) survive.

### Stage 4 — Opus narrates

One `Agent` call to Opus 4.7 1M. Input:
- `pruned.parquet` as JSON
- Top-5 supporting BQ rows per candidate as evidence
- Project glossary / domain vocabulary
- `analysis_principles.md`
- Writing style guide (observation-first, no action prescriptions unless explicitly requested)

Output: `brief_draft_v1.md` with 2–3 B-flavor + 2–3 C-flavor findings.

**Explicit rule in the prompt**: do not add, reframe, or extrapolate beyond
pruned.parquet. Numbers come from the parquet, period.

### Stage 5 — self-critique + refine

Two more Opus calls:
1. Critique: "You are a skeptical <client domain expert>. List the 3 weakest
   claims in this brief + the 3 most likely criticisms from the client side.
   Be harsh."
2. Refine: "Revise the brief to address every critique. Do not hide limitations
   — surface them."

Output: `brief_c_final.md`. Committed.

### Adaptive tuning loop

See `references/adaptive_tuning.md`. Wraps Stages 1–3 up to 3 iterations. Yield
classes: `starved / flooded / trivial / skewed / one-cluster / goldilocks`. Each
triggers a specific relaxation or tightening of `stage_config.yaml`.

## Charts

Both tracks produce PNG charts (matplotlib or equivalent) with embedded
headline + axes + cohort size caption. Not interactive — we want deterministic,
regeneratable artefacts. Every chart script committed alongside its PNG so the
qa-expert review persona can reproduce pixel-for-pixel.

## Commit hygiene

Tracks commit to the same branch (it's fine — they write to disjoint
directories `track_b/` vs `track_c/`). Use descriptive prefixes so
`git log --oneline` tells the story:

- `track-b: hypothesis N — <slug>`
- `track-b: finding N — <slug>`
- `track-b: brief v0.N`
- `track-c: stage 1 scan — funnel_leak`
- `track-c: stage 2 rank`
- `track-c: stage 3 prune`
- `track-c: stage 4 draft`
- `track-c: stage 5 refine`

Plus adaptive-tuning iteration commits: `track-c: tune iter N — <yield_class>`.

## Context-pressure guidance for both tracks

Same successor-handoff pattern (Track B Section above). Track C's context
pressure is usually much lower because Python scripts handle the heavy data and
the LLM only sees the small pruned parquet. Threshold tweaks:
- Track B: trigger at 200 tool events
- Track C: trigger at 80 tool events

Parent orchestrator stays lean throughout — reads only `state/status.json` from
each track, never loads parquets or query results into its own context.
