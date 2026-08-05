---
name: snapshot-feature-pit-leak-rate-measure-history-change-after-target
description: |
  Measure — don't assert — whether a snapshot-sourced TRAINING feature leaks point-in-time (PIT)
  information, and turn "this field is static, the snapshot is fine" into a per-field GO/FIX/INVESTIGATE
  verdict backed by a number. Use when: (1) an ML training pipeline reads a current-state SNAPSHOT
  table (e.g. `crm_account`, `dim_customer`, a CRM/MDM current view) for a feature evaluated
  at a historical `target_date`/`as_of_date`; (2) someone justified the snapshot as a "static attribute"
  by engineering judgment, never a check; (3) you must justify EACH remaining snapshot field before a
  cutover/audit; (4) a `*_history` / CDC / field-history table exists for the entity. The core measure:
  leak rate = fraction of MATERIALIZED training rows whose field-history shows ≥1 change dated AFTER that
  row's target_date (equivalently MAX(change_ts per key) > target_date) — that is exactly the share of
  rows where today's snapshot value differs from the value the model should have seen. Includes: the
  exact SQL template (single-hop + two-hop via a bridge), the join-via-identity-map gotcha, and four
  honesty caveats (field-level leak is an UPPER bound on feature-level; absence-of-history ≠ static;
  row-modstamp upper bound is loose; leak-rate ≠ AUC impact).
author: Claude Code
version: 1.0.0
date: 2026-06-07
scope: global
---

# Measure snapshot→PIT leak rate as "history changes after target_date"

## Problem

A training pipeline reads a **current-state snapshot** for a feature whose label/observation is at a
**historical** `target_date`. If the attribute changed between `target_date` and "now," the snapshot
back-stamps a future value onto a past row → **PIT leakage**. The usual defence — *"it's a static
attribute, the snapshot is fine"* — is an engineering assumption that is frequently **wrong** (a status
attribute gets first-populated, addresses change when a person moves, a transient flag flips, a categorical
profile field is edited). This skill replaces the assumption with a measured rate, per field.

## Trigger conditions

- The feature SQL/DataFrame reads a snapshot table (`*_account`, `*_application`, `dim_*`, a current MDM view)
  rather than a `*_history`/CDC table, for a model trained over many `target_date`s.
- A prior decision (PR/ADR/handoff) called the field "static" / "snapshot like X" without drift evidence.
- You're auditing a model before a cutover and must give each snapshot field a verdict.

## The measure (the one idea)

