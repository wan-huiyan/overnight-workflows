---
name: pit-panel-first-appearance-left-truncation-incident-anchor
description: |
  TRAP — building a per-stage (or per-state) cohort from a DAILY POINT-IN-TIME entity×date
  feature panel by anchoring on each entity's FIRST PANEL APPEARANCE at that stage is a
  PREVALENT-cohort anchor: it is left-truncated and can FLIP THE SIGN of a real effect. Use when:
  (1) you measure "does attribute A (a completion flag, a status, a score) at stage S associate with
  outcome Y" by taking each entity's earliest panel row where status==S; (2) the result is
  surprising — flipped negative, or null — and CONTRADICTS a known benchmark for that stage;
  (3) a `days_since_<stage_event>` feature on those anchored rows has a huge median (the entity
  has been in the stage for months — caught mid-stage, not at entry); (4) the stage is "sticky"
  or residue-prone (deposited-not-enrolled, accepted-not-acted, churn-risk) so the prevalent pool
  is enriched for the stalled/melt subset. FIX: anchor at INCIDENT stage-ENTRY and validate
  against a benchmark. See also: exposure-sliced-by-stage-at-event-window-defined-by-outcome,
  cohort-milestone-lift-is-funnel-position-not-effect, ml-feature-pit-derive-from-anchor-else-ablation-upper-bound.
author: Claude Code
version: 1.0.0
date: 2026-06-25
disable-model-invocation: true
---

# PIT panel: first-appearance-at-stage is left-truncated → anchor at incident stage-entry

## Problem
A point-in-time training/feature panel has **one row per (entity, date)** with the entity's PIT
status on each date. You want "among entities at stage **S**, does attribute **A** mark outcome
**Y**?" The obvious anchor — *each entity's first panel row where status == S* — is a **prevalent**
cohort (everyone who is *in* S), not an **incident** cohort (everyone who just *entered* S). The
panel has a start date, so entities already deep in S at the start (or who entered S before the
panel) are caught **mid-stage** — **left-truncated**. For a sticky / terminal-ish / residue-prone
stage, the prevalent pool is enriched for the *stalled* subset, which **biases or flips the sign**
of A's real association with Y.

Concrete failure (real incident): "file-complete at the **Deposited** stage → enrollment," anchored
at first-deposited-row, gave a **large negative gap (~ −25pp)** (file-complete enrolled *less*) — the
opposite of the established deposit-stage marker (**~ +10pp**). The median `days_since_deposit` on those
rows was **~300 (nearly a year)**: "first deposited panel row" was landing months after deposit, on a
melt-enriched residue.

## Context / Trigger Conditions
- Daily PIT entity×date panel (e.g. an ML `*_training_features` table), per-stage/per-state cohort.
- Anchor = `MIN(date)` / `ROW_NUMBER()=1` over rows where `status == S` (first-appearance / prevalent).
- The measured effect is **flipped, null, or off** and **disagrees with a known benchmark** for that stage.
- A `days_since_<entry_event>` feature (even sentinel-filled when absent — e.g. 999) shows a **large
  median** on the anchored rows ⇒ entities sat in S long before the anchor ⇒ left-truncation.

## Solution
1. **Switch to an INCIDENT stage-entry anchor.** Order each entity's panel rows by date, take the
   first row where it **entered S from a strictly-lower observed stage**:
   ```sql
   stage_rank = CASE ... END  -- map status -> ordinal funnel rank
   seq AS (SELECT *, LAG(stage_rank) OVER (PARTITION BY entity ORDER BY date) prev_rank,
                     MIN(stage_rank) OVER (PARTITION BY entity) min_rank FROM staged),
   incident AS (
     SELECT * EXCEPT(rn) FROM (
       SELECT *, ROW_NUMBER() OVER (PARTITION BY entity, stage_rank ORDER BY date) rn FROM seq
       WHERE stage_rank = S
         AND ( prev_rank IS NOT NULL AND prev_rank < stage_rank   -- entered S from below
            OR (S = bottom_rank AND min_rank <= S) )              -- entry stage: caught at/before S
     ) WHERE rn = 1)
   ```
   Requiring a prior-observed lower stage **excludes left-truncated entities by construction**
   (anyone whose first-ever panel row is already at S is dropped — you never observed their entry).
   For the funnel's **entry** stage there is nothing below it, so allow `min_rank <= S` there and
   carry the caveat.
2. **A directly-available vintage gate is an equivalent, cheaper cross-check.** If a clean
   `days_since_<entry>` exists, `days_since_<entry> <= K` (fresh) reproduces the incident anchor.
3. **VALIDATE against a known benchmark.** Reproduce a published/prior effect for stage S on the
   incident cohort (here: the established ~ +10pp deposit-stage marker). **A miss is your signal the
   grain is still wrong** — don't proceed on a number that disagrees with a benchmark.
4. **Separately, gate the population correctly.** "Outcome-observable" (`label_valid_*`) is **not**
   "candidate" — restrict to the actual candidate set (e.g. `applied = TRUE`), or off-population
   rows with a structurally-0 outcome will deflate every rate. (Distinct bug, often co-occurs — see
   `ml-label-validity-joint-probe` § Variant.)

## Verification
- Incident-anchor effect **agrees in sign and magnitude** with the benchmark.
- Within-stratum (e.g. within one segment/level) the effect holds, ruling out a composition flip.
- The vintage gate (`days_since_<entry> <= K`) and the incident anchor give the **same** answer.

## Example
Deposit stage, real incident: first-appearance anchor → file-complete enrolls **~ −25pp** (left-truncated,
median `days_since_deposit` ≈ 300). Incident anchor (entered Deposited from a lower stage) → **~ +12pp**,
reproducing the established ~ +10pp deposit-stage marker; within-stratum ~ +9pp confirms it is not
composition. Same flip appeared at every funnel stage until the anchor was fixed.

## Notes
- This is epidemiology's **prevalent- vs incident-cohort** bias (left truncation / immortal-time-ish
  selection) in ML-panel clothing. The deeper/stickier the stage, the worse the prevalent bias.
- Don't confuse with the *composition* trap (raw lift that is funnel-position / a contaminated
  comparison group) — see `cohort-milestone-lift-is-funnel-position-not-effect`. Both can co-occur:
  fix the anchor first (incident), then adjust/stratify for composition.
- Don't confuse with defining the stage window from the **outcome's** own timestamps — see
  `exposure-sliced-by-stage-at-event-window-defined-by-outcome`.
- `days_since_*` features in these panels are often **sentinel-filled** (999/-1) when the event hasn't
  happened — `IS NOT NULL` is NOT a "happened" test; check the sentinel and use the real value only on
  rows where the event occurred (see `never-sentinel-satisfies-greater-than-bucket-predicate`).
