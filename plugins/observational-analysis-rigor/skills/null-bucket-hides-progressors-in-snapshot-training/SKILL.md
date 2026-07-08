---
name: null-bucket-hides-progressors-in-snapshot-training
description: |
  Fix the "0% conversion in named stage bucket" false-negative when
  evaluating a sub-stratification feature on snapshot-based ML training data.
  Use when: (1) running a multi-query feature-evaluator diagnostic and observing
  0% outcome rate in a transitional-stage bucket (e.g., In-Process, Pending,
  Quote-Sent, Lead-New) with statistically meaningful N, (2) considering
  rejecting a candidate feature that sub-stratifies that bucket, (3) the
  training data is built from a current-state snapshot with a temporal gate
  like `decision_at <= target_date`. The bug: current-snapshot-based SQL
  typically sends pre-decision progressors to a NULL bucket, not the named
  transitional bucket. The named bucket self-selects for non-progressors
  (deterministic 0%). Sub-features within it cannot rescue that. But the
  feature may still have signal in the NULL bucket where progressors hide.
  Always stratify NULL by (was-gate-condition-met-at-T, has-submitted, etc.)
  before concluding a sub-stratification feature is useless.
author: Claude Code
version: 1.0.0
disable-model-invocation: true
---

# NULL Bucket Hides Progressors in Snapshot-Based Training Data

## Problem

When evaluating a candidate ML feature that sub-stratifies a transitional
funnel stage (In-Process, Pending-Review, Lead-New, etc.), the first
diagnostic query typically filters to rows where `stage_at_target_date = 'X'`
and measures outcome rate. If that rate is **0% across a large N**, the
instinct is to reject the feature — "no signal exists in this bucket."

That instinct is often wrong when the training data is built from a
**current-state snapshot** (upserted, not history-preserving) with a temporal
gate in the SQL. The gate usually looks like:

```sql
CASE
  WHEN decision_at IS NOT NULL AND decision_at <= target_date
  THEN current_snapshot_status        -- definitely-decided-before-T
  WHEN decision_at IS NULL AND status IN (pre-decision codes)
  THEN status                         -- definitely-pre-decision
  ELSE NULL                           -- AMBIGUOUS — decision happened AFTER T
END AS stage_at_target_date
```

**The trap:** records that were pre-decision at target_date T and then
progressed (got accepted, converted, closed) afterward have
`decision_at > T`. They fall to the ELSE branch → `stage_at_target_date = NULL`
— **not the named pre-decision bucket.**

So when you filter `WHERE stage_at_target_date = 'In-Process'`, you get only
those who are *still* in-process in the current snapshot. That population
is self-selected for non-progression. Their outcome rate is tautologically 0%.

The progressors — the ones you actually want to predict — live in the NULL
bucket, typically with `has_submitted = 1` and `decision_at > T`.

## Context / Trigger Conditions

Apply this skill when ALL of these hold:

- The training data is a **pre-computed feature table** built from a current-
  state snapshot (a CRM/ERP source such as Salesforce, HubSpot, Dynamics, or
  similar).
- The feature table has a temporal gate column like `stage_at_T`,
  `status_code`, `lead_status`, etc.
- You just ran a diagnostic query: `SELECT stage, COUNT(*), SUM(label), rate
  FROM features GROUP BY stage`.
- One or more transitional stages shows **exactly 0% outcome rate across
  large N** (hundreds of thousands of person-dates is common).
- You're about to reject a candidate sub-stratification feature ("no point
  adding it, the parent bucket is deterministic zero").

Indicators you're about to fall into this trap:

- The "0%" bucket has N ≫ the total number of progressors in your label
  (suggests the gate has filtered to non-progressors).
- NULL bucket in the same query has non-zero outcome rate.
- `has_submitted` or equivalent "was action taken before T" flag
  is not 0 for NULL-bucket rows.

## Solution

### 1. Before rejecting, stratify the NULL bucket

Rerun the diagnostic with an explicit NULL-bucket breakdown:

```sql
WITH stratified AS (
  SELECT
    visitor_id,
    CASE
      WHEN stage_at_T IS NOT NULL THEN stage_at_T
      WHEN stage_at_T IS NULL AND has_submitted = 1
           AND decision_at > target_date THEN 'pre_decision_progressor'
      WHEN stage_at_T IS NULL AND has_submitted = 0 THEN 'no_application_yet'
      ELSE 'null_other'
    END AS bucket,
    label
  FROM training_features
  WHERE target_date = '2025-03-01'
)
SELECT bucket, COUNT(*), SUM(label), SUM(label) / COUNT(*) AS rate
FROM stratified
GROUP BY bucket
ORDER BY rate DESC;
```

You will typically see:

| bucket | N | rate |
|---|---|---|
| pre_decision_progressor | (hundreds to thousands) | **meaningful % — the signal lives here** |
| no_application_yet | (large) | small baseline |
| In-Process (stuck) | (large) | **0.00%** (the trap) |
| Rejected / Withdrawn | (medium) | 0% (expected) |

