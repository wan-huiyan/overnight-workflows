# Phase 0.6 — v6.1 historical re-scoring (Path A, standard recipe)

**Added v1.6.0 (S98, 2026-04-21).** Codifies the historical re-scoring recipe
so future runs don't re-discover it. v2 (S92) and v4 (S95) both hit this
blocker; S94's v3 dropped the axis entirely. Path A is now the sanctioned
workflow.

---

## When Phase 0.6 runs

If the session plan commits to a `v6_residual` axis (i.e., any candidate that
stratifies on "where the current scoring model is wrong"), Phase 0.6 must
produce historical v6.1 scores aligned to the panel's `target_date` before
Phase A dispatches.

## Path A — inline feature materialisation (standard)

Four features are required by the trained v6.1 model but not present in
`ml_features.v10_term_enrollment_training_features`:

1. `days_in_accepted_not_deposited`
2. `attended_campaign_last_180d`
3. `responded_campaign_last_180d`
4. `in_process_missing_credentials`

### Workflow

1. Re-compute all four from raw BR + SF tables at the historical `target_date`.
2. Write to `ml_scratch.v4_ahha_inline_features_{panel_date}`.
3. `LEFT JOIN` the inline table to the v10 training features by
   `visitor_id` + `target_date`. Output → `ml_scratch.v4_ahha_scoring_input_{panel_date}`.
4. Invoke the scoring script with
   `--features-table ml_scratch.v4_ahha_scoring_input_{panel_date}`.

### Canonical CTE source

CTE recipes for all four features are the single source of truth at:

    docs/overnight/2026-04-19/scoping/new_feature_cte_snippets.sql

**Reuse as-is.** Do not re-derive — the snippets were reviewed in S94 and
exercised in v4.

## Fail-loud contract

If **any** of the four features returns all-NULL or cannot be materialised,
**STOP and escalate**. Do NOT proceed to Phase A with partial inputs.

**Path B is banned.** Cross-temporal score substitution (using current-day
v6.1 scores as proxies for historical ones) produced pathological residuals
in v4; the consolidator correctly refused to publish them. Do not re-invent.

## Option C — sanctioned fallback

If Path A escalates, the sanctioned fallback is:

1. **Drop the `v6_residual` axis.** Reallocate its 0.20 weight evenly across
   the four surviving axes (stage, engagement, recency, novelty).
2. **Pivot the client brief's "Model Bias Audit" section** to Option C:
   descriptive high-yield cohort ranking using stitched-view aggregates only.
   See `phase_c_consolidation.md` §"Option C fallback" for the full brief
   structure.

Escalating to Option C is a normal outcome, not a failure. v4 shipped Option C
cleanly and the consolidated client brief was well-received.

## One-liner for morning_summary §4

> Path A historical-scoring recipe codified; Path B banned; Option C is the
> sanctioned fallback.
