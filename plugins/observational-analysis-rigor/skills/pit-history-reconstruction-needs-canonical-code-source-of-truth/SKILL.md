---
name: pit-history-reconstruction-needs-canonical-code-source-of-truth
description: Use whenever a subagent is asked to do a point-in-time (PIT) or historical-reconstruction join that involves bucketing entities by status codes, stage labels, or category mappings — and the project has a canonical source-of-truth for those code lists (e.g., `stage_codes.py`, `cohort_definitions.py`, a shared constants module). Triggers on "PIT probe", "historical reconstruction", "rewind to date X", "as-of-the-anchor reconstruction", "snapshot vs PIT", "stage-conditioned cut at historical date", or any subagent-dispatched data-pipeline work where status-code-to-bucket mapping is in scope. The orchestrator MUST either (a) bake the canonical code constants into the subagent's prompt verbatim, OR (b) independently re-derive the subagent's headline cells using the canonical constants before promoting any result into a doc, follow-on PR, or downstream decision. Subagents under time pressure use plausible-looking but non-canonical code lists, and PIT joins are sign-flip prone when the code list is wrong. Skipping this discipline can promote a sign-flipped finding into a stakeholder-facing or production-facing artefact. Especially critical for data-eng / ML / dashboard projects where canonical constants live in code and ADRs document the bucketing logic.
---

# PIT-history reconstruction needs a canonical-code source-of-truth

## Why this exists

Point-in-time (PIT) reconstructions answer "what would the data have looked like if we'd queried it on date X?" They're load-bearing for:
- Backtest probes ("did the model see this signal at scoring time?")
- Snapshot-vs-PIT comparisons ("does the snapshot back-stamp future-completers' final status?")
- Stage-conditioned cuts ("entities that were Accepted at the historical anchor enrolled at Y%")
- Cohort reconstructions for retraining decisions

A PIT join typically looks like:

```sql
-- Take the LAST status per entity where change_ts <= anchor
WITH hist_pit AS (
  SELECT entity_id, new_value AS pit_status,
    ROW_NUMBER() OVER (PARTITION BY entity_id ORDER BY change_ts DESC) AS rn
  FROM status_history
  WHERE change_ts <= ANCHOR_DATE
)
SELECT pit_status, COUNT(*) FROM hist_pit WHERE rn = 1 GROUP BY pit_status;
```

The headline finding usually depends on bucketing `pit_status` into named categories ("Accepted", "Deposited", "In Process") via a CASE statement. **That CASE statement is where the bug lives.** If the subagent's CASE uses status codes that differ from the project's canonical list, the bucket populations shift — sometimes by 2-3×, sometimes sign-flipping the headline finding.

This isn't subagent incompetence. It's a structural problem: subagents under time pressure look at sample data, infer status codes from prefixes (e.g., "starts with A → probably Accepted"), and ship. The project's canonical list often has codes like `ACC`, `ACF`, `ACO`, `OFR`, `OFC`, `RVA`, `ADN` — most of which a sampling-based inference would miss.

## When to invoke

Trigger any of these conditions:
- About to dispatch a subagent to do a PIT-history reconstruction with status/stage/category bucketing
- A subagent has just returned PIT results with a stage-conditioned cut and the headline is about to be promoted into a doc
- The subagent's PIT result "disagrees with prior work" or shows an extremal value (0%, 100%) — both are sign-flip warning signs
- The project has `_CODES`, `_STATUSES`, `_BUCKETS`, or `_CONSTANTS` constants in source code that the subagent may or may not have used
- About to ship a PIT result into a stakeholder-facing, client-facing, or production-facing artefact

Do NOT skip this discipline because:
- The subagent reported high confidence (subagent confidence is calibrated against its own SQL, not against canonical project definitions)
- The PIT result "looks plausible" (sign-flipped numbers often look plausible — that's why they're dangerous)
- "We're under time pressure" (the discipline takes ~5 minutes; cleanup from a shipped sign-flip takes hours and damages stakeholder trust)

