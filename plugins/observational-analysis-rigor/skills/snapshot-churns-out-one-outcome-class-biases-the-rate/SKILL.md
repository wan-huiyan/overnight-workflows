---
name: snapshot-churns-out-one-outcome-class-biases-the-rate
description: |
  A RATE (accept rate, win rate, conversion rate, churn rate, approval rate) computed over a
  current-state SNAPSHOT table is biased when the snapshot differentially retains/prunes one
  outcome class — losers/denials/cancellations often churn out faster (status overwritten to
  Withdrawn/Closed/Deleted) than winners. Use when: (1) computing a per-segment accept/admit/
  conversion rate FROM a snapshot/upserted table, (2) a rate looks suspiciously high/clean, (3)
  a denominator is "everyone with a decision" but read from current state, (4) ranking segments
  by a rate where the ranking itself is the deliverable. Fix: count numerator AND denominator
  from the immutable event/history log (the full outcome universe), use the snapshot only for
  attribution. NOT PIT feature leakage (that's a future value back-stamped onto a past row);
  this is present-tense selection bias in the rate's denominator.
author: Claude Code
version: 1.0.0
date: 2026-06-24
disable-model-invocation: true
---

# A snapshot that churns out one outcome class biases any rate computed over it

## Problem
You compute a rate — accept rate, conversion rate, win rate, approval rate — as
`favorable_outcomes / all_decided`, reading both from a **current-state snapshot** (an upserted
table that keeps only the latest row per entity). If the snapshot **differentially prunes one
outcome class**, the rate is biased and the per-segment ranking is distorted — even though every
individual number "looks right."

The mechanism: unfavorable outcomes often **churn out of the snapshot faster** than favorable ones.
A rejected application gets withdrawn/closed/re-coded (`WD`/`WA`/`Closed`/`Cancelled`) and the
original "Denied" status is overwritten or the row drops; the accepted ones persist. So the snapshot
**over-retains the numerator class relative to the denominator class** → the rate is **inflated**, and
because the pruning rate differs by segment, the **ranking is unevenly biased** (not a constant offset).

## Trigger conditions
- Computing a per-segment **rate** (accept/admit/conversion/win/approval/pass) where the **ranking is
  the deliverable** (e.g. "which programs/reps/cohorts are most selective/effective").
- The denominator is "everyone who reached a decision/outcome," read from a **snapshot / upserted /
  latest-state** table rather than an append-only event log.
- A rate looks **suspiciously high or clean** (e.g. 95% accept) or a segment looks *less* selective/
  effective than domain experts expect.
- The table has churn states (`Withdrawn`, `Cancelled`, `Closed`, `Deleted`, `Deferred`) that can
  **overwrite** the original outcome.

## Solution
1. **Measure the pruning before trusting the rate.** Join the snapshot to the immutable
   event/history log and compute *retention by outcome class*: what fraction of each outcome's events
   still appear in the snapshot. If retention differs across classes, the snapshot rate is biased.
2. **Count the rate from the full outcome universe** (the append-only event/history log), keyed on the
   entity id — numerator AND denominator. Use the snapshot **only to attach attributes** (segment,
   level, owner) via an inner join, and **report the share of history-outcome rows that have no
   snapshot attribute row** (those are the pruned ones you can't attribute — a visible coverage gap).
3. If attribution is impossible for the pruned class, **label the metric honestly** ("rate among
   currently-retained records — NOT the authoritative rate") and put "authoritative rate per segment"
   on the stakeholder question list.
4. Note the bias **direction**: over-retaining the favorable class inflates the rate, so a corrected
   read usually makes selective/low-rate segments look *even more* extreme — i.e. the bias generally
   **understates**, not overstates, a "this segment is different" headline (a useful robustness point).

## Verification
- Retention-by-outcome probe returns materially different fractions per class (the smoking gun).
- The history-universe rate differs from the snapshot rate by more than rounding, and the per-segment
  *ordering* changes for at least some segments.
- Independent re-derivation: an adversarial agent re-computing the rate from the raw event log
  reproduces the corrected number, not the snapshot one.

## Example (illustrative — from a verified engagement)
Per-segment **accept rate** for a competitiveness ranking, first cut from a current-state snapshot
(`analytics.application_snapshot`, an upserted latest-row-per-entity table):
- Retention probe over one year of decisions: the snapshot retains **~95% of accepted** applications but
  only **~78% of rejected** ones (denials churn out under `WD`/`WA` re-codes). Pooled accept rate **~87%
  (snapshot) vs ~84% (true)** — and the per-segment ranking was unevenly biased.
- Fix: count `ever_accept` / `ever_reject` / `ever_waitlist` from the append-only status-history table
  (`application_status_events`, keyed on `application_id`), the full decision universe; use the snapshot
  only to attach the segment attribute (program / level). The most selective segment came out at a
  verified **~11% accept** (a highly selective program) — the corrected (more selective) number,
  confirming the bias understated the headline.

## Notes
- Distinct from **PIT feature leakage** ([[snapshot-feature-pit-leak-rate-measure-history-change-after-target]],
  [[null-bucket-hides-progressors-in-snapshot-training]]): those are about a *feature/label* on a
  *training* row being back-stamped from current state. This is present-tense **selection bias in a
  rate's denominator** on an *analytics* read — no target date involved.
- Related: [[pit-history-reconstruction-needs-canonical-code-source-of-truth]] (the history log needs a
  canonical code set), [[probe-prior-status-before-bucketing-a-pit-code]] (churn codes like WD/WA can
  overwrite a prior decision — handle a fallback when the history has no decision event but the snapshot
  shows a withdrawal-after-decision).
- The same trap hits any churning snapshot: CRM win-rate (lost deals deleted), support resolution-rate
  (spam tickets purged), loan approval-rate (declined apps archived). Always probe retention-by-outcome
  before ranking segments by a rate read from current state.
- **See also** [[near-absorbing-state-rate-deflated-by-snapshot-residue]] — the sibling observation-time
  trap: same "don't trust a rate off current state," but the bias is *when* you observe a fast-draining
  pool (the residue's conversion rate), not *which outcome class* the snapshot retains.
