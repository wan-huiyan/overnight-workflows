# Canonical Numbers — <project> Overnight Run <YYYY-MM-DD>

Reference numbers verified from live data at scoping time. Every analytical
claim in both tracks' briefs must match these within statistical noise. The
`data-analyst` review persona cross-refs every number against this document.

## Production scoring

- **TARGET_DATE**: YYYY-MM-DD
- **Active model_version**: <model_id> (<version_name>)
- **Total scored rows on TARGET_DATE**: ~<n>
- **Data window available**: YYYY-MM-DD → YYYY-MM-DD (<n> days)

Verify live before committing:
```sql
SELECT model_version, MIN(scoring_date), MAX(scoring_date), COUNT(DISTINCT scoring_date) AS n_days
FROM `<project>.<dataset>.<predictions_table>`
GROUP BY model_version
ORDER BY 2
```

## Outcome label definition

**Do NOT use <deposit_status_field> for enrollment outcome.** Use:

```sql
<source_table>.enrolled = TRUE
AND SAFE.PARSE_DATE('%Y-%m-%d', <date_field>)
    BETWEEN DATE_ADD(DATE '<target_date>', INTERVAL 1 DAY)
        AND DATE_ADD(DATE '<target_date>', INTERVAL <lookforward_days> DAY)
AND REGEXP_EXTRACT(<term_field>, r'<term_regex>$') IN (<term_codes>)
```

Replace `<...>` with project-specific values. Include the exact SQL in this doc;
every reviewer will paste it verbatim.

## Cohort distributions (on TARGET_DATE)

Fill in from a live query:

```sql
SELECT <cohort_dim>, COUNT(*) AS n
FROM <enriched_predictions>
WHERE scoring_date = DATE '<target_date>'
GROUP BY <cohort_dim>
ORDER BY n DESC
```

| cohort_dim | value | n |
|---|---|---|
| funnel_stage | No App | <n> |
| funnel_stage | In Process | <n> |
| funnel_stage | Accepted | <n> |
| funnel_stage | ... | ... |

Repeat one table per cohort dimension listed in `config.yaml#cohort_dims`.

## Bucket definitions

- Percentile cuts: X% / X% / X% / X% (project-specific)
- Current label set: <current_labels>
- Legacy label set (before rename at <date>): <legacy_labels>
- Normalization applied in `stitched_score_view_v1` so downstream queries don't
  have to map them.

## Model features (current)

- Base features: <n>
- New features added in latest retrain: `<f1>`, `<f2>`, `<f3>`, ...
- Features present in TRAINING features table only (not in SERVING yet): `<f4>`, `<f5>`

## SHAP coverage

- Table: `<project>.<dataset>.<shap_daily>`
- Coverage: YYYY-MM-DD → YYYY-MM-DD (<n> days)
- SHAP columns: <n> (one per base feature)

## Known cross-model effects to flag

- Any visible `~` markers in WoW columns from cross-model transition periods
- Any ADR decisions that annotate (not backfill) historical data — call out here
  so reviewers know the trend caveats

## Common pitfalls already documented

- [Link to project analysis_principles.md]
- [Link to any prior session handoffs that touched data-integrity issues]

Replace every `<...>` placeholder before committing. The file is reviewer-facing;
`<angle brackets>` remaining in the committed doc is itself a review-panel fail.
