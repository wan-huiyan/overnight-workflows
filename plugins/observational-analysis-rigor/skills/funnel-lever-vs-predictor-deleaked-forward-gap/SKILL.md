---
name: funnel-lever-vs-predictor-deleaked-forward-gap
description: |
  Methodology for "what can we DO to move users from funnel stage A to stage B" analyses where the deliverable
  is a list of ACTIONABLE LEVERS (interventions), not just correlates. Use when: (1) a client/PM asks "how do we
  get more people to advance from <stage A> to <stage B>" (stalled→submitted, signup→activate, trial→paid, cart→checkout)
  and you must recommend interventions; (2) you have a behavior/event table and are about to rank levers by raw or
  adjusted lift on the transition; (3) the a-priori "strongest" lever is itself part of the transition act (uploading
  a doc to submit, completing a checklist that IS submission) so its lift is the act-in-disguise; (4) a behavior
  shows a NEGATIVE or null RAW association that flips POSITIVE once you control stage/level (Simpson). The fix is a
  DE-LEAKED FORWARD-GAP TEST: measure the predictor in an EARLY window strictly before the outcome window, measure the
  outcome in a LATER gap-separated window, AMONG users still un-transitioned at the gap boundary — a surviving effect
  is a genuine forward driver (a lever), one that evaporates is a predictor/intent-marker (use for targeting, not as
  the intervention). Also covers the intervention-vs-intent-marker classification and the by-stage Simpson check;
  and (5) VERIFYING a shipped "N× more likely if they do Y AFTER event X" claim — if the probe SQL has no join to
  each unit's OWN event-X date, the claim is a whole-window read mis-described; rebuild on the own-anchor
  (censored-window primary + fixed forward-gap robustness + specific-vs-generic conditional control; never ship
  the pooled lift — segment composition Simpson-inflates it). A volume/engagement-stratified ablation PASSING does
  NOT clear such a claim — volume strata cannot control cohort-vintage composition; only the own-anchor read
  adjudicates; and (6) the anchor is a REWRITABLE business-milestone date (a decision/status date overwritten by
  re-decisions) that can POSTDATE the outcome — probe the outcome−anchor gap FIRST; if the outcome frequently
  precedes the anchor a post-milestone forward-ordered design is structurally INAPPLICABLE (not just biased),
  and an "engagement ≤ anchor" window LEAKS post-outcome activity; re-anchor on an immutable-upstream event.
  See also: marginal-lift-collapses-on-pre-event-temporal-restriction (sister: pre/post-creation leakage for FEATURE
  selection), cohort-milestone-lift-is-funnel-position-not-effect, within-stratum-residual-event-floor-anchor-split,
  exposure-sliced-by-stage-at-event-window-defined-by-outcome (the outcome-defined-window biases the own-anchor
  variant's paired design is built to dodge).
author: Claude Code
version: 1.6.0
date: 2026-07-07
---

# A funnel-progression "lever" must survive a de-leaked forward-gap test — else it's a predictor, not an action

## Problem
You're asked "what can we DO to move more users from stage A → stage B?" and you build a lever table: for each
behavior, the rate of advancing is higher when the behavior is present. You're about to hand the stakeholder a
recommendation list. The trap: **the highest-lift behaviors are often the transition act itself in disguise.**
Uploading a required document, completing the final checklist item, clicking "submit-adjacent" CTAs — these correlate with
advancing because they ARE part of advancing, measured in a window that ends at/just-before the observation anchor.
Recommending "get users to upload a document" then reads as "get users to do the thing that means they already
advanced" — a tautology, not a lever. A naive lift table cannot tell a true lever from this.

## Context / trigger conditions
- Deliverable is **actionable interventions** for a funnel transition, not just feature importance.
- Behavior/event windows are measured relative to an anchor, with the outcome = "advanced within W after anchor."
- The a-priori "obvious" lever is mechanically entangled with the transition (document upload ↔ submit;
  checklist-complete ↔ submit; "add to cart" ↔ checkout).
- A behavior's **raw** association is negative/null but **adjusted** (controlling stage/level) is positive — a
  Simpson reversal (the behavior concentrates in a low-base-rate stratum).
- Trigger phrases: "what moves them from X to Y", "what can we DO", "which behaviors drive submission /
  activation / conversion", "give me the levers", "abandoned-X rescue".

## Solution — the de-leaked forward-gap test (run BEFORE labeling anything a lever)
1. **Define the gap.** Pick a gap boundary G after the anchor T (e.g. T+7). Restrict to users **still
   un-transitioned at T+G** (still un-advanced at T+7). This drops the users who were already mid-transition at the anchor.
2. **Predictor strictly before the outcome window.** Measure each candidate behavior in an **early window that ends
   at or before T** (e.g. the 8–30d-before-T window — NOT the 0–7d window that abuts the transition). The behavior is
   now ≥G days before any outcome can count.
3. **Outcome strictly after the gap.** Outcome = transitioned in **(T+G, T+W]**. Now a positive effect cannot be
   "the user is transitioning right now" — there is a clean temporal gap between predictor and outcome.
4. **Re-estimate adjusted (OR + AME) on this de-leaked design.** A behavior that **survives** (CI excludes 1, sane
   power) is a genuine **forward driver → a LEVER**. A behavior that **collapses to null/negative** (especially the
   ones with huge raw lift) is the **transition act in disguise → a PREDICTOR/intent-marker**, not a lever.
5. **Classify every surviving behavior: intervention vs intent-marker.** *Intervention* = something the operator can
   directly initiate (drive a visit, send an event invite, surface the next task). *Intent-marker* = the user's own
   activity you can only nudge (uploads, checklist). Lead the recommendation with interventions; demote intent-markers
   to "use for targeting / prioritising the nudge queue," explicitly NOT as the thing to ask the user to do.
6. **Run the by-stage Simpson check on any raw-negative lever.** If raw is negative but adjusted positive, recompute
   the rate WITHIN each stage/level stratum (and report a matched-grain / Mantel–Haenszel OR). Confirm the sign in
   both strata; flag thin within-stratum cells.
7. **Control for ENTITY VINTAGE (age since creation) — the confound a same-cohort verification panel will miss.**
   A newer record (application/cart/account) is both *more active* AND *more likely to still convert* (it hasn't had
   time to die), so "recent activity → advance" is partly "newer entities convert." Add `days_since_creation`
   (binned/spline) to BOTH the adjusted and de-leaked models. Expect re-engagement magnitudes to **attenuate
   ~15-20%** but keep sign/rank; lead the write-up with the vintage-controlled (conservative) numbers. NB: an
   adversarial re-derivation panel that queries the *same cohort table* verifies arithmetic, not the control set —
   it will not catch a missing confound. Name the confounds yourself.
8. **Report cross-group effect modification on the ADDITIVE (pp) scale, not the OR.** An OR inflates at a low base
   rate, so a lever will look "bigger" for the lower-converting subgroup purely as an artifact. Before claiming
   "lever helps group X more," compare the **average marginal effect (pp)**: often the additive lift is equal across
   groups and only the OR differs (the interaction is real on the OR scale, illusory on the pp scale).

## Verification
- Huge-raw-lift "obvious" lever → null/negative under the forward-gap test ⇒ correctly reclassified predictor.
- Operator-initiable behaviors (re-engagement visits, events) → survive ⇒ the real levers; report their de-leaked OR
  as the honest magnitude (the at-anchor OR overstates it).
- Any "act within first N days → outcome" headline computed on a window that abuts the transition is leaky: lead with
  the de-leaked magnitude and caveat the at-anchor number as "partly the act itself."

## Example (stalled → submitted transition)
Stalled cohort (a pre-submission status, PIT, each-entity-once), outcome = submitted in (T, T+60].
Raw lever table: recent page-views ~+13pp, attended-event ~+20pp, **document upload ~−3pp (raw negative)**.
De-leaked test (predictor in 8–30d window, outcome in (T+7, T+60] among still-stalled-at-T+7):
- page-views OR **~1.9**, navigation **~2.3**, CTA **~2.0**, attended-event **~1.7**, responded-to-outreach **~1.8**
  → **survive = levers (interventions).**
- document/file uploads → OR ~1.2 with point estimate <1 and underpowered → **fail = predictor, not lever.**
Document upload's raw −3pp was a Simpson reversal (one segment uploads more and stalls more; within each segment the
within-stratum rate is positive, matched-grain MH OR ~1.5). The "act within first 7 days → OR ~2.8" headline was
leaky (the 0–7d window abuts submission); honest forward magnitude is ~1.9–2.3. Recommendation led with
re-engagement + events (interventions); checklist/uploads framed as targeting signals only.
Adding **record-age** control (step 7) attenuated the at-anchor levers ~15-20% (recent-visit OR ~3.2→~2.4,
+17→+14pp) but all survived; the de-leaked ORs barely moved. The "re-engagement helps one segment more" claim
(OR ~5 vs ~2) **dissolved on the additive scale** (step 8): +14.0pp vs +14.1pp — identical. A 4-lens adversarial
panel re-deriving from the same cohort table confirmed every number but missed the vintage confound entirely.