## The pattern — two valid paths

### Path A: Bake canonical constants into the subagent prompt up-front (preferred)

Before dispatching, grep the project for the canonical code constants:

```bash
grep -rn "_ACCEPTED_CODES\|_IN_PROCESS_CODES\|_CLOSED_CODES\|STAGE_" \
  --include="*.py" --include="*.sqlx" path/to/project/
```

Then paste the canonical constants verbatim into the subagent's prompt:

```
## Canonical status-code constants (from stage_codes.py)

_ACCEPTED_CODES = {"ACC", "ACF", "ACO", "OFR", "OFC", "ADN"}
_IN_PROCESS_CODES = {"REV", "PND", "RVA", "HLD", "WLT"}
_DEFERRED_CODES = {...}
_CLOSED_CODES = {"CLD", "REJ", "WDN", "EXP", "XFR"}
# APP → Applied-Not-Submitted
# Deposited = _ACCEPTED_CODES AND deposit_status IN ('P','W')

USE THESE EXACTLY in your bucket CASE statement. Do NOT infer from
status-code prefixes; the canonical list is authoritative.
```

The subagent then has no excuse to use a non-canonical list.

### Path B: Independently re-derive the headline cells (mandatory if Path A wasn't done)

If you didn't bake the constants in up-front, you MUST re-derive every headline cell yourself before promoting:

1. Find the canonical constants (same grep as Path A).
2. Re-run the subagent's query with the canonical CASE statement.
3. Compare cell-by-cell.

If the cells match → safe to promote.

If the cells don't match → STOP. The subagent's result is contaminated. Treat as a `[METHODOLOGY-DISPUTE-UNRESOLVED]` finding (see the `review-panel-pre-dispatch-claim-recheck` skill for the doc-rewrite procedure).

## Why subagents drift on this

Several converging pressures:

