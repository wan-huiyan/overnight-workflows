---
name: conditional-funnel-by-segment-within-level
description: |
  Methodology for breaking a multi-stage conversion FUNNEL down by a SEGMENT to find WHERE in the funnel the
  segment acts (not just whether it correlates with the final outcome). Use when: (1) asked to break a funnel —
  signup→activate→subscribe, applied→submitted→accepted→deposited→enrolled, cart→checkout→purchase — down by a
  segment dimension (geography, scholarship/plan tier, acquisition channel/source, campaign-touch, engagement
  band, plan type, cohort timing); (2) someone is about to headline a POOLED/MARGINAL "segment X converts more"
  number; (3) the segment value is correlated with a dominant axis (account level, product line, plan) so the
  pooled number could be composition; (4) the segmenting attribute might itself be recorded/awarded only AFTER
  reaching a downstream stage. Three things this prevents: (a) reporting a pooled segment→outcome rate that is
  really level/program COMPOSITION (read WITHIN the dominant stratum instead); (b) missing that the segment's
  whole effect concentrates at ONE transition (e.g. deposit|accepted) while submit/accept are flat — you only
  see that by computing CONDITIONAL transition rates, not the cumulative marginal; (c) the structural artifact
  where an attribute conditioned on a downstream stage (a merit/discount tier awarded at acceptance, a
  "syndicated-apply source" that IS the submission act, an "aid-application-complete" flag) reads ~100% at every
  UPSTREAM gate BY CONSTRUCTION, so its upstream cells are
  meaningless. See also: cohort-milestone-lift-is-funnel-position-not-effect (the milestone-cohort composition
  trap), within-stratum-residual-event-floor-anchor-split (de-confounding a single ranked metric; events are the
  binding floor), funnel-lever-vs-predictor-deleaked-forward-gap (turning the same cut into actionable levers).
author: Claude Code
version: 1.0.0
date: 2026-06-25
---

# Conditional-funnel-by-segment, within level

## Problem
"Break the conversion funnel down by <segment>" almost always gets answered with a **marginal** number —
`P(final outcome | segment value)` — which (a) hides WHERE in the funnel the segment acts and (b) is usually
**composition** (the segment is correlated with a dominant axis like account level or product line). Both lead
to confident wrong headlines: "last-minute applicants enrol more", "scholarship students enrol less", "this
channel is our best" — when the real story is a level mix, or a single-transition effect, or a construction
artifact.

## Context / Trigger conditions
- A multi-stage funnel + a request to cut it by a segment dimension.
- You're about to report `P(enrol|segment)` / `P(convert|segment)` pooled across the population.
- The segment is plausibly correlated with a dominant stratum (level, plan, product, vintage).
- The segmenting attribute might be **set as a consequence of** a funnel stage (awarded, derived, or = the act).

## Solution — three rules

**1. Report CONDITIONAL transitions, not the cumulative marginal.** Decompose the funnel into per-step
conditional rates, each given the prior stage:
`submit|applied · accept|submitted · deposit|accepted · enrol|deposited` (+ keep the marginal `enrol|applied`
as a *separate, clearly-labelled* headline). The conditional decomposition is what reveals **where the segment
bites**: a segment can leave submit/accept flat and swing one transition hard. (Empirically, commitment/intent
segments concentrate almost entirely at the *last* discretionary step — deposit/purchase — because that's where
commitment is revealed; submit/accept are near-flat.)

**2. Read WITHIN the dominant stratum — the pooled/marginal is composition.** Always cut by level/product/plan
*and* the segment. The pooled marginal mixes strata with very different base rates and timing, so a pooled
"segment X converts more" is usually the stratum mix, not the segment. Show pooled only to *expose* the trap;
the within-stratum cells are the finding. (This is the funnel-decomposition sibling of
`within-stratum-residual-event-floor-anchor-split` and `cohort-milestone-lift-is-funnel-position-not-effect`.)

