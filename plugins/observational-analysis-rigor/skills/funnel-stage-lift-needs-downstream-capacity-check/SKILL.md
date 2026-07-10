---
name: funnel-stage-lift-needs-downstream-capacity-check
description: |
  Guard the step where a VALID early-funnel lift becomes a recommendation. A segment genuinely
  starts/converts more at stage N — and the write-up is about to say "prioritize this segment"
  as if that buys the END outcome (enrollment, purchase, activation). Use when: (1) a finding,
  headline, card, or play recommends tilting effort toward a segment based on a stage-N rate
  (lead→start, start→submit, signup→trial); (2) the favored segment funnels into a
  capacity-capped or selective downstream stage (limited seats/inventory/approval slots, an
  admissions committee, a review queue); (3) you are REWORDING or promoting an existing
  funnel-stage finding — the reword inherits the original's stage scope, so re-check it;
  (4) a stakeholder asks "but doesn't that program/product only take a few?". The lift can be
  real AND the recommendation still over-promise: extra stage-N entrants feed a bottleneck,
  not the end outcome. Distinct from cohort-milestone-lift-is-funnel-position-not-effect
  (which attacks the lift's VALIDITY); here the lift is valid — the question is what decision
  it licenses. Fix = check downstream capacity + segment-specific downstream yield, then state
  the claim's stage ON the artifact face, not in a collapsed footnote.
author: Claude Code
version: 1.0.0
date: 2026-07-10
disable-model-invocation: true
---

# A valid stage-N lift is not an end-outcome claim — check downstream capacity before recommending

## Problem
A segment shows a real, verified lift at an early funnel stage — say, prospects who name an
interest in a popular program area start applications at ~1.7× the base rate. The natural
write-up is "prioritize this segment." But to the reader, "prioritize them" silently means
"this buys more of the END outcome" (enrollments, purchases). If the favored segment funnels
into a **supply-constrained** downstream stage — selective programs admitting ~1 in 10 decided
applicants while open programs admit ~19 in 20, limited inventory, an approval quota — the
extra stage-N volume hits a wall the claim never mentioned. The finding is true; the implied
promise is not.

## Context / Trigger conditions
- Any recommendation of the form "tilt/lean/prioritize toward segment X" grounded in a
  lead→start, start→submit, or other early-stage rate.
- The favored segment concentrates in capacity-capped or selective downstream units.
- A REWORD or promotion pass on an existing funnel finding — rewording inherits the original
  stage scope, so the check must be re-run, not assumed.
- A stakeholder reads the claim and immediately asks about seats/capacity — treat that as the
  canary: if one reviewer went straight there, so will the audience.
- Trigger phrases: "these leads convert best, focus on them", "grow the X pipeline",
  "prioritize the high-interest segment", "this segment is our best top-of-funnel bet".

## Solution — the two-question downstream check (run BEFORE the framing ships)
1. **Supply constraint:** is the segment's destination capacity-capped or selective? Pull the
   downstream acceptance/allocation rate for the favored segment's units vs the open ones
   (e.g. accepts-per-100-decided by program). If the favored units sit far below the open
   ones, the stage-N lift buys **starts, not end outcomes**.
2. **Segment-specific downstream yield:** does the segment's accepted→committed /
   committed→completed conversion differ from baseline? Compute it per unit — and expect the
   bottleneck LOCATION to move: in one real case, two selective units lost ~2/3 of their
   admits *after* acceptance (high accept-side throughput, low yield), while two others were
   brutally selective *at* the accept step but their admits committed above average.
   "Selective ⇒ lower downstream yield" is an assumption, not a fact — check, don't infer.
3. **State the claim's stage on the artifact face.** If the caveat lives only in a collapsed
   drawer/appendix, a scanning reader reads the stage-N claim as an end-outcome claim. One
   line at the recommendation ("this grows application starts, not seats — the favored
   programs are capped; the yield side lives in the accepted→committed analysis") is the fix.
   Link the downstream analysis rather than duplicating its numbers (construct drift risk).
4. **Keep the lift and the promise separate in the action line.** The defensible action is
   stage-scoped ("use the interest signal as a priority marker for *completion nurture*"),
   not outcome-scoped ("this segment will drive enrollment").

## Verification
- The artifact face names the stage the claim lives at, adjacent to the recommendation.
- The downstream capacity/yield numbers cited come from a committed, re-runnable derivation
  (per data-provenance-verifier), not from memory or assumption.
- A cold reader (or a fresh-reader review pass) can answer "does this buy end outcomes?"
  without opening any drawer.

## Example
A dossier card recommended leaning outreach toward prospects naming a high-demand program
area (real stage-1 lift: ~1.7× application-start rate, verified). The downstream check found
the area's flagship program admits ~11 of 100 decided applicants (vs ~95 for open-admission
programs) — and, per-program, the post-acceptance yield SPLIT: two programs leaked ~2/3 of
admits after acceptance while two equally selective ones converted admits above average. The
shipped fix was one line on the card face — "this grows application starts, not seats" — plus
a pointer to the accepted→committed card for the yield half. The recommendation survived; the
over-promise did not.

## See also
- `cohort-milestone-lift-is-funnel-position-not-effect` — when the lift itself is suspect
  (composition/funnel position); run that first if the lift isn't yet verified.
- `conditional-funnel-by-segment-within-level` — locating WHERE in the funnel a segment acts.
- `differentiator-scoping-by-provenance-not-signal` — marker-vs-lever framing discipline for
  the surviving action line.
- `ship-the-correction-to-every-rendered-surface` — the added caveat must reach every surface
  that renders the recommendation (page, export, design copy).
