---
name: cohort-broadening-event-source-scope-leak
description: |
  Diagnose silent monotonicity violations (e.g. "Low bucket has higher conversion
  rate than High bucket" on a rank-quality chart, or any reverse-ordered metric
  on a model-quality dashboard) caused by broadening a cohort filter in one CTE
  while leaving sibling event/stage-source CTEs at their original narrower
  scope. Use when: (1) you just removed or relaxed a `WHERE period IN (...)` /
  `WHERE scope = X` / similar scope filter on a cohort-defining CTE
  to broaden coverage (e.g. "include all scored records, not just period-A
  applicants"), (2) the downstream JOIN pulls in a `funnel_stage` /
  `latest_status` / `enrollment_state` field from a separate row in the same
  table, (3) a rank-quality chart suddenly shows reverse-ordered or otherwise
  impossible numbers (Low > High on Net/cumulative rate, while per-step
  conditional rates look fine), (4) the per-step transition rates (conditional
  on prior stage) look reasonable but the "% of full cohort reached final
  stage" column is broken. Root cause: cohort CTE now spans periods A+B+C; stage
  CTE still defaults to "latest stage across ALL periods" so a record scored
  for period A but advanced for period B gets credited at period-B's stage in
  period-A's funnel matrix. Fix: keep the period filter on the stage source, but
  use LEFT JOIN + COALESCE-to-'No Application' (or equivalent base state) so
  cohort breadth is preserved while stage stays period-specific.
author: Claude Code
version: 0.1.0-draft
date: 2026-05-07
status: draft
draft_notes: |
  Captured during a model-quality dashboard preview session. Pattern is real and
  the example is verified, but skill needs more work before promotion:
  - Trigger description could be tighter
  - Add CI-test invariant snippet for the Net-monotone assertion
  - Cross-reference more sister skills if any exist
  - Verify against a second instance before bumping to 1.0.0
  Do NOT run skill-sync / publish until promoted to 1.0.0.
disable-model-invocation: true
---

# Cohort-broadening event-source scope leak

## Problem

A multi-CTE SQL has a "who" CTE (cohort definition) and a separate "what they
did" CTE (event/stage source). Both originally have parallel period/scope
filters (e.g. `cohort_period IN ('P1', ...)`). You broaden the cohort filter to
include more rows (e.g. drop the period filter so NoApp / unscored / cross-period
records are included). You don't touch the event-source CTE.

The result: the JOIN now computes a "stage" or "outcome" for each cohort
member using events from a DIFFERENT scope than the cohort itself. A record
that's in the broadened cohort because it was scored for period A, but that
actually advanced for period B, gets credited at period-B's stage in period-A's
analysis. Numbers come out impossible-looking — typically a low-quality bucket
appears to outperform a high-quality bucket on cumulative-rate columns,
because the low-quality bucket has more records that progressed in *some
other period*.

## Context / Trigger Conditions

You've recently changed a cohort-scope filter, AND one or more of:

- **Reverse-ordered rank chart**: Low bucket beats High bucket on a Net /
  cumulative / conversion rate (e.g. Low Net ~47% > High Net ~23%)
- **Per-step rates look fine, cumulative does not**: each conditional
  transition rate (col 2 = X→Y given X reached, col 3 = Y→Z given Y reached)
  is monotone-correct, but the "% of full cohort that reached the final
  stage" column is inverted or noisy
- **NoApp→FirstStep rate is too high for low-propensity bucket**: e.g.
  ~86% of "Low" records reached AppCreated when only ~33% should have
  (because Low's "AppCreated" includes records that applied for a
  DIFFERENT period than the analysis is supposed to scope)
- **Headline lift unaffected, but funnel matrix broken**: the simpler
  bucket-vs-bucket lift number on resolved events stayed correct because
  it used a period-scoped event source; only the funnel/stage matrix that
  pulled "latest stage across all rows" went wrong

## Solution

The fix is **NOT** to undo the cohort broadening — that was deliberate. The
fix is to make the stage/event source period-aware so it returns NULL (or the
explicit base state) for records whose stage isn't from the analysis-scoped
period.

### Pattern: period-scoped stage with NoApp fallback

```sql
-- BEFORE (broken — broadened cohort, but stage source unchanged)
latest_stage AS (
  SELECT entity_id,
    ARRAY_AGG(funnel_stage ORDER BY scoring_date DESC LIMIT 1)[OFFSET(0)] AS stage
  FROM events_table
  WHERE scoring_date <= @sd
    AND funnel_stage IS NOT NULL
    -- ⚠ no period filter; stage now spans ALL periods
  GROUP BY entity_id
),
joined AS (
  SELECT fb.bucket, ls.stage     -- INNER JOIN drops cohort members; bad
  FROM cohort_broadened fb
  JOIN latest_stage ls USING (entity_id)
)

-- AFTER (fixed — stage source stays period-scoped, JOIN uses LEFT + COALESCE)
latest_stage AS (
  SELECT entity_id,
    ARRAY_AGG(funnel_stage ORDER BY scoring_date DESC LIMIT 1)[OFFSET(0)] AS stage
  FROM events_table
  WHERE scoring_date <= @sd
    AND cohort_period IN ('P1', 'P1a', 'P1b')   -- ← restored
    AND funnel_stage IS NOT NULL
  GROUP BY entity_id
),
joined AS (
  -- LEFT JOIN preserves the broadened cohort. Records without a period-A
  -- stage (i.e. they're in the cohort but didn't apply for period A) get
  -- the explicit base state.
  SELECT fb.bucket, COALESCE(ls.stage, 'No Application') AS stage
  FROM cohort_broadened fb
  LEFT JOIN latest_stage ls USING (entity_id)
)
```

The pattern generalises beyond `funnel_stage`. Anywhere the cohort is broader
than the event source's natural scope, you need:

1. **Keep the scope filter on the event source** (so events stay period-A-only)
2. **LEFT JOIN cohort × events** (so the broader cohort is preserved)
3. **COALESCE to the explicit base state** (so the math works — usually
   "no event happened" or the equivalent stage 0)

## Verification

After the fix, the cumulative / Net column should be monotone in the bucket
ordering you expect. Quick checks:

```sql
-- Per-bucket Net rate should rank-order correctly
SELECT bucket,
  SAFE_DIVIDE(SUM(IF(stage_ord >= 4, n_entities, 0)), SUM(n_entities)) AS net_rate
FROM stage_rank
GROUP BY bucket
ORDER BY net_rate DESC
-- Expected: High > Developed > Emerging > Low (or whatever the model says)
```

If still inverted, the leak is somewhere else — check every CTE that pulls
from the same events table and verify each one is period-scoped or
period-broadened to match the cohort.

Cross-check against a **resolved event metric** that uses a period-scoped
event source: if the lift on `IF(deposited_in_period_A, 1, 0)` looks
sensible for the same cohort but the funnel matrix doesn't, the leak is in
the stage source, not the cohort.

## Example (from issue trail)

Symptom in a model-quality funnel matrix on a multi-period propensity
dashboard (numbers illustrative, magnitudes and ordering preserved):

| Bucket | NoApp→App | App→Sub | Sub→Acc | Acc→Dep | **Net** |
|---|---|---|---|---|---|
| High | ~96% | ~99% | ~72% | ~33% | ~23% |
| Developed | ~96% | ~98% | ~69% | ~41% | ~27% |
| Emerging | ~80% | ~98% | ~68% | ~30% | ~16% |
| **Low** | ~86% | ~98% | ~77% | ~72% | **~47%** ← Low Net > High Net?! |

Low's NoApp→App at ~86% was the bigger giveaway (a Low-propensity record
should rarely even apply). Both anomalies traced to the same root: the
`latest_stage` CTE had its `cohort_period IN (period-A)` filter dropped
when the cohort was broadened, so Low-bucket records that actually advanced
for **period B** had their funnel_stage="Enrolled" pulled in as their
"period-A stage." Period-B advancers are over-represented in Low (they signed
up for a different period, so the model rated their period-A propensity low —
correctly).

