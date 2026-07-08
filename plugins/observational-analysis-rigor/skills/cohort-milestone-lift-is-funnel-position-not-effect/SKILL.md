---
name: cohort-milestone-lift-is-funnel-position-not-effect
description: |
  Diagnose the trap where a cohort defined by a LATE-funnel milestone (aid application filed,
  application submitted, deposit paid, event attended, document received) shows a strong raw
  outcome "lift" — and you're about to frame a client/dashboard action ("these convert
  higher, target/nudge them"). Use when: (1) a cohort's raw conversion/deposit/enrollment
  rate is much higher than a comparison group (+5–25pp) and the cohort membership is itself
  reached only AFTER getting partway down the funnel; (2) the comparison ("non-X") group is
  contaminated by a population that structurally CAN'T have the milestone (e.g. non-domestic
  applicants can't file a domestic aid form); (3) someone proposes a new feature or a "high-yield"
  nudge card off that gap. The raw gap is composition + funnel position, not a causal effect of
  the milestone. Decisive test = matched-grain stratification holding the confounders fixed in
  BOTH arms; sign-instability across strata is the fingerprint of residual composition; beware
  the seductive positive cut that conditions ONE arm on a near-outcome proxy. Product fix: frame
  as process-eligibility ("ready for X"), not yield. See also:
  predicted-score-cohort-comparison-flips-on-realized-backtest,
  marginal-lift-collapses-on-pre-event-temporal-restriction, ml-bug-blast-radius-natural-baseline-conflation.
author: Claude Code
version: 1.0.0
date: 2026-06-01
disable-model-invocation: true
---

# A late-funnel-milestone cohort's raw "lift" is funnel position + composition, not effect

## Problem
You're asked to surface a cohort defined by a milestone that members reach only after
progressing down the funnel — "aid application completed", "application submitted", "deposited",
"attended an event". The cohort's raw outcome rate dwarfs the comparison group (e.g. aid-
complete enroll ~11% vs ~5%, deposit ~70% vs ~46%). It's tempting to frame a client action
around it: "these convert higher — prioritize/nudge them." That framing is almost
always wrong, because membership in the cohort is *downstream of* the very outcome you're
crediting it for. This error class has produced retracted findings and published lifts that
failed to reproduce on re-analysis, for exactly this reason.

## Context / Trigger conditions
- A cohort filter where the defining event happens late (post-application, post-decision).
- A raw outcome gap that "looks great" and someone wants to ship/feature/nudge on it.
- The "non-cohort" comparison group silently includes a population that *cannot* have the
  milestone (non-domestic applicants can't file a domestic aid form; no-application members
  can't have a decision date).
- Trigger phrases: "aid-form completers convert way higher, let's target them", "deposited
  members enroll at 95% so deposit is our best signal", "event attendees are 5× — build the
  card", "add a <milestone> feature, the lift is huge".

## Solution — the decisive matched-grain test (do this BEFORE framing any action)
1. **Name both confounds.** (a) Composition: does the comparison group include a sub-pop that
   structurally can't have the milestone? (b) Funnel position: is the milestone reached only
   after a funnel step the cohort therefore over-represents?
2. **Hold them fixed in BOTH arms and recompute.** Restrict both the cohort and the comparison
   to the same composition slice (e.g. `residency_status IN ('C','R')` in both) AND the same
   funnel stage (e.g. `application_stage = 'accepted'` in both), then recompute the gap. If it
   collapses toward zero or flips sign, the raw gap was composition/position, not effect.
3. **Check sign stability across strata.** Compute the within-stage gap in several stages. If
   the sign is unstable (e.g. −2 / −12 / +9 pp), that instability *is* the fingerprint of
   residual composition — not a real effect in any direction.
4. **Distrust the seductive positive cut.** If the only cut that stays strongly positive does
   so by conditioning on a variable that (a) only exists for the cohort arm and (b) is a near-
   proxy for the outcome (e.g. `aid_record_year = '<current-cycle>'` ≈ "live currently-enrolling
   record"), that's asymmetric outcome-conditioning — the retracted-finding error class. Reject it.
5. **Use realized labels, not predicted scores**, and check whether the model already prices
   the milestone: if the matched-grain *score* gap vanishes/reverses, no new feature is
   warranted (see predicted-score-cohort-comparison-flips-on-realized-backtest).
6. **Reframe the product action.** If there's no defensible lift, frame the cohort as a
   **process-eligibility fact** ("Accepted, aid application on file: ready for aid-award
   packaging") — true by construction, needs no yield claim — NOT as a high-converter target.
   Add a build guardrail: copy must not imply "prioritize because they convert", or it collapses
   back into the rejected narrative. Tighten the cohort to the operational queue (exclude
   already-past-decision rows; current cycle only).

## Verification
- Raw gap large; matched-grain (both-arms) gap ≈ 0 or sign-unstable → confirmed composition.
- The strong positive survives ONLY under asymmetric/outcome-adjacent conditioning → reject.
- Realized-backtest and predicted-score orderings disagree → cite neither as a yield effect.

## Example (aid-application-completed cohort)
Raw: aid-complete (`has_aid_doc=1` within an aid-eligible slice) converts at a visibly higher rate
than incomplete (say ~11% vs ~5%, a +6pp gap). But holding the aid-eligible slice fixed in both arms
collapses the gap to ≈0 (even slightly negative), and within a fixed application stage the sign is
unstable (it flips positive/negative across strata). The apparent lift was further inflated by a
downstream-conditioned proxy: the `has_aid_doc=1` arm was implicitly conditioned on a *current-cycle*
aid record (a liveness proxy the incomplete arm never gets — members tied to a stale record converted
at nearly zero). Verdict: no defensible lift; the model already prices aid-completion (matched-grain
score gap ≈ 0). Correct framing = aid-packaging readiness (process-eligibility), with the cohort
tightened to current-cycle still-open records.

## Notes
- The two confounds compound: strip composition first (it often does most of the work), then
  funnel position.
- "Deposited → 95% enrollment" is the purest instance: deposit is almost the outcome.
- This is the inverse framing discipline to null-bucket-hides-progressors-in-snapshot-training
  (which is about the cohort DEFINITION hiding signal); here the cohort definition manufactures
  a spurious one.

## References
- Internal analysis brief (RESULTS section).
- Companion skills: predicted-score-cohort-comparison-flips-on-realized-backtest;
  marginal-lift-collapses-on-pre-event-temporal-restriction; ml-bug-blast-radius-natural-baseline-conflation.