### 2. Re-evaluate the sub-stratification feature against the correct bucket

The sub-feature (e.g., `ready_to_review`, `has_followup_scheduled`,
`credit_check_passed`) should be measured within the **pre_decision_progressor
bucket**, not the named stuck bucket. Pool across target_dates for statistical
power if N per target_date is small.

If the sub-feature shows a gradient there, it has real signal — even if the
magnitude is smaller than the diagnostic first suggested. Document the
training-serving asymmetry: at serving time the current snapshot IS the
point-in-time truth, so serving-time signal may be stronger than training-
time measurement indicates.

### 3. Document the bucket semantics in the training SQL

If the temporal gate routes progressors to NULL, add a comment in the CTE
that makes this explicit, so future diagnostics know to stratify NULL:

```sql
-- ⚠️ Bucket semantics:
--   stage_at_T = 'In-Process'  → never decided → stuck (0% outcome)
--   stage_at_T = 'Decided'      → decided before T → current-state leak-safe
--   stage_at_T = NULL           → decided after T (progressors hide here)
-- Always stratify NULL before drawing conclusions about pre-decision signals.
```

## Verification

Your correction is right when:

- The pre-decision-progressor sub-bucket shows non-zero outcome rate
  consistent with the label's base rate or higher.
- The named stuck bucket remains 0% (confirms it's the non-progressor slice).
- The sub-stratification feature (ready-to-review, credit check, etc.) shows a
  gradient within the progressor bucket that you can statistically defend
  with N.

## Example

**Scenario:** evaluating `ready_to_review` (BOOL) as a feature for a
conversion-propensity model, sub-stratifying the In-Process population.

**Wrong analysis (initial):**

```sql
SELECT ready_to_review_current, COUNT(*), SUM(converted), rate
FROM features
WHERE status_code = 'In-Process' AND target_date = '2025-03-01'
GROUP BY ready_to_review_current;
-- ready_to_review=false: ~640 rows, 0 converted, 0.00%
-- ready_to_review=true:     1 row,  0 converted, 0.00%
```

Initial conclusion: zero signal, reject the feature. **Wrong.**

**Correct analysis (after pushback):**

Training SQL gate routes pre-decision progressors to `status_code IS NULL`,
not `'In-Process'`. Re-query with the stratified NULL bucket:

```sql
SELECT ready_to_review, COUNT(*), SUM(converted), rate
FROM features_with_decision_gate
WHERE has_application = 1
  AND has_submitted = 1
  AND decision_at > target_date        -- progressors hide here
GROUP BY ready_to_review;
-- ready_to_review=false: ~420,000 person-dates, ~82,000 converted, ~19.5%
-- ready_to_review=true:    ~3,600 person-dates,    ~900 converted, ~25%
```

**~1.3x lift with statistically solid N.** Feature has real signal; the first
query was interrogating the wrong population.

## Notes

- **This is NOT the same as leakage.** Using `decision_at > target_date`
  at training time is correct — it identifies "progressors" without revealing
  the label (conversion is a later event). The pattern is: use gate-crossing
  AS A POSITIVE SIGNAL in analysis, not as a label.
- **Applies to any snapshot-based CRM/ERP pipeline.** Salesforce, HubSpot,
  Dynamics, Zendesk, and similar systems upsert current state without history
  by default. Any training table built from these will exhibit this pattern
  unless an SCD type-2 (history-preserving) source exists.
- **Illustrative case:** an initial analysis claimed a boolean
  `ready_to_review` flag was a no-signal feature based on 0% conversion across
  ~170K transitional-stage ("In-Process") person-dates. On pushback,
  re-analysis found ~1.3x lift in the NULL+submitted+decided-after-T bucket —
  the first query had simply interrogated the non-progressor slice.
- **Relates to an "annotate, don't wait" stance** — accept the snapshot
  limitation and annotate the bucket semantics rather than block on rebuilding
  full history. This skill is the diagnostic companion: even when you accept
  the limitation, feature-evaluation methodology must account for where
  progressors actually land in the training data.
- **Not every 0% bucket is this bug.** Legitimately-zero buckets exist
  (not-started populations well before the decision window, already-rejected
  applicants, etc.). The tell: check whether the bucket has enough N to be a
  meaningful subset of all progressors. If `N(bucket) * base_rate >>
  N(label=1, expected)`, you may be in this trap. If the bucket is rare and
  the zero is plausible, it's probably a real zero.

## References

- A project diagnostic where this bug surfaced: an initial "no-signal" verdict
  on a boolean sub-stratification feature, revised after pooling the
  NULL+submitted+decided-after-T bucket (~420K + ~3.6K person-dates) for a
  ~1.3x lift.
- The training SQL gate pattern to inspect: the `CASE WHEN decision_at <=
  target_date ... ELSE NULL` block in the point-in-time feature build — the
  exact pattern that creates the bucket ambiguity.
- Related pattern: a sister check for numeric-sentinel features (a sentinel
  value such as 999 or -1 masquerading as a real measurement), which has the
  same "wrong population in the named bucket" shape.
