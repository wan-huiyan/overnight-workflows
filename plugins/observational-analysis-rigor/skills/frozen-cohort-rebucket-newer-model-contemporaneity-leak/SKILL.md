---
name: frozen-cohort-rebucket-newer-model-contemporaneity-leak
description: |
  Diagnose the trap where you A/B two MODEL VERSIONS by re-bucketing the SAME frozen-cohort
  outcome matrix (a dashboard/funnel matrix that freezes each unit's bucket at its first score
  since date X) with a NEWER model's scores, and the newer model looks dramatically better at
  sorting a downstream/forward outcome. Use when: (1) a prompt/plan calls re-scoring a live
  cohort with the new model and recomputing a transition/conversion matrix a "clean A/B" between
  versions, (2) the new model's top bucket hits ~100% (or the bottom ~0%) on a FORWARD transition
  — a near-perfect monotone gradient (e.g. 1.0/0.94/0.43/0.04), (3) the two versions occupy
  DISJOINT time windows because the cutover was a one-time switch, so the newer model was only
  ever scored LATER — contemporaneous with (or after) the outcome it's being "graded" on. The
  newer score reads PIT/current-state features that REFLECT the realized outcome (deposit/convert
  status already set) → it is reading the outcome, not predicting it. A 100% forward rate on a
  real cohort is the tell. Three confounds stack: disjoint windows, ~0 prediction horizon
  (contemporaneity), and survivorship (the newer cohort is a smaller,
  terminalized-records-dropped subset). The honest comparison needs BOTH models scored at a
  MATCHED information cutoff
  predicting a FORWARD realized outcome (feasible if the new model is PIT: reconstruct early-date
  features and score offline). Trigger phrases: "re-bucket the cohort with the new model", "score
  the same units with the new model and compare the funnel", "v_new differentiates mid-funnel way
  better", "the new model's High bucket converts at 100%".
author: Claude Code
version: 1.0.0
date: 2026-06-19
disable-model-invocation: true
---

# Re-bucketing a frozen-cohort matrix with a newer model leaks via contemporaneity

## Problem

A dashboard shows a **frozen-cohort outcome matrix**: each unit's bucket is frozen at its
**first score on/after a start date**, and you grade each bucket on a **downstream/forward
transition** (funnel progression, conversion, deposit, retention). You notice the buckets sort
the *top* of the funnel strongly but *compress* after some milestone, and you hypothesise the
newer model version fixes it. The proposed test — and it sounds clean — is: **re-bucket the same
cohort with the newer model's scores and recompute the same matrix.**

Done naively this produces a *spectacular but spurious* result: the newer model's top bucket hits
**~100%** on every forward transition and the bottom bucket ~0% — a far bigger spread than the old
model. It reads as "the new version differentiates the mid-funnel N× better." **It does not. The
new version is reading the outcome, not predicting it.**

## Why it's a leak, not skill

The version cutover was a **one-time switch on a date**, so the two versions occupy **near-disjoint
time windows**. The old model scored the cohort *early* (a genuine forward forecast — it could not
see the future outcome). The new model only ever scored the cohort *late* — **contemporaneous with,
or after, the outcome** you're grading it on. If the new model reads point-in-time / current-state
features that encode the realized outcome (deposit status, conversion flag, stage-reached), then a
unit that already converted gets a high new-model score *because it converted*, and you then
"measure" that it converted. Circular.

Three confounds stack — any one alone breaks the comparison:

1. **Disjoint time windows** — "v_old vs v_new" is partly "early vs late."
2. **Prediction horizon ≈ 0 (contemporaneity)** — the old bucket forecasts months ahead; the new
   bucket is graded against a same-period outcome it can already see.
3. **Survivorship / coverage** — the newer cohort is usually a smaller subset (terminalized units
   get suppressed/NULL buckets in current scoring), with a different outcome-stage mix.

## The tell

- **A ~100% (or ~0%) forward rate on a real cohort.** No honest forward predictor hits 100% on a
  real population — that only happens when the score is reading the realized label. A clean monotone
  gradient like `1.00 / 0.94 / 0.43 / 0.04` is a *leak gradient*, not a sorting-power gradient.
- **Disjoint version date-spans** (print `MIN/MAX(score_date)` per `model_version` — they won't
  overlap if the cutover was a switch).
- **Direct check:** for the new model's top bucket, what fraction were *already at the outcome
  state at their new-model scoring date*? If it's ~100%, the model is reading status, not forecasting.

## Solution

