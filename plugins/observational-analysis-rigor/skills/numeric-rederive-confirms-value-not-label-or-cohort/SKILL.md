---
name: numeric-rederive-confirms-value-not-label-or-cohort
description: |
  When you have independently re-derived a subagent's / analysis doc's / PR claim's number
  "to the digit" and it MATCHED — and you're about to treat the finding as verified. The trap:
  arithmetic re-derivation confirms the VALUE only; it is STRUCTURALLY BLIND to a wrong
  label / cohort / conditioning / denominator / definition. A number can reproduce EXACTLY
  while meaning something else entirely, so "it re-derives" is NOT "the claim is true." Use when:
  (1) you re-ran a query/computation, got the same value, and feel the finding is now confirmed;
  (2) verifying analysis findings, fairness stats, dashboard chart values, funnel rates, or any
  claim that pairs a number with a noun-phrase ("engagement rate", "forward apply→enroll", "deposit
  when engaged"); (3) folding subagent-reported numbers into a ledger / client deliverable;
  (4) a re-derivation matches but an adversarial reader (advisor, second agent, the user) reads
  the same number differently. The fix: verify what each number MEANS against the source — its
  label, the cohort it's computed over, what it's conditioned on, its denominator — not just that
  the value reproduces. See also: dashboard-metric-label-vs-sql-definition (label vs backing SQL),
  verifier-rederive-from-raw-not-the-checked-artifact (source expectations from raw, not the
  artifact), blind-rederive-pass-when-orchestrator-already-read-the-answer (independence of the
  re-derivation), audit-derivation-layer-before-claiming-data-gap.
author: Claude Code
version: 1.0.0
date: 2026-06-28
disable-model-invocation: true
---

# Re-deriving a number to the digit confirms its value, not its meaning

## Problem
You independently recompute a subagent's (or doc's, or PR's) number from raw, it matches **exactly**,
and you mark the finding verified. But every failure mode in a numbers-heavy analysis is not arithmetic —
it is **semantic**: the value is right, the **label / cohort / conditioning / denominator** attached to it
is wrong. Re-derive-to-the-digit is built to catch arithmetic; it **cannot** catch "correct number, wrong
meaning," because when you reproduce the value you reproduce it *under the producer's framing* — you confirm
the framing instead of testing it.

## Context / Trigger conditions
- You re-ran the query/computation, got the same value, and are about to call it confirmed.
- Verifying analysis findings, fairness/subgroup stats, dashboard chart values, funnel/conversion rates —
  anything where a number is paired with a noun-phrase.
- Folding subagent-reported numbers into a ledger, dashboard, or client-facing deliverable.
- A re-derivation matched, but an adversarial reader (advisor / second subagent / the user) reads the same
  number to mean something different.

## Solution
For each number you're about to trust, verify the **meaning**, not just the value, against the source:
1. **Label** — does the noun-phrase match what was computed? (e.g. `~25%` was reported as an *engagement
   rate*; the source query computed *deposit-rate-among-the-engaged* — same digits, opposite meaning.)
2. **Cohort** — over what population? (e.g. an "early-cohort" deposit figure paired in one sentence with an
   "all-cohort" engagement figure — a **cohort seam**; the subgroup `n` that doesn't reconcile is the tell.)
3. **Conditioning / denominator** — rate of what over what? `X|Y` vs `X|Z`; marginal vs within-stratum;
   forward-only vs gross; not-engaged→engaged progression vs a single rate.
4. **Provenance** — is the label traceable to the source query/CSV, or only to the producer's prose summary?
   Prose summaries are where the relabel happens; read the analyze.py / SQL / state CSV, not the README's
   one-line gloss.
5. Treat **disagreement from any adversarial reader** as a meaning-bug until reconciled — don't assume your
   matching digits settle it.

## Verification
You've actually verified the claim (not just the arithmetic) when you can state, from the source: the exact
label, the cohort/filters, and the conditioning/denominator — and they match how the number is used on the
surface it lands. If you can only say "the value reproduces," you have verified nothing about the claim.

## Example
A real observational-analysis session — every subagent miss was a correct number with a wrong meaning, and
each survived digit-level re-derivation:
- `~25% / ~40%` reported as "engagement rate is lower for subgroup B" → actually **deposit-when-engaged**
  rates; engagement rates were ~equal. (Shipped to a client dashboard before an adversary reader caught it.)
- "`~20×` larger pool" → that was the *total-population* ratio mislabeled onto the *not-engaged* pool (really ~2× that,
  ~40×).
- A NOT-FEASIBLE verdict whose underlying counts were right but whose *conclusion-label* was wrong (it had
  scanned only the main object + its history table, missing a child object).
- The first fix introduced a fresh **cohort seam** (early-cohort deposit paired with all-cohort engagement),
  caught only because a reviewer flagged a non-reconciling `n`.
Each "verified to the digit." None was an arithmetic error.

## Notes
- This is the structural complement to `verifier-rederive-from-raw-not-the-checked-artifact`: that one says
  *re-derive from raw, not the artifact under test*; this one says *even a from-raw match only proves the
  value — separately interrogate the label/cohort/conditioning.*
- Strongest catch mechanism is an **adversarial reader who interprets the number independently** (advisor,
  a second subagent given the question not the number, the user). Convergence on a value ≠ convergence on
  meaning.
- Cheapest preventive: when a subagent reports `X% = <phrase>`, open its committed SQL/CSV and confirm the
  GROUP BY / WHERE / numerator-denominator literally produce that phrase before you propagate it.