## Extending across MULTIPLE funnel transitions (a per-stage sweep)
When you run this method on every stage of a funnel (stalled→submit, submit→decision, accept→deposit, deposit→enroll, …),
do NOT reuse the first transition's harness defaults — three things change per transition:
1. **The leakage-exclusion set INVERTS.** Exclude whatever encodes the **destination** stage; KEEP the **source**-stage
   encoder as a control. A flag that is leakage upstream becomes a legitimate cohort control downstream (e.g.
   a `status_variant` encoder is leakage at submit→decision but a cohort control at accept→deposit; the deposit-payment event
   is the *outcome* at accept→deposit but the *cohort definition* at deposit→enroll). A blanket inherited EXCLUDE both
   leaks and over-controls.
2. **Re-probe G and W from EACH transition's own time-to-event hazard** (front-loaded vs long admin tail vs cyclical deadline).
3. **Re-anchor vintage to SOURCE-stage entry** (days-since-submit / -accept / -deposit), not creation.
Also: downstream transitions are often **calendar-deadline-driven, not continuous-hazard** (deposits cluster at the
deadline, enrollment at cycle-start) — add anchor-period/days-to-deadline as a control; it's the confound a
same-cohort arithmetic panel misses. And the **active lever can SHIFT down the funnel** (re-engagement at the top →
administrative/financial/calendar at the bottom) — report the shift; don't assume the top-of-funnel lever persists.
Expect/accept structural nulls where the mechanism cannot exist (a decision stage gated on documents, not engagement;
a financial-aid application filed on an external government site) — a "lever" that survives there is reverse causation,
not a lever.

