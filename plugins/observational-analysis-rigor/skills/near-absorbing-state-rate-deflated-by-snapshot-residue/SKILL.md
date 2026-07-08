---
name: near-absorbing-state-rate-deflated-by-snapshot-residue
description: |
  A "conversion / never-convert / progression" rate for a near-absorbing funnel state (created-not-
  submitted, abandoned-cart, stalled-ticket, dormant-lead, draft-not-finished) swings by orders of
  magnitude depending on WHEN you observe the pool, NOT on a data error. Observe each record at
  CREATION-time → high conversion (fast movers included). Observe the pool as it sits at a LATE
  SNAPSHOT date → near-zero conversion / ~99% "never move" — because the fast movers already drained
  out, leaving the dead-draft RESIDUE. Use when: (1) reconciling two or more "X% never convert"
  numbers that disagree wildly (e.g. under 1% vs ~65% vs ~90%), (2) a "99% never move / near-absorbing
  state" stat is quoted as the conversion baseline for an actionable cohort, (3) a denominator is
  "everyone currently in status S" read at one date. Fix: before calling the numbers contradictory,
  align them on ONE outcome and re-derive that outcome observed AT record-creation (the unconditional
  rate) vs at the late snapshot (the residue rate) on the SAME population — the gap IS the artifact.
author: Claude Code
version: 1.0.0
date: 2026-06-25
disable-model-invocation: true
---

# A near-absorbing state's "never-convert" rate is a snapshot-residue (observation-time) artifact

## Problem
You are handed several "X% never convert" numbers for the same funnel state and they disagree by
1–2 orders of magnitude (e.g. *"~99% of created-not-submitted records never move"* vs *"~65% never
submit"* vs *"~90% never progress"*). The instinct is "one of these is wrong" or "they contradict."
Usually **none is wrong** — they measure the rate after observing the pool **at different times**,
and a near-absorbing state's residue rate is dominated by *observation time*, not by the intervention.

The mechanism: in a state where most records move **fast** (e.g. median time-to-submit = 0 days,
half submit the **same day** they are created), the composition of "everyone currently in status S"
shifts violently with the observation date:
- **Observe at creation** (every record, the moment it enters S) → most convert → high rate.
- **Observe the pool sitting in S at a late snapshot date** → the fast movers already left; what
  remains is the **dead-draft residue** (the slow/abandoned) → near-zero conversion / ~99% "stuck."

So "99% never move" is **not the actionable stall rate** — it is the near-absorbing residue measured
at a late date. Quoting it as the conversion baseline for a cohort you intend to act on overstates the
loss and understates the reachable opportunity.

## Trigger conditions
- Two+ "never convert / never progress / never submit" numbers for the **same** state disagree by a
  large factor, and someone wants you to "pick the right one" / "reconcile before shipping copy."
- A **"near-absorbing state" / "99% never move"** statistic is being used as a card's conversion
  denominator.
- The state's time-to-next-step distribution is **heavily front-loaded** (median ≈ 0, big same-day
  spike) — the precondition that makes observation time dominate.
- A denominator is phrased "everyone **currently** in status S" (a late snapshot) rather than
  "everyone who **entered** S."

## Solution
1. **Decompose the disagreement on three axes before calling it a contradiction:** different
   **outcome** (deposit vs submit vs any-forward-move — a deeper outcome is always smaller), different
   **population** (whole-population vs reachable/web-linked vs re-observed subsample), and different
   **observation time** (at-creation vs late-snapshot). Two "contradictory" numbers usually differ on
   ≥2 axes and are simply different quantities.
2. **Prove the observation-time axis on ONE outcome, ONE population.** Re-derive the *same* outcome
   (e.g. ever-submit, from an immutable `submitted_at` stamp) two ways on the *same* population:
   - **at creation:** of all records ever created, what % ever convert (the unconditional rate).
   - **late-snapshot residue:** of records still in S at a late date D (`created ≤ D AND (converted IS
     NULL OR converted > D)`), what % ever subsequently convert.
   A large gap (66% vs ~2% is real) **is** the artifact — same population, opposite numbers, pure
   observation time. Check it is robust across several D values (not a cherry-picked date).
3. **Recommend the actionable grain, not the residue.** For an *intervention* card, headline the
   **reachable** cohort's rate at a **fixed term-relative window** (e.g. advance-within-60-days), and
   relegate the "99% never move" population number to **context**, explicitly labeled a snapshot-
   residue artifact — never the headline conversion baseline.

## Verification
- The at-creation vs late-residue rates differ by a large factor on the *same* population (smoking gun)
  and the gap holds across multiple snapshot dates.
- Each disagreeing source number, once you state its (outcome × population × observation-time) tuple,
  reproduces — so they were three quantities, not three estimates of one.
- An independent re-derivation on the immutable event stamp reproduces both ends of the contrast.

## Example (application funnel, verified on live event data)
An "application-started, not submitted" card had 3 conflicting "never-submit" numbers. Re-derived
live on the submit outcome from the immutable `submitted_at` stamp:
- **At creation** (all ~99k created records): **~two-thirds ever submit** (over half of submitters
  submit the same day; median 0 days) → ~one-third never.
- **Late-snapshot residue** (~30k records still not-submitted at a late snapshot date): **~2% ever
  submit / ~98% never** — robust across several snapshot dates (all landing in the low single digits,
  roughly 2–8%).
- The prior "~99% never move" figure was exactly this residue (a deeper *deposit*-outcome variant).
- Decision: the card headlines the **reachable stalled cohort, ~28% advance@60** (reachable
  population, fixed window); the ~99% residue number is context only. The >30× creation-vs-residue gap
  *is* the proof it was an observation-time artifact.

## Notes
- **See also** [[snapshot-churns-out-one-outcome-class-biases-the-rate]] — the *adjacent* snapshot
  trap, but a **different mechanism**: that one is cross-outcome-class retention bias (denials churn
  out → inflated accept rate). This one is observation-time selection within a *single* outcome
  (the residue of a fast-draining pool). Both: "don't trust a rate read off current state."
- Related: [[null-status-with-engagement-conflates-never-vs-form-stall]] (a NULL/stuck status can mean
  "form-stall," not "never"), [[trailing-window-population-decline-seasonal-vs-real]] (a trailing window
  shifts pool composition over time), [[descriptive-universe-vs-action-worklist-cohort-size]] (whole-
  population vs reachable-worklist — the *population* axis of step 1).
- The same trap hits: abandoned-cart "X% never buy" (observe at add-to-cart vs at a stale-cart sweep),
  support "X% never resolved" (observe at open vs at a backlog snapshot), lead "X% never engage."
  Whenever most records move fast and you read the rate off the residual pool, you measure the residue,
  not the funnel.