**3. Flag attributes CONDITIONED on a downstream stage — they read ~100% at upstream gates by construction.**
Before trusting a segment's upstream conditional rates, ask: *is this attribute recorded/awarded/derived only
after reaching some stage S?* If yes, every gate at or before S is ~100% **by construction**, not behaviour:
- **merit / discount tier** is awarded *at acceptance* → `submit|applied` and `accept|submitted` = 100% for
  every award tier (they were all accepted to get the award). Only `deposit|accepted` and `enrol|deposited` are
  interpretable.
- **application source = a syndicated-apply platform** → `submit|applied` = 100% because that platform's
  submission *is* the application.
- **"aid-application-complete" / "received document X"** flags are partly downstream too — interpret the gates
  *after* the flag can first be true.
Read ONLY the cells downstream of where the attribute is determined; the upstream 100%s are a tautology, not a
high-performing segment.

**Floors & framing:** the binding constraint is usually the *events* (numerator) in the deepest cell, not the
population — floor on `≥15` outcome events per arm and suppress+log thin cells. These cuts are **📊 descriptive /
selection-prone, NOT levers** (you can't assign a customer a geography or a scholarship tier to move them) —
frame as targeting/prioritisation, and gate any causal/lift claim behind a holdout.

## Verification
- The within-stratum cells should *disagree* with the pooled marginal if composition was present (if they agree,
  composition wasn't the issue — say so).
- One conditional transition should carry most of the segment's effect; name it.
- Re-derive one cell with a standalone GROUP BY query (bypass any pandas/pipeline funnel logic) — the conditional
  rates must match exactly. (A conditional-funnel bug is easy to introduce in the numerator/denominator pairing.)
- For any 100% upstream cell, confirm it's a construction artifact (the attribute is downstream-conditioned),
  not a real perfect-conversion claim.

## Example (a multi-stage enrollment funnel)
Cut the application funnel by application-creation timing and ~8 other segments, within level:
- **Pooled** "last-minute applicants enrol more" (the pooled enrol rate fell several-fold as lead time grew) was
  **composition** — the very-early (>1-year lead) bucket was dominated by a selectivity-loaded sub-population
  (accept|submitted only ~20-25%).
- **Within the dominant level**, the real signal appeared at one transition: `deposit|accepted` rose sharply
  toward the deadline (roughly 40% for last-minute vs under 10% for early); submit/accept were flat. Same shape
  for campaign attendance (deposit|accepted several-fold higher for attendees), engagement breadth, aid status.
- **Structural artifacts flagged:** merit tiers all showed `submit|accept = 100%` (awarded post-acceptance); a
  syndicated-apply source showed `submit = 100%` (it *is* the submission) — only their deposit cells were read.
- **Revived nulls:** distance & award were "null for the final conversion" pooled, but the *conditional
  within-level* funnel showed a clean near>far deposit gradient and a higher-award→lower-deposit
  (competitive-admit selection) one.

## Notes
- Keep the **denominator lens** explicit: cumulative cycle outcomes (`ever-X`) ≠ windowed rates (`X within N
  days`) — don't place a cumulative `deposit|accepted` next to a windowed `deposit@60d` as if the same quantity.
- This is the multi-transition extension of single-metric within-stratum de-confounding; reuse that skill's
  event-floor discipline.
- Aggregate-only on protected attributes; a low-conversion subgroup (e.g. a specific demographic segment) is a
  fairness watch-item, reported as composition, not an individual cut.

## References
- Sibling skills: `cohort-milestone-lift-is-funnel-position-not-effect`,
  `within-stratum-residual-event-floor-anchor-split`, `funnel-lever-vs-predictor-deleaked-forward-gap`,
  `two-lever-synergy-scale-dependent-and-simpson-ceiling`.
- Reference implementation: a reusable per-segment conditional-funnel runner (`analyze.py`) that computes each
  conditional transition (`submit|applied`, `accept|submitted`, `deposit|accepted`, `enrol|deposited`) via a
  standalone GROUP BY, cut both pooled and within-level.
