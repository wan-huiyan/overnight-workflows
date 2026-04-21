# Sub-skill: stitched-view-case-completeness (v1.5.0)

A small diagnostic sub-skill for enumerating CASE branches in any derived view and
verifying that the ELSE fallthrough is below an acceptable threshold. Run during
Phase 0.3 (after view materialisation) before any track queries the view.

## When to run

- After every `CREATE OR REPLACE TABLE` or `CREATE OR REPLACE VIEW` that includes a
  `CASE` expression mapping raw source codes to derived labels (e.g. `funnel_stage`
  from `application_status_code` + `enr_dep_status`).
- After any schema change to the upstream source table that could introduce new enum
  values not covered by existing CASE branches.

## Step 1 — Enumerate the CASE branches

Read the `.sql` file that defines the view. For each CASE expression, list all
`WHEN ... THEN` branches and the `ELSE` label. Document in a table:

| Branch | condition summary | output label |
|--------|------------------|--------------|
| 1 | `status_code IS NULL AND dep_status IS NULL` | No App |
| 2 | `status_code IS NULL AND dep_status IN ('P','W')` | Deposited |
| 3 | `status_code = 'NS'` | Applied-Not Submitted |
| … | … | … |
| N | ELSE | Other |

## Step 2 — Query for fallthrough rows

```sql
-- Replace <view>, <panel_col>, <derived_col>, <else_label> as appropriate
SELECT
  <panel_col> AS panel,
  <derived_col> AS label,
  COUNT(*) AS n_rows,
  ROUND(COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY <panel_col>), 4) AS share
FROM `<project>.<dataset>.<view>`
GROUP BY panel, label
ORDER BY panel, n_rows DESC;
```

Commit the output as `scoping/case_completeness_<view_name>.md`.

## Step 3 — Apply the flag rule

| Outcome | Action |
|---------|--------|
| All panels: ELSE share ≤ 1% | Green. Proceed to Phase A launch. |
| Any panel: ELSE share 1%–5% | Yellow. Investigate but may proceed if the codes are genuinely miscellaneous (e.g. legacy/rare status codes not in active use). Document rationale in `orchestrator_decisions.md`. |
| Any panel: ELSE share > 5% | Red. STOP. Add missing CASE branches, re-materialise, re-probe. |

## Step 4 — Diagnose unmatched codes

If ELSE share is above threshold, query for the raw input code combinations landing
there:

```sql
SELECT
  application_status_code,
  enr_dep_status,
  COUNT(*) n_rows
FROM `<project>.<dataset>.<source_table>`
WHERE target_date IN (<panel_dates>)
  AND <derived_col> = '<else_label>'   -- filter on the stitched view join
GROUP BY 1, 2
ORDER BY n_rows DESC
LIMIT 30;
```

Cross-reference the results against the client reference doc (see Phase 0.2a —
client reference-doc scan). Common root causes:

- **New application status sub-codes** not present in the original legend (added by
  the SIS vendor without announcement).
- **NULL status + non-NULL dep_status** — deposited students who entered via a
  non-application pathway (test-optional admits, staff waivers, etc.). The fix is
  to add `WHEN status IS NULL AND dep_status IN ('P','W') THEN 'Deposited'`.
- **Legacy status codes** retired from the enum but still present in historical data.

## Step 5 — Re-materialise and re-probe

After adding CASE branches, re-run the `CREATE OR REPLACE TABLE` / `CREATE OR REPLACE
VIEW` statement and repeat Step 2. ELSE share must drop below 1% before Phase A
launches.

## Anti-patterns

- **Treating the ELSE bucket as "miscellaneous" without checking its size.** If 5% of
  your Deposited cohort is mislabelled as "Other", the Deposited findings are
  understated by 5% — a systematic error, not noise.
- **Fixing the CASE in the view but not in the upstream SQLX / DBT source.** If the
  fix doesn't land in the production Dataform / DBT model, the next Dataform run will
  overwrite the view back to the broken state. Fix both together.
- **Skipping the probe for "stable" views.** Views are only as stable as their source
  tables. A SIS export with new status codes will break any view that doesn't have an
  explicit `ELSE` guard, silently.

## Origin

Added in v1.5.0 after the Barry v3 overnight run's `stitched_view.sql` had a silent
fallthrough: `application_status_code IS NULL AND enr_dep_status IN ('P','W')`
mapped to `'Other'` instead of `'Deposited'`. Caught at PR review rather than at
Phase 0, after both tracks had already run. The probe would have caught it in ~30s.
