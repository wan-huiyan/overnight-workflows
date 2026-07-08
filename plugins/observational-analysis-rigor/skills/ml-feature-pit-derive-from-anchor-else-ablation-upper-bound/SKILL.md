---
name: ml-feature-pit-derive-from-anchor-else-ablation-upper-bound
description: |
  Decide go/no-go on a candidate ML feature that derives from a SNAPSHOT field with NO point-in-time
  history table — where joining today's value onto historical training rows back-applies the future
  and leaks. Two branches with opposite resolutions. Use when: (1) scoring a CRM/account/lead
  snapshot field (transfer credits, age, status, score) as a model feature and there's no
  `*_history`/`*_audit`/`*_cdc` for it; (2) a candidate shows a strong incremental gradient you
  suspect is partly post-outcome leak; (3) someone proposes proving value with a naive with/without
  ablation; (4) a time-varying attribute (age, tenure, days-since-X) is stored as a recomputed
  snapshot. Covers: derive-from-immutable-anchor (birthdate→age, created_date→tenure) for PIT-exact
  with NO history table; and the asymmetric ablation-upper-bound rule when PIT is truly unsatisfiable.
author: Claude Code
version: 1.0.0
date: 2026-06-03
---

# Candidate ML feature, snapshot source, no history table: derive from the anchor, else treat the ablation as an upper bound

## Problem
A candidate feature comes from a **current snapshot** (one row per entity, latest value). There is no
history/CDC table for the field, so its **as-of-target_date** value can't be reconstructed. Joining
the snapshot onto historical training rows back-applies *today's* value — if the field is set or
revised at/after the outcome, that's a **leak** that inflates the feature's apparent lift. Two cases
resolve differently, and conflating them either kills a usable feature or ships a leaky one.

## Context / Trigger Conditions
- Scoring a snapshot field (account/lead/CRM) as a model feature; no `*_history` / `*_audit` / `*_cdc`
  table exists for it (or the history table is a stub — e.g. a 100-row single-day export).
- The candidate shows a strong incremental gradient and you suspect part of it is post-outcome.
- Someone proposes a naive with-vs-without retrain to "prove" the feature's value.
- The attribute is **time-varying** (age, tenure, days-since-X, account state) but stored as a single
  recomputed number.

## Solution

### Branch 1 — an immutable anchor exists → DERIVE, don't reconstruct (no history table needed)
If the time-varying attribute has an **immutable underlying anchor**, compute the as-of-T value from
the anchor at the target date — PIT-exact, no history required:
- age → `DATE_DIFF(target_date, birthdate, YEAR)` (birthday-aware), NOT the stored `age` column.
- tenure / days-since → `DATE_DIFF(target_date, created_date / first_seen)`, gated `anchor <= T`.

**Prefer the derived value over the stored snapshot** — the stored snapshot is usually recomputed and
stale. Diagnose it: compute how often the stored value matches (a) today and (b) the value-at-creation.
If it matches *neither* well, it's a leaky moving snapshot. (Seen: a stored `age` matched today only
~25% of the time and age-at-creation only ~15% → matches neither → leaky; the `birthdate`-derived
`age_at_target` is exact.) Encode missing
as an explicit `*_unknown` category + an `is_*_known` indicator (missingness is often informative);
never 0/mean-impute a derived attribute.

### Branch 2 — no anchor AND no history → PIT-UNSATISFIABLE → the ablation is an UPPER BOUND
If the value can't be derived from an anchor and has no history table, you **cannot** strip the leak.
Critically:
- **A naive with/without ablation REPRODUCES the leak** and reports the lift as an **inflated upper
  bound**, not production lift. Do not read it as "the gain we'll get."
- **Bound, don't assume.** A subpopulation that structurally *can't* carry the post-outcome update
  (e.g. applied-but-never-enrolled still has the field) rules out a *total* leak — but does NOT bound
  the inflation in the outcome-positive cell. Check whether the field's *magnitude* differs by outcome
  (if mean-when-present is ~equal across enrolled/not, the leak is in *presence*, not value).
- **Asymmetric decision rule:** small gain even at the inflated upper bound → **DROP** (leaky,
  account-grain, no PIT path). Large gain → **do NOT ship the leaky version**; productionize only
  after securing point-in-time data first — concrete ask to data-eng: add the field to source-system
  field-history tracking (CDC) so future rows are PIT-reconstructable.
- Encode value-when-absent as **NULL, not 0**, so the tree separates no-record / not-yet-set /
  has-value (don't collapse structural-absence into a real 0).

## Verification
- Branch 1: the stored snapshot matches neither today nor creation-time (proves it's a moving
  snapshot); the anchor-derived value is monotonic-correct as-of T and needs no history table.
- Branch 2: you can state *why* the ablation lift is an upper bound (the leak you can't remove), and
  the decision (drop / secure-PIT-first) follows the asymmetric rule rather than the raw ΔAUC.

## Example
- **transfer_credits** (a `account_snapshot.transfer_credits` column): real incremental lift over a
  coarser `student_type` feature (a several-fold gradient within one applicant segment), BUT it's an
  account-grain snapshot, the `account_history` table is a ~100-row single-day export stub, and the
  field is untracked → **PIT-unsatisfiable**. The underlying value (e.g. a credit posting) can complete
  at/after the enroll decision → today's value leaks onto pre-decision rows. Verdict: add-with-caveat;
  ablation = inflated upper bound; small→drop, large→secure field-history first. Encode
  `has_transfer_credits` + `transfer_credits_n`, **NULL when absent**.
- **age**: raw `age` is a leaky recomputed snapshot, but `birthdate` is immutable → derive
  `age_at_target` PIT-exact, **no history table** (Branch 1). The opposite resolution to transfer.

## Notes
- The two branches are the whole decision: *can I derive the as-of-T value from an immutable anchor?*
  Yes → derive (cheap, exact). No → it's an upper-bound ablation + an asymmetric ship/drop rule.
- See also: `field-pit-mode-triage-when-no-history-table` (monitoring/backtest triage for a no-history
  field — the sibling "do we even need history?" question), `sf-bq-upsert-verify-before-createddate-gate`
  (verify upsert-vs-append before trusting created_date as a gate), `cdc-field-history-coverage-audit-
  before-scoping-temporal-fix`, `availability-tier-conflates-base-rate-with-data-gap` (don't drop the
  feature for "low coverage" before this PIT triage), `baked-derived-age-freezes-at-bake-time` (the
  dashboard-baking cousin of the derived-age trap), `pit-backstamp-test-presence-before-record-creation`
  (PROVE a snapshot-attached/identity feature is back-stamped before deciding — the verification that
  feeds this go/no-go).
