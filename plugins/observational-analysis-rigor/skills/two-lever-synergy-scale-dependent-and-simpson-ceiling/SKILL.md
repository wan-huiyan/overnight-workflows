---
name: two-lever-synergy-scale-dependent-and-simpson-ceiling
description: |
  Use when testing whether two binary levers/signals SYNERGIZE — i.e. any "do A and B together beat the sum
  of their parts" / "super-additive interaction" / "compound effect" / "do these stack" question on a 2×2 of
  a binary outcome (enroll, convert, retain, click). Two traps that flip the verdict: (1) "super-additive" is
  SCALE-DEPENDENT — a real +Npp gap on the risk-difference (probability) scale can be exactly ZERO on the
  odds/logit scale (the levers stack multiplicatively, no interaction), so a blanket "synergy" or "no synergy"
  claim is wrong without naming the scale; (2) a pooled difference-in-differences that "DISSOLVES under a
  confounder control" can be a SIMPSON'S-PARADOX artifact when one stratum sits near a rate CEILING (~50%+) and
  therefore carries a spurious NEGATIVE additive interaction that cancels a real positive one in another stratum.
  Trigger phrases: "is the combination super-additive", "do the two signals interact", "stack / compound /
  synergy", "the interaction dissolves under controls", "joint cell beats the additive baseline". The fix: fit
  logistic for the odds-scale interaction AND report the additive contrast AND stratify by the big confounder
  before pooling; report the high-yield SEGMENT (the BOTH cell's rate) separately from the SYNERGY claim.
  See also: wide-signal-sweep-confirm-budget-is-events-not-compute, within-stratum-residual-event-floor-anchor-split,
  incremental-auc-baseline-must-refit-both-sides-not-raw-score.
author: Claude Code
version: 1.0.0
date: 2026-06-23
---

# Two-lever synergy is scale-dependent — and a pooled DiD can hide a Simpson ceiling

## Problem
You have a 2×2: two binary levers A and B, a binary outcome. The BOTH cell looks high and someone asks "is it
**super-additive** — do A and B synergize?" The naive test (observed BOTH rate minus additive prediction) gives a
positive gap and you're tempted to call it synergy. Two independent traps make that verdict unreliable, and they
pull in opposite directions, so you can talk yourself into *or* out of a real effect.

## Context / Trigger conditions
- A "do two signals/levers stack / compound / synergize / beat the sum of parts / show a super-additive interaction" question.
- A 2×2 (or factorial) on a binary outcome where the BOTH cell is well-populated.
- You're about to report "super-additive synergy" — or about to dismiss it because "it dissolves once I control for X".
- One arm/stratum has a high base rate (≳40–50%) — ceiling territory.

## Solution
1. **Name the scale — synergy is not scale-free.** Compute BOTH:
   - **Additive / risk-difference:** `DiD = (p11 − p10) − (p01 − p00)`, with `SE = sqrt(Σ p(1−p)/n)` over the 4 cells.
   - **Multiplicative / odds:** fit `logit(y ~ A * B)` and read the `A:B` interaction term.
   A positive additive DiD with an odds-scale interaction ≈ 0 means the levers stack **multiplicatively** (independent
   on the odds scale) — that is NOT "super-additive synergy," it's "two independent levers." Sanity check: a
   *no-interaction* logit's predicted BOTH rate will match the observed BOTH rate almost exactly when it's truly multiplicative.
2. **Stratify before you pool — guard against the Simpson ceiling.** Before concluding "the interaction dissolves under
   control for confounder C," fit the interaction WITHIN each level of C. A stratum whose base rate is near a ceiling
   *cannot* stack additively (you can't add +12pp to a 45% rate the way you can to a 7% one), so it tends to show a
   **negative** additive interaction that cancels a real positive one from a low-base stratum in the pool. A pooled
   interaction that flips to null under a control, where the control's strata straddle a ceiling, is a Simpson artifact —
   not evidence the synergy is confounded away.
3. **Separate the SEGMENT from the SYNERGY.** The robust, decision-relevant fact is almost always the **segment**: the
   BOTH cell's yield (e.g. "~40% vs ~9% baseline, powered out-of-sample"). Report that as the finding. Report the
   **synergy** (super-additivity) separately and honestly — it is usually borderline and scale-dependent, and you
   rarely need it to act (the targeting list is the value, not the synergy).

## Verification
- Re-fit the no-interaction logit and confirm its predicted BOTH rate ≈ observed BOTH rate (→ multiplicative, odds-interaction ≈ 0).
- Confirm the additive DiD's sign/significance WITHIN the low-base stratum, not just pooled.
- Check whether any stratum's base rate is ≳45% before trusting a pooled "dissolves under control".

## Example (illustrative)
2×2 = campaign-attendance × recent-browsing within an accepted cohort, each entity counted once. Cells (illustrative
round numbers): neither ~9%, recent-only ~19%, attended-only ~21%, BOTH ~40%. Additive DiD ≈ +8pp (p≈0.05) → looks
super-additive. BUT `logit(y ~ A*B)` interaction ≈ 0 (p ≈ 0.85–0.9), and a no-interaction logit predicts the BOTH cell
at ~37% vs ~37% observed → **multiplicative, not super-additive**. The pooled DiD "dissolved" to ~+3pp (p≈0.3) under a
segment control — but that was a **Simpson ceiling artifact**: the high-base segment (base ~25%, BOTH ~50%, near the
ceiling) carried a ≈ −12pp additive interaction that cancelled the low-base segment's real ≈ +8pp (p≈0.05, base ~7%).
Honest report: a confirmed high-yield **segment** (~40% vs ~9%, holds on holdout, within both segments, under an
alternate recency definition); levers stack ~multiplicatively; a modest borderline additive bump survives only in the
ceiling-free low-base segment.

## Notes
- This was surfaced by an adversarial verifier re-deriving from raw — exactly the kind of scale/Simpson error a single
  pass misses. When a synergy claim is load-bearing, have an independent agent re-fit on both scales and stratify.
- "Multiplicative" (constant odds ratio for B regardless of A) is the usual null for independent levers; report it as
  "they stack independently," not "no effect."
- The segment can be operationally great even with zero synergy — don't let a null interaction bury a real targeting cohort.