> The snapshot value used at train time (≈ today's value) equals the true PIT value as-of `target_date`
> **iff the field did not change after `target_date`.** So for a field with a field-history table:
>
> **leak(row) = [ MAX(history.change_ts for that entity key) > row.target_date ]**
>
> **leak rate = COUNTIF(leak) / COUNT(rows where the field is applicable)**

No value reconstruction is needed for the *rate* (only for magnitude). `MAX(change_ts) > target_date`
is exactly equivalent to "∃ a change after target_date" and is cheap.

## Solution / procedure

1. **Ground-truth the feature set.** Confirm the field is an ACTUAL live model feature (read the saved
   model feature list / `_features.json`, not an exclude-list reconstruction). Snapshot columns that are
   read but model-EXCLUDED carry no model leakage → trivially GO.
2. **Map BQ/df column → source-system API field name** and confirm it is field-historied: enumerate the
   distinct `Field` values of each EAV history table (`SELECT Field, COUNT(*) GROUP BY Field`). Match by
   source-system API name (`residency_status`), not the warehouse column name. (Per
   `cdc-field-history-coverage-audit`, one history row tracks ONE field — don't assume a table covers
   every column.)
3. **Pre-screen (cheap GO):** `COUNTIF(change_ts > window_start)` for the field. **Zero ⇒ no training row
   can leak ⇒ GO**, one query.
4. **Measure the rate** against the MATERIALIZED training table (it has `target_date` + the entity key, or
   a join to one). Aggregate history to `MAX(change_ts) per key` first, then LEFT JOIN — never a correlated
   subquery. Single-hop template:

   ```sql
   WITH rows AS (                         -- materialized training rows -> entity key
     SELECT target_date, LOWER(account_id) AS key
     FROM `train_features`                -- or via the identity map, see gotcha
   ),
   hist AS (
     SELECT LOWER(account_id) AS key, MAX(SAFE_CAST(changed_at AS TIMESTAMP)) AS last_change
     FROM `field_history` WHERE Field = 'the_field' GROUP BY key
   )
   SELECT ROUND(SAFE_DIVIDE(
     COUNTIF(h.last_change > TIMESTAMP(r.target_date)),
     COUNTIF(r.key IS NOT NULL)), 4) AS leak_rate
   FROM rows r LEFT JOIN hist h USING(key);
   ```
   **Two-hop** (history keyed on a child entity, e.g. application): insert a bridge
   (`parent_id → child_id`) between `rows` and `hist`, take `MAX(last_change)` per parent.
5. **Verdict logic:**
   - **historied + churns (leak ≥ ~1-2%)** → **FIX** (PIT-reconstruct from history; symmetric train+serve).
   - **historied + ~0% churn** → **GO** (document the measured rate).
   - **NOT in any history table** → **INVESTIGATE**, *not* GO (see caveat 2).
   - **already PIT-from-history or self-date-gated `≤ target_date`** → **GO** (verify the gate exists in BOTH
     train and serve).
   - **excluded / not a model feature** → GO (no model leakage).

## Caveats (state these — they are the honesty of the report)

1. **Field-level leak is an UPPER bound on feature-level leak.** If the feature collapses the raw field
   (raw codes → a binary flag; miles → distance band; N status codes → K groups), a change that stays within
   the same bucket doesn't move the feature. Report the field-level rate as the conservative bound; the
   value the model sees changes **≤** as often.
2. **Absence from the history tables ≠ static.** Field-history tracking is usually **opt-in per field**, so
   a missing field may be untracked, not unchanging. Verdict = INVESTIGATE (can't prove static), not GO —
   this is the exact intuition the audit exists to overturn.
3. **Row-level modstamp is a LOOSE upper bound for non-historied fields.** `last_modified_date >
   target_date` bounds the leak (the row was touched after target_date) but is usually uninformative
   (rows are modified for many reasons — often 60-85%). Use it to confirm "can't rule leakage out," not to
   tighten.
4. **Leak RATE ≠ model-performance impact.** The rate measures correctness (how many rows carry a future
   value), not AUC delta (depends on feature importance + how different the leaked value is). Quantifying
   impact needs an ablation retrain — out of scope for the leakage audit; don't conflate.
5. **Serving snapshot is correct.** At scoring time you *want* current state, so the FIX is **training-side
   only**; serving stays on the snapshot (and should already be symmetric — verify).

## Gotcha — joining via an identity map

Training rows often key on an opaque `visitor_id`; the entity key (account/lead/application id) lives in a
separate identity map (often an ARRAY, picked via `[SAFE_OFFSET(0)]`). Join through it, and **match the
feature pipeline's own pick** (`[SAFE_OFFSET(0)]` vs `ANY_VALUE`/`MAX` across multiple ids). When rows map
to >1 entity, your single-key join introduces measurement noise — quantify the multi-key share and state it.

## Verification

- Cross-derive one headline rate with an independently-written query (different casing/shape). Agreement
  within <0.5 pp validates the join; a gap usually means a key-casing or grain bug.
- Sanity-check the denominator equals the materialized training row count for rows that resolve to a key.

## Example (worked instance)

Auditing every remaining snapshot field of a live model before a cutover: a `residency_status` field
justified upstream as a "static" attribute was measured to carry a value recorded after target_date on
**~3%** of entity-rows (the earlier "static" call was wrong). The largest leaks were fields nobody had
flagged: a workflow status flag (`ready_to_review`→`missing_credentials`) at **~15%**, and
mailing-address→distance at **~9%**. A feature parked in GO purely on an *asserted* design-doc caveat (a
"days in stage" counter derived from snapshot status + a deposit date) flipped to FIX once measured
(status leak **~25%** + deposit **~6%**). Non-historied fields (an application-source code, a
transfer-credits count) → INVESTIGATE, not GO. Deliverable: a per-field GO/FIX/INVESTIGATE scorecard.

## Notes

- Pairs with `cdc-field-history-coverage-audit-before-scoping-temporal-fix` (verify the table covers your
  field), `field-pit-mode-triage-when-no-history-table` (the INVESTIGATE branch),
  `ml-feature-pit-derive-from-anchor-else-ablation-upper-bound` (how to bound impact once you decide to FIX),
  and `null-bucket-hides-progressors-in-snapshot-training`.
- Read-only audit. If the training table feeds an auto-promote/retrain trigger, do NOT rebuild it as a
  side effect of probing — reads are safe, rebuilds can arm a retrain.