## Deepening along a COHORT axis (run the de-leak PER cohort — the pooled verdict masks markers)
When you split a confirmed lever by cohort (level, segment, cycle, plan), three rules keep the deepening honest:
1. **Re-run the de-leaked forward-gap test WITHIN each well-powered cohort — do NOT inherit the pooled verdict as if
   it held everywhere.** A lever that survives pooled can collapse to a **powered null** in a high-base-rate cohort,
   where recent activity is an **intent marker (reverse causation), not a push** — the cohort already advances at a
   high rate, so the active are just the ones about to advance anyway. The pooled de-leak is then carried by the
   *other* cohort. (One instance: navigation de-leak pooled OR ~1.9 at accept→deposit splits to the low-base-rate
   segment **~2.8** (lever) vs the high-base-rate segment **~1.0**, CI spans 1 (marker) — the high-base segment
   deposits ~45% regardless. The pooled verdict would have mislabelled the high-base cells "forward-validated." So the
   lever doesn't only shift *down* the funnel — it can *narrow to one cohort* at a step before dying.)
2. **A cohort cell too thin to run its own de-leak INHERITS the best-powered verdict — and is LABELLED as inheriting,
   never shown as forward-validated on its own.** A thin cell's high recent-window OR is exactly where the act-in-
   disguise re-enters through the split; mark it `inherited`, not `validated`.
3. **The additive-pp "this cohort gains more" crossover is behaviour- AND scale-dependent — verify it on the SAME
   scale you cite.** OR inflates at the higher-base cohort, so "OR bigger for cohort A but pp bigger for cohort B" is
   the expected shape — but whether B>A on pp can hold on the *raw gap* yet not the *adjusted marginal effect* (or
   vice-versa) for a given behaviour. Pick a behaviour where both scales agree and cite that one. (One instance: for
   page-views the crossover holds on raw (~+7 < +14pp) AND adjusted (~+5 < +10pp); for navigation it holds only on the
   adjusted AME — a verifier flagged the navigation citation as directionally reversed on the raw scale.) And note the
   pp-bigger cohort may be the *marker* cohort (rule 1) — "predicts more, doesn't cause more."

## Two evidence tiers — a non-windowable flag CANNOT be de-leaked
Some predictors are **windowed events** (page_visit_8-30d…) and some are **non-windowable flags** (a 180-day
touch marker, a financial-aid-completion/merit flag). Only windowed events can pass the de-leaked forward-gap test —
a static or long-lookback flag has no early-vs-late split, so you **cannot** forward-gap-test it. Therefore: **never
label a flag "de-leak-validated."** Keep two explicit tiers in the lever map — *de-leak-validated forward driver* vs
*suggestive (adjusted-OR only, not forward-tested, selection/reverse-causation-prone)*. Lead recommendations on the
validated tier; demote flags to *targeting* signals needing an A/B / held-out test. A flag sitting **on the causal
path** (financial-aid-completion ↔ committing to enroll) is most likely a **marker**, not a cause. (One instance: a deposit
lever-map led with `attended_event` OR ~2.9 and called the set "survives de-leak" — but the event flag was never
de-leaked; the verifier caught the column header was literally false. The de-leak-validated driver was navigation, OR ~1.9.)

## Prove a NULL is powered, not weak (lead on the CI exclusion)
When you claim "lever X stops working at stage B," a skeptic says "low power / OR compression at a different base rate."
Defeat it by **leading on the confidence-interval exclusion**: show that B's de-leaked OR *upper bound* sits below a
stage-A-magnitude effect (so the data affirmatively excludes a real effect), and corroborate with a comparable
transition. NB the de-leaked OR is estimated on the **gap subset** (those still un-advanced at T+G), whose advance rate
is NOT the full-cohort base rate — don't claim a "same base rate" match on the full-cohort number; cite the gap-subset
rates if you compare. (One instance: navigation de-leak OR ~2.0 [1.6,2.5] at submit→decision vs **~1.0 [0.8,1.2]** at
deposit→enroll; the deposit→enroll upper bound ~1.2 excludes the effect → real null. Both ~63% full-cohort @120d;
gap-subset rates ~37% vs ~49% — comparable, so not base-rate compression. An *earlier* draft wrongly said "same 63%
base rate" — the 63% is the full cohort, not the gap subset the OR is fit on; the reviewer caught it.)

## Pre-anchor contamination check for the surviving lever (run it; it often STRENGTHENS the claim)
If a large share of advancers anchor *on* their source-stage-entry day, the "early 8-30d" de-leak window for them is
actually **before** they entered the source stage — so the surviving OR may measure pre-entry intent, not a push on an
already-stalled entrant (the difference between "engaged applicants deposit" = targeting vs "re-engaging a stalled
accepted entity causes deposit" = lever). Clean test: restrict the de-leak to entrants anchored **≥(window-end) days
after source entry** (e.g. days-since-accept ≥30), so the 8-30d window is genuinely post-entry, and re-check survival.
(One instance: among accepted entities anchored ≥30d post-acceptance, navigation's de-leaked OR *rose* to ~3.6
[2.3,5.7] — the recommendation was strengthened, not softened.)

## Variant: verifying a shipped "after event X" claim (own-anchor rebuild)
A shipped metric that CLAIMS per-unit timing conditioning ("~2–3× more likely to deposit if they visit
program pages **after acceptance**") may not implement it — read the probe SQL: if there is **no join to
each unit's own event date** (it counts any activity in a fixed pre-anchor window), the claim is a
whole-window read mis-described, regardless of whether the magnitude reproduces. Rebuild it honestly:
1. **Own-anchor join:** each unit's first "event X" timestamp from PIT history (not a shared calendar anchor).
2. **Paired designs, both reported:** (a) primary = predictor window (event_ts, min(outcome_ts, event_ts+W))
   — censoring at the outcome biases the lift DOWN (fast completers have short exposure), state that as the
   conservative direction; (b) robustness = fixed forward-gap window (event_ts, event_ts+g] with outcome in
   (event_ts+g, event_ts+W], excluding units that completed inside g. If both agree, the signal is real.
3. **Specific-vs-generic control (ablation-lite):** compute the same lift for ANY return activity, and the
   specific behaviour's lift CONDITIONAL on any return. If the conditional collapses to ~1, the "specific
   content" story is just re-engagement wearing a costume.
4. **Never ship the pooled lift** — segments that both engage more AND convert at a higher base rate
   (a high-base segment vs a low-base one) Simpson-inflate it (~2.8 pooled vs ~2.0 / ~1.1 split); the split IS the finding.
   (In one instance the pooled "after-acceptance" rebuild also REFUTED a shipped negative guardrail — the high-base
   segment that browsed post-acceptance deposited several points HIGHER (~+7pt), not "~9pt lower" — a reminder that an
   unsourced action-driving number can be wrong in *direction*, not just magnitude.)
5. **A stratified-on-engagement ablation is NOT a substitute for the own-anchor rebuild — volume ≠ vintage.**
   An MH-pooled lift within engagement-volume strata (return-days / total-events terciles) can PASS — even
   *strengthen* — on a lever the own-anchor design then kills, because in a whole-window design the "never
   engaged with Y" arm can be dominated by **stale prior-cycle records** that neither engage nor convert, and
   volume strata cannot separate cohort vintage. Run the volume ablation as a complement (it rules out the
   "just more engaged overall" story) but let only the own-anchor read adjudicate an "after event X" claim.
   (One instance: a return-activity-after-advancing lift of ~7–10× whole-window; MH within volume
   terciles ~10×; own-anchor ~0.8–1.2 in every design — matching the sweep's powered null, navigation
   OR ~1.0. The whole-window multiplier was fresh-vs-stale composition wearing a lever costume.)

## Variant: probe the outcome−anchor gap FIRST — a rewritable milestone anchor can postdate the outcome
Before running ANY forward-gap or "engagement-before-the-milestone → advance" design, probe the distribution of
`outcome_date − anchor_date`. The forward-gap test silently assumes the anchor precedes the outcome; two failure
modes break that assumption:
1. **The outcome can PRECEDE the anchor → a post-milestone forward-ordered design is *structurally inapplicable*,
   not merely biased.** If a large share of advancers hit the outcome on/before the anchor, "activity in a window
   AFTER the anchor → advance strictly later" discards them wholesale and computes lift on an unrepresentative tail.
   (One instance, accepted→deposit: deposits land a **median of roughly −30 days relative to the recorded decision
   date — about two-thirds deposit on/before the decision is even stamped**, only ~8% >30d after; the post-milestone
   anchored lift ran on the ~8–17% long tail and was meaningless. This is the "event-anchored engagement lift inverts
   when advancement is fast" pattern in its extreme form — advancement is *prior to* the anchor, not just fast.)
2. **A REWRITABLE business-milestone date leaks post-outcome activity into a "pre-anchor" window.** If the anchor is
   a mutable field (a decision date overwritten by re-decisions, a status date that can be back/forward-dated) and it
   frequently POSTDATES the outcome, then "engagement ≤ anchor" silently counts activity that happened AFTER the
   outcome. (One instance: a `decision_at` field postdates the deposit for ~two-thirds of depositors, so "engagement ≤
   decision_at" swept post-deposit portal/app-progress activity into the "pre-decision" bucket — depositor
   engagement-breadth ~6.9 pre-decision vs ~2.9 pre-submission, a ~2.4× inflation that manufactured a spurious
   monotone gradient.)

**Fix:** re-anchor on an **immutable, provably-upstream** event — the earliest funnel stamp that cannot postdate the
outcome (submission, account-creation) — and VERIFY it (one instance: only a handful, <0.5%, deposited before they
submitted). The clean read is then a *pre-upstream-anchor selection* contrast (a marker), reconciled to the cohort and
A/B-gated — never a post-milestone lift. Composition still bites underneath: a lens whose categories are near-perfectly
stratified by the controlling variable (an acquisition-channel field: ~93% of one segment in some values, ~97% of the
other segment in others) shows a pooled gradient that is ENTIRELY composition and washes to a dead null under
level+program controls (`event` adj OR **~1.0**, retiring a stale several-fold "~4× event lift"). Reproduce the model's
own feature (e.g. earliest-touch channel, from the account/entity side) so the retired claim is contradicted on its
own definition.

## Notes
- This is the **actionability** counterpart to `marginal-lift-collapses-on-pre-event-temporal-restriction` (which
  de-leaks a per-event lift table by pre/post entity-creation split for FEATURE/anchor selection). Same instinct
  (temporally separate predictor from the transition), different question: that skill asks "is this a clean
  pre-entity feature?"; this skill asks "is this an actionable lever or just a predictor?" and adds the
  forward-gap-among-still-stalled design + the intervention-vs-intent classification.
- The gap G matters: too small and you re-admit the act; too large and you lose power. G≈7d worked for a transition
  whose population median completion was 0 days.
- Pair with a base-rate reconciliation: anchoring on "still in stage A at a scoring date" selects the stalled slice,
  so the cohort's advance rate sits far below the population completion rate — state that explicitly, it's the
  denominator the whole rescue story hangs on. Probe the per-transition time-to-event so a fast/front-loaded transition
  (e.g. deposits: median ~10d, ~40% within a week) isn't misread — the low stalled-slice rate is restriction-of-range,
  not a low population rate.
- **Characterize the NON-advancers against the live snapshot before headlining the rate** — a "X% reach the next
  stage" denominator can be dominated by **stale/zombie source records** that were never worked and never will advance.
  Check: of those who never advance (even with unlimited follow-up), how many are STILL in the source stage in today's
  snapshot? If most are (one instance: ~85% of never-decided records are still in the source stage today), the
  non-advancers are genuine stuck records, not a data gap — and staleness DEFLATES the advance rate (stuck records are
  non-advancers in the denominator), it does not inflate it. Confirm the outcome source (history vs snapshot) is
  complete: 100% of the advanced cohort should also show the event in the snapshot, or you're undercounting.
- Fairness: if a protected attribute (e.g. citizenship) shows a large base-rate gap, the lever can still be fair (helps
  both groups) while propensity-ranked *targeting* is not — recommend not allocating outreach by predicted propensity
  alone.
- **A funnel "stage" may be CONDITIONAL / branching — verify the stamp is a real universal gate before treating its
  absence as a stall.** A step that looks mandatory (a deposit, a verification, a call) may not apply to a whole
  subpopulation (program type, segment), who skip it and still advance. If so, "never did X" mixes true stalls with
  structurally-exempt entities, and a "X → next stage" transition silently drops the exempt branch. Check: of those
  who reach the FINAL stage, how many ever had the intermediate stamp? (One instance: ~6.6k accepted entities reached
  the final stage with no deposit stamp — continuing entities + deposit-exempt program types — so "accepted-not-deposited"
  conflated stalled depositors with deposit-exempt entities; the deposit→enroll transition covered only the
  deposit-applicable branch.) Model the branch explicitly or scope the cohort to where the step applies.
- **State the DENOMINATOR's data basis on every funnel breakdown, and reconcile the full 2×2.** A structural
  side-probe ("of everyone who reached stage X, how many did Y?") can quietly use a **snapshot status filter** as the
  denominator even when the main cohort analysis is PIT — and a current-status filter **undercounts** the
  "ever-reached-stage-X" population vs the history reconstruction (entities churn out of that status). Immutable date
  stamps (deposit-date, enrolled-flag, submit-date) are PIT-safe as "ever happened" markers; **current status is not**.
  Prefer a history-derived "ever reached X" denominator and immutable-stamp numerators. Also: when you quote two
  numbers from a cross-tab ("~7,300 deposited; ~6,600 enrolled-without-deposit"), make sure they're on the same footing —
  don't mix a **row total** with a single **cell**; present the whole 2×2 so it sums to the population. (One instance: the
  snapshot accept-filter counted ~32k vs the history "ever-accepted" ~42k — a ~10k undercount; the
  enrolled-without-deposit cell (~6.6k) was identical either way because the stamps are immutable, but the deposit
  *rate* moved ~23%→~18%, and the original "~7,300 + ~6,600" mixed a row-total with a cell and didn't reconcile.)
```