Fix: restore the `cohort_period` filter on `latest_stage`, change the
`joined` CTE from INNER to LEFT JOIN, and `COALESCE(stage, 'No Application')`.

After the fix:

| Bucket | NoApp→App | App→Sub | Sub→Acc | Acc→Dep | **Net** |
|---|---|---|---|---|---|
| High | ~90% | ~99% | ~70% | ~29% | **~18%** |
| Developed | ~85% | ~99% | ~66% | ~33% | **~18%** |
| Emerging | ~66% | ~98% | ~67% | ~23% | **~10%** |
| Low | ~33% | ~98% | ~58% | ~28% | **~5%** |

Net rates now monotone (modulo High≈Developed which is itself a separate,
real model-quality finding). NoApp→App now ~33% for Low (matches intuition:
most low-propensity records never apply *for this period*).

## Notes

- This is structurally similar to but distinct from `bq-join-using-ambiguous-case-silent-empty`
  (CASE-statement ambiguity in joins) and `null-bucket-hides-progressors-in-snapshot-training`
  (NULL bucket suppression masking conversion in training data). Those operate
  at the value-encoding layer; this skill operates at the *scope-filter*
  layer of the SQL.
- Especially insidious in dashboards built on top of multi-period prediction
  tables (e.g. multi-term academic systems with multiple enrollment periods per
  year; subscription systems with monthly + annual plans; multi-region traffic
  data) where the same record can have legitimate events under multiple scopes.
- A useful invariant test for any rank-quality dashboard is: assert that
  the Net (cumulative) column is monotone in the bucket ranking — within a
  small tolerance for adjacent buckets. Failing that assertion in CI surfaces
  this class of bug before users see it.
- The "fix" is one-line in the SQL but **two-line in the diagnostic**: the
  symptom is in the cumulative column, but the root cause is two CTEs
  upstream. Trace the lineage of each metric column back to its event source
  and verify scope alignment.

## References

- Original surfacing: a multi-period propensity model-quality dashboard,
  funnel-stage matrix. The bug appeared when broadening the cohort to include
  NoApp (no-application) records, in service of a unified "everyone scored as
  the base" page-wide cohort decision.
- Sister skills:
  - `null-bucket-hides-progressors-in-snapshot-training` — same family
    (cohort-vs-event scope mismatch in ML training data)
  - `bucket-at-first-prediction` — same family (snapshot cohort vs
    full-history event scope)
  - `bq-join-using-ambiguous-case-silent-empty` — joins with silent
    semantics, different layer