1. **Refuse the naive re-bucket as a version comparison.** It cannot separate model skill from
   contemporaneity. Do not ship any "v_new improved mid-funnel sorting" claim derived from it,
   especially on a client-facing / regulated surface.
2. **Keep the old model's columns — they're the clean half.** The old (early) bucket → (late)
   outcome IS an uncontaminated forward measure (the early score couldn't see the future). If it
   shows mild/weak sorting on a transition, that's a *real* finding (the model genuinely struggles
   there), and it MOTIVATES the hypothesis without confirming it.
3. **Decompose the "compression" before blaming the model** (don't let "structural" launder away the
   real gap either):
   - **Ceiling transitions** (≈ everyone makes the step, e.g. 98% pooled) are *unsortable by any
     model* — flat-across-buckets is structural, not a version defect.
   - **Wrong-target transitions** (driven by a different decision-maker, e.g. an approval/gatekeeper
     step) are expected to be weakly sorted by a propensity model.
   - **The genuine open transition** (the one the model is actually supposed to predict, with real
     variance) is where the hypothesis is *alive* — and exactly where the leaky re-bucket cannot
     answer it.
4. **The only clean test = matched-horizon controlled backtest.** Score BOTH versions at the SAME
   information cutoff (an early anchor date with a real forward window), restricted to the population
   for whom the transition is defined, predicting the FORWARD realized outcome (from a closed-cycle
   outcomes table). Compare High–Low spread / monotonicity / sub-population AUC. **If the new model
   is PIT, this is feasible offline:** reconstruct the early-date features and score the new model at
   that horizon (the old model's early scores already exist).

## Verification

- Per-version `MIN/MAX` score-date printed and shown disjoint.
- No forward rate is ~100%/~0% in any comparison you treat as "model skill."
- The contemporaneity probe (% of new-top-bucket already at outcome state at scoring date) is run
  and reported.
- Any version-skill claim is sourced from a matched-horizon backtest, not observational re-bucketing.

## Example

An enrollment-funnel propensity project (an anonymized engagement). A live dashboard's funnel
matrix freezes each unit's bucket at its first score on/after a cycle-start date (~97% of buckets
assigned by the **old snapshot-feature model**; prod had since cut over to a **newer PIT-feature
model** a few weeks earlier). Proposed "clean A/B": re-bucket the cohort with the newer model and
compare the post-application funnel columns.

Naive re-bucket (newer model's latest score vs the same funnel stage; illustrative numbers):

| Bucket | Accepted→Deposit |
|---|---|
| High (n≈100) | **~1.00** |
| Mid-high (n≈300) | ~0.94 |
| Mid-low (n≈1,300) | ~0.43 |
| Low (n≈4,800) | **~0.04** |

A ~0.96 High–Low spread vs the old matrix's ~0.10 — "the new model sorts the yield ~9× better!"
**Spurious.** The old model scored the cohort early over a multi-month window; the new model only
scored it in a later, near-disjoint window. Direct probe: **~all of the new-model High bucket were
already at Deposited-or-beyond at their new-model scoring date** — the model read realized deposit
status (PIT current-state features such as `days_since_deposit` / `current_stage_flag`), it did not
forecast it. The newer cohort was also only ~half the size of the early cohort (survivorship). The
clean old-model columns *did* show the yield transition (Accepted→Deposit) is weakly +
non-monotonically sorted — a real, well-motivated gap — but whether the new model closes it can only
be answered by a matched-horizon offline backtest (filed as a gated follow-up).

## Notes

- **Sister skills:**
  - `observational-version-comparison-confounded-by-time` — the general parent (any version metric
    from usage logs is time-collinear). This skill is the specific *frozen-cohort re-bucket +
    forward-outcome contemporaneity* instance, with the ~100%-rate tell.
  - `predicted-score-cohort-comparison-flips-on-realized-backtest` — adjacent but DIFFERENT root
    cause (comparing two *cohorts* by predicted score; flips because the model overweights a prior
    version's feature pattern). Here the issue is *contemporaneity leakage* of a *newer* model on a
    forward outcome, not feature-weight inheritance.
  - `cohort-milestone-lift-is-funnel-position-not-effect`, `derived-table-outcome-flag-frozen-at-write-time`.
- **PIT cuts both ways.** The same PIT feature that makes the new model leak when scored *late*
  (it reads current deposit status) is exactly what makes the clean backtest *feasible* when scored
  *early* (you can reconstruct the early snapshot). PIT enables the honest test; contemporaneity is
  the trap.
- **Guardrail:** the matched-horizon backtest should be **offline scoring only** — don't rebuild the
  training table or trigger a retrain to run it if an auto-promote scheduler is armed.
