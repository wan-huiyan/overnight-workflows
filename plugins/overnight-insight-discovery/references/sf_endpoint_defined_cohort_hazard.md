# Gotcha — SF latest-snapshot + endpoint-defined-cohort hazard

**Added v1.6.0 (S98, 2026-04-21).** The biggest structural finding from
S95b–S97. Affects any candidate that gates on "SF state at historical
target_date" without a native timestamp.

---

## The mechanism (one paragraph)

Salesforce BigQuery exports for Barry are **latest-snapshot only** (per
ADR-0009 — no daily snapshotting). Any feature or filter that evaluates
current-snapshot state — `decision_date IS NULL`, `current_status IN
(pre-decision codes)`, `has_accepted_sub_code = 0` — resolves against
**today's** SF row, not the historical `target_date`'s. Consequence: a
"cohort defined at target_date" built with such a gate is actually "that
state at target_date **AND** still in that state today." This is an
**endpoint-defined cohort**, not a point-in-time cohort. Students who
progressed or withdrew between target_date and today silently drop out, so
the cohort's realized yield collapses toward 0% by construction — a
mechanical artefact, not a causal signal.

## S96–S97 evidence (empirical)

| Finding | Cohort | Expected yield | Observed | Verdict |
|---|---|---:|---:|---|
| v4 F05 (In-Process 0% yield) | IMC=1 at target_date 2025-03-16 | ~10% (baseline) | 0% | Falsified — endpoint-defined |
| S97 Probe 5 (IMC as training feature) | IMC=1 rows, label-valid | >0 ever decided | 0/629 | Mechanism confirmed |

Of 629 students flagged `in_process_missing_credentials = 1` at 2025-03-16,
**zero** have `decision_date IS NOT NULL` in the current snapshot.
100% are still pre-decision. This is the mechanism manifesting in a
**training feature**, not just a post-hoc cohort filter.

## Detection probe (drop-in)

Before using any SF-derived binary feature as a primary stratifier, run
this probe against the feature's definition:

```sql
-- Template — substitute {feature} / {panel_date} / {panel_table}
WITH panel_flagged AS (
  SELECT visitor_id, salesforce_account_id
  FROM `{panel_table}`
  WHERE target_date = '{panel_date}'
    AND {feature} = 1
),
decided AS (
  SELECT LOWER(a.id) AS acct_id
  FROM `barry-cdp.bloomreach_imports.salesforce_adm_account` a
  WHERE a.decision_date IS NOT NULL
)
SELECT
  COUNT(*) AS n_flagged,
  COUNTIF(d.acct_id IS NOT NULL) AS n_since_decided,
  SAFE_DIVIDE(COUNTIF(d.acct_id IS NOT NULL), COUNT(*)) AS pct_moved_forward
FROM panel_flagged p
LEFT JOIN decided d
  ON LOWER(p.salesforce_account_id) = d.acct_id;
```

**Interpretation:**
- `pct_moved_forward ≈ 0%` → feature is **endpoint-defined**. Do NOT use as
  primary stratifier. Tag any finding using it `[TEMPORAL-GATE-HAZARD]`.
- `pct_moved_forward > 5%` → feature is point-in-time-valid enough to use
  (threshold from S97 Probe 5 tolerance band).

## Triage rules for the review panel

1. **Primary stratifier** uses an SF-snapshot-gated feature → **demote to
   caveat** or retract. Substitute with a BR-event proxy
   (see `bloomreach_event_temporal_proxy.md`).
2. **Contamination risk** — a finding's *base* cohort (e.g., "No-App")
   silently inherits endpoint-defined contamination from an SF gate
   upstream. Probe the base cohort's move-forward rate; if > threshold,
   tag `[CONTAMINATED]`.
3. **Descriptive cells only** — using an SF-snapshot feature as a
   *descriptive* cohort cell (not as a causal stratifier) is still OK,
   with an explicit caveat that "this cohort is defined using current SF
   state, so yield comparisons across time should not be drawn."

## Durable fix (out-of-scope for skill)

The correct fix is **daily SF snapshotting** in the Dataform pipeline
(ADR-0009 revisit; data-eng ticket
`docs/tickets/2026-04-20_sf_daily_snapshot.md`). Until that lands, this
skill's detection probe + BR-event proxy recipe carry the load.

## Related skill references

- `bloomreach_event_temporal_proxy.md` — Tier 1b BR-event proxy workflow.
- `stitched_view_case_completeness.md` — the ELSE-fallthrough probe (v1.5).
  Different gotcha, same "don't trust snapshot semantics" family.

## One-liner for morning_summary §4

> SF-gated features must pass the endpoint-defined-cohort probe before use
> as stratifiers; descriptive cells stay OK with caveat.
