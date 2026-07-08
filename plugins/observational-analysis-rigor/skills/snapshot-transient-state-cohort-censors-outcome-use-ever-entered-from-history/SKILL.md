---
name: snapshot-transient-state-cohort-censors-outcome-use-ever-entered-from-history
description: |
  Use when you build a "records CURRENTLY in transient/pending state X → did they advance to the next
  state?" cohort by filtering a SNAPSHOT on status = X, and the outcome rate comes back implausibly near
  ZERO (or absurdly low). Trigger conditions: (1) a funnel/pipeline/CRM/ticketing analysis of the form
  "applicants in 'In Process' → got decided?", "leads 'contacted' → converted?", "tickets 'in progress'
  → resolved?", "trials 'active' → paid?"; (2) the cohort is defined off TODAY's status column of a
  snapshot table; (3) the state is one records pass THROUGH, not a terminal state; (4) the measured
  advance/resolve rate is ~0 and you suspect the data is wrong. Root cause: a snapshot only retains
  records STILL in X — and still-in-X means not-yet-resolved, so you've conditioned on the outcome not
  having happened (survivorship/censoring). Fix: reconstruct the cohort as EVER-ENTERED-X from the
  status-history / CDC / event-log table. See also pit-downstream-cohort-match-reconstructed-status-to-immutable-stamp,
  null-bucket-hides-progressors-in-snapshot-training.
disable-model-invocation: true
author: Claude Code
version: 1.0.0
date: 2026-06-29
---

# A snapshot "currently in state X" cohort censors its own outcome — use ever-entered-X from history

## Problem
You want a panel for a mid-funnel step: "of the records in transient state **X**, which advanced to the
next state?" The obvious build is `WHERE snapshot.status = 'X'`, outcome = "reached the next state."
The outcome rate comes back **~0** (e.g. 1-3%), and you're tempted to conclude "almost nobody advances
from X" or "the data is broken."

Neither is true. **A snapshot only contains records that are STILL in X right now.** A record still in X
is, by definition, one that **hasn't resolved yet** — everything that entered X and then advanced has
*left* X and no longer matches `status = 'X'`. So filtering the snapshot on the transient state
**conditions on the outcome not having happened** (survivorship / right-censoring). The cohort is
structurally incapable of showing the advance you're measuring.

## Context / Trigger Conditions
- Cohort defined off **today's status field** of a snapshot table, where the status is a **transient /
  pending / in-flight** state (in-review, in-process, awaiting-X, contacted, trialing, pending), not a
  terminal one.
- Outcome = "did they move to the *next* state?" and the rate is implausibly low / near-zero.
- (Bitemporal tell) the question is "as-of when they were in X, did they go on to resolve?" but the data
  source only knows "are they in X *now*."

## Solution
1. **Reconstruct the cohort as EVER-ENTERED-X from the status-history / CDC / event-log table**, not the
   snapshot. The history table records transitions; select the records that ever had a transition
   *into* X (e.g. `New_Value = 'X'`, `to_status = 'X'`, an event `entered_X`). DISTINCT on the entity id.
2. **Join that cohort to the (immutable) outcome** — the terminal fact (decided / converted / resolved /
   paid), which is durable and not censored.
3. Now the cohort contains *both* the records that entered X and resolved *and* those still pending — a
   real outcome-bearing panel. The advance rate becomes plausible and the N is far larger than the
   snapshot-still-in-X count.
4. If no history/CDC table exists, you cannot build this cohort honestly from a snapshot — say so (don't
   report the censored ~0), and treat it as a data-availability bound.

## Verification
- The ever-entered-X advance rate matches the funnel's known base rates (sane, not ~0).
- N(ever-entered-X) ≫ N(snapshot status = X) — the gap is exactly the records that already resolved.
- Spot-check: a handful of records with `status != 'X'` *today* but a history row entering X, and confirm
  they have a real outcome.

## Example
A mid-funnel "awaiting documents" step — transient status `IP` ("in process"). Building it from the live
snapshot `snap_status = 'IP'` would make the outcome (`decided`) **~all-zero** — a still-`IP` record is
precisely one not yet decided. Rebuilt as **ever-entered-IP** from the status-history table
(`application_status_history` where `new_value = 'IP'`), joined to the immutable decision outcome, the
decided rate lands in a plausible high range (well above zero), on an N in the **thousands** — a large,
real outcome-bearing panel. The snapshot build would have silently produced a useless censored cohort.

## Notes
- This is the **cohort-definition** cousin of snapshot-feature PIT leakage: that trap is "a snapshot
  *feature* leaks the future"; this trap is "a snapshot *cohort filter on a transient state* erases the
  future." Both come from using a point-in-time snapshot to answer an as-of-then question.
- Terminal-state filters are fine (`status = 'paid'` is not censored — paid is an endpoint). The trap is
  specific to **transient** states records pass through.
- Generalizes well beyond admissions: sales pipelines (`stage = 'negotiation'`), support
  (`status = 'in_progress'`), subscriptions (`state = 'trialing'`), order fulfilment (`in_transit`).
- See also: `pit-downstream-cohort-match-reconstructed-status-to-immutable-stamp` (joining
  reconstructed-status to snapshot signals), `null-bucket-hides-progressors-in-snapshot-training`,
  `null-status-with-engagement-conflates-never-vs-form-stall`.