- **Sample-based inference is fast.** Subagents look at 10 rows of `status_code` values, see codes like `REV` and `WLT`, conclude "REV probably means In Review, WLT is Waitlist" — and miss less-obvious codes like `RVA` (also In Process) or `ADN` (Accepted-No-Deposit-yet).
- **Time-boxed probes reward broad strokes.** A subagent given 8 minutes will favour "get a reasonable-looking result" over "verify against source-of-truth."
- **Canonical constants live in `.py` files the subagent didn't read.** The subagent reads the table schema and infers; the canonical mapping lives a few directories away in a shared constants module like `feature_common/stage_codes.py`.
- **PIT joins amplify the error.** A snapshot query bucketed wrong has visible errors (totals don't add up); a PIT join bucketed wrong looks internally consistent because the join itself worked — only the bucket *contents* are wrong.

## Companion skill: grain mismatch

PIT joins have a SECOND drift source beyond status codes: **grain mismatch.** Production datasets typically pick ONE primary entity per parent (one primary application per applicant); subagent PIT joins typically use `ARRAY_AGG + EXISTS` over ALL entities per parent. Two different selection rules, two different answers, neither directly comparable.

This is a known follow-on issue. When the canonical-codes fix surfaces a remaining disagreement between PIT and snapshot, the second-order suspect is grain selection. Resolution requires:

1. Find the production query's primary-entity selection rule (typically a `ROW_NUMBER() OVER (PARTITION BY parent_id ORDER BY ...)` or a service-layer filter)
2. Replicate it in the PIT path on both sides
3. Re-compare

If grain selection isn't locked down in time, mark the comparison `[METHODOLOGY-DISPUTE-UNRESOLVED]` and defend the headline on non-PIT probes only.

## Worked example — a PIT-Accepted near-miss

During a board-defense review session, a subagent ran a PIT-history per-stage cut and reported:

> PIT-Accepted at the historical anchor: n_att≈240, enrolled=0, **rate=0.00%**

This was promoted into the doc as "snapshot back-stamps Deposited onto future-depositors; the residual Accepted bucket is dead-on-arrival."

The orchestrator independently re-derived using the canonical `_ACCEPTED_CODES = {"ACC","ACF","ACO","OFR","OFC","ADN"}` from `stage_codes.py`. Result:

> PIT-Accepted-Not-Deposited at the same anchor: n_att≈**360**, enr≈78, **rate≈22%**

Opposite direction. The subagent had used a non-canonical list (likely something like `('A','AC','AS','CA')` inferred from sample prefixes) that silently bucketed roughly 120 real Accepted entities into an "Other-Unknown" residual. The 0% finding was an artefact of wrong code-mapping, not a real signal about snapshot back-stamping.

**Resolution:** marked that probe `[METHODOLOGY-DISPUTE-UNRESOLVED]`, rewrote the affected divergence in the reconciliation section, removed the "snapshot back-stamp is a class of bug" lesson candidate (it had been built on the wrong PIT number), defended the finding on the surviving probes only. A subsequent reviewer-panel run had a reviewer independently re-derive and confirm the orchestrator's fix.

## What "the canonical constants" look like in practice

Different projects, different idioms:

```python
# Admissions-funnel project — stage_codes.py
_ACCEPTED_CODES = {"ACC", "ACF", "ACO", "OFR", "OFC", "ADN"}
_IN_PROCESS_CODES = {"REV", "PND", "RVA", "HLD", "WLT"}

# CRM-style project — lead_lifecycle.py
LEAD_STAGES = {
    'mql': ['marketing_qualified', 'engaged'],
    'sql': ['sales_qualified', 'sales_accepted'],
    'opp': ['opportunity_open', 'opportunity_negotiating'],
}

# Healthcare project — patient_status.py
ENCOUNTER_STATUSES = frozenset(['SCHED', 'CHK_IN', 'IN_VISIT', 'CHK_OUT', 'CANCEL'])
```

Find the equivalent in your project. Grep for `frozenset`, `_CODES`, `STATUSES`, `BUCKETS`, `STAGES`. They're nearly always at module-level in a shared constants file.

## When NOT to apply this pattern

- Raw row-count probes that don't bucket by status (e.g., "how many rows in this table at this date?")
- Probes where the bucketing happens via a published view that the subagent SELECTs from (the bucketing is encoded in the view, not in the subagent's CASE)
- Snapshot-only queries (no PIT join; canonical-codes drift still possible but lower-stakes)
- Projects without canonical code constants (rare in mature data projects; if true, this skill doesn't apply — but use `review-panel-pre-dispatch-claim-recheck` instead)

## Sequencing notes

- **Path A is preferred.** Baking canonical constants into the subagent prompt is cheaper than post-hoc validation.
- **Path B is mandatory if Path A wasn't done.** Promoting a subagent's PIT result without re-derivation is the failure mode this skill exists to prevent.
- **Pair with `review-panel-pre-dispatch-claim-recheck`** when the PIT result is about to feed into a review panel.
- **The fix when Path B fails** is documented in `review-panel-pre-dispatch-claim-recheck` Step 4 — mark the section unresolved, defend on surviving probes, rewrite downstream prose.

## Provenance

Pattern discovered live during a board-defense review session when an agent-review-panel pre-dispatch re-derivation surfaced a sign-flipped PIT-Accepted finding in a per-stage attendance probe. The subagent had used a non-canonical status-code list; the canonical list lived in a shared constants module (`feature_common/stage_codes.py`) that the subagent never read. The fix saved the document from shipping with a stakeholder-facing sign-flipped headline. The same failure-pattern (subagent using a non-canonical filter, orchestrator promoting without re-derivation) had occurred in a prior session — a subagent's ~50%/~6% split that couldn't be reproduced — so this skill captures the recurring lesson to keep future projects from relearning it.
