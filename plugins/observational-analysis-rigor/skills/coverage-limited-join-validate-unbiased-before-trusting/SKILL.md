---
name: coverage-limited-join-validate-unbiased-before-trusting
description: |
  When you can only observe an outcome/attribute on a SUBSET of the population because the
  join/linkage/bridge is coverage-limited — imperfect identity resolution, a side child-table that
  only covers some rows, partial instrumentation, survey non-response — validate the restricted
  estimate is UNBIASED before reporting it. Use when: (1) a join covers only X% of rows and you're
  about to either treat unmatched rows as null/negative OR report the matched-subset rate as the
  population rate; (2) the primary record has no foreign key to the entity you need and you found a
  peripheral table that carries both keys as a bridge; (3) you're weighing "rebuild the full linkage"
  vs "use the partial one." The technique: find an INDEPENDENT full-coverage reference for the same
  quantity and compare the bridged-subset rate to it — match ⇒ missingness is ~ignorable (MAR),
  report with a coverage caveat; diverge ⇒ the gap is informative (MNAR), the restricted estimate is
  biased and must be recovered or bounded. Prevents silently shipping a biased rate off a partial join.
author: Claude Code
version: 1.0.0
date: 2026-07-07
---

# Validate a coverage-limited join is unbiased before trusting its estimate

## Problem

You need an outcome or attribute that lives on a related entity, but the link is **coverage-limited**
— identity resolution is fuzzy, the primary record lacks a foreign key, a bridge table only covers
some rows, or instrumentation/response is partial. You can only observe the quantity on the matched
subset. Two instinctive moves are both wrong:

- **Impute unmatched rows as null/negative/absent** → undercounts the numerator (the unmatched rows
  had outcomes too; you just can't see them).
- **Report the matched-subset rate as the population rate** → silently biased *if coverage is
  non-random* (e.g. matched rows skew toward further-along / more-engaged / higher-income entities).

The coverage % alone doesn't tell you which. "76% coverage" could be perfectly unbiased or badly
skewed — you have to *check*.

## Context / Trigger conditions

- A join/`LEFT JOIN`/bridge that resolves only part of the population and you're about to compute a
  rate over the matched subset.
- The primary record has no FK to the entity you need (so you hunted for a peripheral child table
  that carries both keys — e.g. a supporting-documents / events / junction table).
- You're deciding whether to invest in rebuilding full linkage (expensive, error-prone) vs. accept
  the partial bridge.
- A cross-cohort / cross-cycle / cross-segment estimate where one slice has full coverage and another
  only has the partial bridge.

## Solution

**1. Find the peripheral bridge (if the FK is missing).** When the primary record carries no FK to the
target entity, scan sibling/child tables for one holding *both* keys (`grep` INFORMATION_SCHEMA for
the two id columns co-occurring). A junction/child table is often a usable bridge even when the parent
isn't linked — but it inherits that table's coverage (only rows that produced a child record).

**2. Get an INDEPENDENT full-coverage reference for the same quantity.** The check needs a benchmark
you trust at ~100%. Sources, in order of preference:
   - A different slice/cohort where full linkage *does* exist (e.g. a recent cycle covered by a
     complete identity graph while the older cycle only has the bridge).
   - An aggregate control total from an authoritative source (a reported enrollment count, a billing
     total, a source-system COUNT).
   - The same population via a second, independent linkage path.

**3. Compare the bridged-subset rate to the full-coverage reference.**
   - **Match (within noise)** → the missingness is ~ignorable for *this* outcome (MAR). Report the
     restricted estimate, state the coverage explicitly ("N=X of Y, ~C% coverage, verified unbiased
     against <reference>"). Do NOT rebuild linkage you don't need.
   - **Diverge** → coverage is informative (MNAR); the restricted rate is biased in the direction of
     who-gets-matched. Either recover the missing linkage, or report a **bound** (e.g. assume the
     unmatched are all-0 / all-1 for a worst/best case) rather than the point estimate.

**4. Never let the coverage gap masquerade as a real signal.** If you skipped the check and imputed
unmatched-as-negative, a coverage difference *between segments* will look like an outcome difference.
Segment A at 90% linkage and segment B at 50% linkage will show a fake gap purely from coverage.

## Verification

The MAR check *is* the verification. Additionally: report the covered-N alongside the rate (not just
the %); confirm the reference is genuinely independent of the bridge (not the same join relabeled);
if no independent reference exists, mark the estimate **coverage-limited / unverified** rather than
asserting it.

## Example (a two-cycle funnel with a partial identity bridge)

The primary `application` record has **no foreign key to the `account`** that carries the outcome
label, so the outcome can't be joined directly. A complete identity graph exists **only for the
current cycle**. For the prior cycle the only bridge is a peripheral child table (`supporting_documents`)
carrying both `application_id` and `account_id` — but it covers only part of the outcome records
(say roughly three-quarters).

Rather than treat the uncovered remainder as "did not convert" (undercount) or trust the covered
subset blindly, benchmark on the **current cycle**, where BOTH the bridge and the full graph exist:
if the *bridged-subset* outcome rate matches the *full-coverage* rate within about a percentage point,
the bridge is ~unbiased for this outcome, so the prior-cycle restricted rate is trustworthy with a
stated coverage caveat — no need to rebuild the (expensive, fuzzy) identity graph. Had they diverged,
the prior-cycle number would be reported as bounded/unverified instead.

## Notes

- This is the descriptive-analysis sibling of feature-join-path coverage decisions — but the question
  is different: not "which path / take the union" but "is the *only* available partial link unbiased
  for my estimate."
- Works for any partial-observation setting: record linkage, tracking-pixel coverage, survey response,
  instrumented-vs-uninstrumented users, matched-cohort studies.
- The reference must be *independent*. Benchmarking a bridge against itself (or against a subset of the
  same join) proves nothing.

## References / See also

- `rebuild-transient-state-cohort-from-history` — related snapshot-vs-history censoring trap.
- Missing-data taxonomy: MCAR / MAR / MNAR (Rubin 1976) — the check above distinguishes MAR-enough from MNAR empirically.
