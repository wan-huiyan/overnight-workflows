# Phase 0 — Preparation

Goal: produce every input both tracks will need, so the tracks themselves don't have to
re-derive shared context. Roughly 60–90 min of work. **Eight** sub-tasks — do 0.-1 first, then 0.0.

## 0.-1 Credential & environment liveness pre-flight (do ABSOLUTELY FIRST)

**Why this exists (learned the hard way in v1.0.1):** Track B of a production run burned
its entire background-agent dispatch on an expired Google ADC refresh token. The agent
hit `google.auth.exceptions.RefreshError: Reauthentication is needed. Please run 'gcloud
auth application-default login'` on its first BigQuery call and could not resolve it —
that command requires an interactive browser flow a background subagent cannot complete.
Cost: one full 6.5-hour Track B budget reduced to 0 findings. **2 minutes of pre-flight
would have caught this.**

Before launching ANY autonomous Phase A subagent, verify in the foreground session:

| Check | Command | Pass criteria |
|---|---|---|
| ADC token works | `gcloud auth application-default print-access-token` | Prints a token, no `RefreshError` |
| BigQuery reachable | `bq ls -p <project>` | Returns dataset list, not auth error |
| Scratch dataset exists | `bq ls -d <project>:<scratch_dataset>` | No "Dataset not found" |
| Required tool in PATH | `which python3 && which bq && which gh` | All three resolve |
| Python libs importable | `python3 -c "import google.cloud.bigquery, shap, xgboost, pandas, pyarrow"` | No ImportError |
| LLM API key reachable | `[[ -n "$ANTHROPIC_API_KEY" ]] && echo ok` (or project-specific) | Prints `ok` |

If any fail, fix in the foreground session. Background agents cannot recover interactive
auth flows. A failed agent dispatch on an expired token wastes the wall-clock budget slot
AND leaves a misleading commit trail that looks like the agent tried to work.

Commit `scoping/preflight_results.md` with the output of each check. Acts as audit
evidence that Phase A launches were legitimately blocked-or-not.

## 0.-1.1 Mid-run auth liveness probe (v1.4.0)

**Why this exists:** Phase 0.-1 (v1.1.0) catches auth expiry at *start of run*, but
not mid-run. GCP ADC access tokens last ~1 hour; refresh tokens can be revoked or
rotated by the IdP at any time. A tiered failure mode — "probe OK at T=0, token
dies at T=3h, Track B burns 3h more on silent auth errors" — is documented in
[Why AI Agents Keep Failing in Production](https://medium.com/data-science-collective/why-ai-agents-keep-failing-in-production-cdd335b22219)
as "authentication rot," the #1 silent production failure.

**Contract:** the orchestrator schedules two re-probes during the run:

| Probe | Trigger | Action on failure |
|---|---|---|
| T+2h | Wallclock from run start | `AUTH_ROT_ABORT` artifact + SIGTERM both tracks |
| T+5h | Wallclock from run start | `AUTH_ROT_ABORT` artifact + SIGTERM both tracks |
| Any in-flight BQ `RefreshError` | Reactive | Same — don't wait for next scheduled probe |

Each probe runs the same 6-check battery as §0.-1 (ADC, BQ, dataset, PATH, libs,
LLM key) and appends to `state/auth_probes.jsonl`:

```json
{"ts":"2026-04-17T03:00:00Z","probe":"T+2h","checks":[{"adc":"PASS"},{"bq":"PASS"},...]}
```

**On abort** (`AUTH_ROT_ABORT`):

1. Write `state/AUTH_ROT_ABORT.json` with `{triggered_at, probe, failed_checks[]}`.
2. Flush in-flight work: both tracks get SIGTERM (30s grace) — findings already
   committed to `state/findings/` are preserved.
3. **Do NOT auto-respawn.** Unlike meltdown, auth rot means fresh subagents will
   hit the same dead credential. Run goes into `PAUSED_AUTH_ROT` state.
4. morning_summary.md §1 Headline flags this with: "Run paused at T+Xh due to
   credential expiry. Findings collected before abort: N. Re-run after `gcloud
   auth application-default login` in foreground."

**If `ANTHROPIC_API_KEY` is set via short-lived token (e.g. Vertex/Bedrock),**
also include an LLM-API smoke call in each probe — not just key-present check.
One-token completion suffices; failure = rotation happened mid-run.

**Graceful refresh (future — v1.5):** pre-emptive `gcloud auth application-default
print-access-token --force-auth-refresh` at T+2h could refresh the ADC without
aborting. Deferred because (a) force-refresh still requires a valid refresh
token, which is what expires in the common failure mode, and (b) runs in
Workload-Identity-Federation environments don't need it (auto-rotation). Revisit
after the first v1.4 run.

## 0.0 Schema reality check (do FIRST after 0.-1 — cheap insurance, catches ~1 session of rework)

Before writing ANY SQL that downstream tasks depend on, query `INFORMATION_SCHEMA.COLUMNS`
for every canonical table this run will touch. Commit the output as
`scoping/schemas.md`. This catches the single most expensive class of plan-vs-reality
mismatch: **wide-vs-long schema**.

### Why this step exists

Phase 0 originally only verified row counts ("data window available: N days, M rows").
That's necessary but not sufficient. A table can have the expected row volume while
having a completely different column shape than the plan assumed. The classic failure:

- Plan's SQL: `SELECT score, term, bucket FROM predictions` (assumes long-format)
- Actual schema: `enrolled_term_fall_score`, `enrolled_term_spring_winter_score`,
  `enrolled_term_summer_score`, `propensity_bucket_fall`, ... (wide)
- Row counts match the plan, but every downstream query written against long-format
  breaks. This cascades through Tasks 3–N and is typically caught mid-Task-3 after 2+
  hours of Phase 0 work.

The v1.0.0 run caught this the hard way: the stitched-score-view SQL materialized
"successfully" on wide source data, producing 0 usable rows because the SELECT
referenced non-existent columns (`term`, `score`, `bucket`). The downstream plan
assumed the view was long-format; it wasn't. Cost: one retry cycle + a blocker escalation.
**Adding 0.0 prevents this.**

### What to query

For every table the plan will hit (predictions, enriched, SHAP, training features,
identity maps, stitched view sources):

```sql
SELECT column_name, data_type, ordinal_position
FROM `<project>.<dataset>.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = '<table>'
ORDER BY ordinal_position
```

Capture the full column list for each. Takes ~5 seconds per table.

### What to document in `scoping/schemas.md`

One section per table, covering:

| Field | Contents |
|---|---|
| **Shape** | "Wide (per-term columns)" / "Long (has `term` row dimension)" / "Hybrid" |
| **Per-term pattern** | If wide: list the per-term column prefixes/suffixes (e.g., `enrolled_term_*_score`). If long: name the term column (e.g., `term_category`). |
| **Primary key** | The minimal tuple that uniquely identifies a row (e.g., `(scoring_date, visitor_id)` for wide-per-term; `(scoring_date, visitor_id, term_category)` for long). |
| **Term-independent columns** | Columns the plan can safely join on without term filtering (e.g., `application_status`, `funnel_stage` in enriched tables). |
| **Term-dependent columns** | Columns that need UNPIVOT or term-filtering to use downstream. |
| **Null behavior** | When is a per-term column NULL? (e.g., "Spring/Winter scores NULL mid-season because that term isn't being scored right now.") |

### Pattern decisions that flow from 0.0

Once schemas are documented, decide the query-shape policy up front:

- **If any canonical table is wide**: build a stitched view that UNPIVOTs via UNION ALL,
  and mandate that every downstream query go through the view. Never let downstream
  queries touch the raw wide table directly — they'll miss the term dimension and
  break silently.
- **If enrichment tables are wide but term-independent cols are all that's needed**:
  join enriched on `(scoring_date, visitor_id)` AFTER filtering the stitched view by
  term. This is cleaner than UNPIVOTing enrichment twice.
- **If a canonical table is already long**: check it has the expected term row-dimension
  name (different projects use `term`, `term_category`, `term_code`, `cohort_term`,
  etc.). Downstream queries need to know which.

Document these decisions in `scoping/schemas.md` under "Canonical query patterns" so
every downstream task references one place for the right join/filter shape.

### Rule of thumb

If 0.0 takes more than 30 min, either the scope has too many tables (narrow the
canonical set) or the schemas are genuinely complex (in which case 0.0 paid for itself
5× — commit the notes and move on).

### Data-depth pre-flight (v1.5.0)

After documenting schemas, run a row-count check per panel and source table before
writing any downstream SQL:

```sql
SELECT target_date, COUNT(*) n_rows
FROM `<project>.<dataset>.<table>`
WHERE target_date IN (<panel_dates>)
GROUP BY target_date
ORDER BY target_date;
```

**Block rule:** if any plan-committed panel returns 0 rows from any referenced source
table, STOP. Do not draft the stitched view, do not launch tracks. Resolve the
data-window gap first — consult `canonical_numbers.md §Data-window blocker` or
escalate to user. Committing a plan that runs against empty panels wastes the entire
overnight budget.

This takes ~5s per table per panel date. It catches the pattern where cross-year
panels (e.g. `2024-03-16`) are assumed available but the source table's
`MIN(target_date)` is 2025-02-23 — a mismatch discovered only after the stitched
view is half-drafted in a prior run.

## 0.1 Scoping config

Write `scoping/config.yaml` with:
- `run_id` (dated, e.g. `2026-04-17-ahha-v1`)
- `target_date` — pin a fixed date string. Never let downstream queries use `CURRENT_DATE()` — the run may cross midnight UTC + mid-run data writes will cause silent drift.
- `target_term` and `target_term_label` — which term are we analysing
- `lookback_days_default` (30) and `lookback_days_shap` (often much smaller — check actual SHAP coverage)
- `budget`: `bq_total_tb`, per-track split, `wall_clock_total_hr`, per-track split
- `ban_patterns`: regex list for things the qa-expert persona will fail on (e.g. `CURRENT_DATE\(\)`)
- Paths to the stitched view + scratch dataset

See `assets/config_yaml_template.yaml` for a starter.

## 0.2 Canonical numbers

Write `scoping/canonical_numbers.md`. This is the data-analyst review persona's
ground truth — every claim in both briefs will be cross-checked against this file.
Verify the numbers LIVE with a `bq query` before committing; do NOT paste memorized values.

Include:
- Model version(s) active on target_date
- Total rows on target_date (count)
- Data window available with `MIN(date)`, `MAX(date)`, `COUNT(DISTINCT date)`
- Distribution of every cohort key the novelty gate uses (e.g. distinct values of `application_status`, `bucket`, `acceptance_variant`)
- Source-of-truth definitions for derived labels, especially outcome labels. If the project has a "deposit status vs enrollment" confusion, call it out explicitly.

See `assets/canonical_numbers_template.md` for structure.

## 0.3 Stitched score / outcome view

If the project has had multiple model versions, raw probabilities cannot be
stitched across versions. Stable units:
- **Bucket membership** (if buckets are percentile-based, the cuts are stable but
  individual membership can flip at version boundaries — flag this for reviewers)
- **Per-day percentile rank**
- **Enrollment / conversion outcome** — completely model-independent; preferred for
  longitudinal analyses
- Raw probability — NOT stable; use only within a single model_version window

Build one BQ view (or equivalent) that exposes all three stable units alongside
`model_version` so every downstream query can annotate or filter by version
without re-deriving. Rename the raw probability column something like
`raw_score_DO_NOT_STITCH` as a lint flag the qa-expert persona will catch if
misused.

Also normalise any historical label renames in this view (e.g. legacy
`Hot/Warm/Cool/Cold` → current `High/Developed/Emerging/Low`) so downstream queries
don't have to.

### Cohort-completeness probe (v1.5.0)

After materialising the stitched view, run a CASE-exhaustiveness check before any
track queries it. For each derived categorical column (e.g. `funnel_stage_derived`),
query for rows landing in the fallthrough (`ELSE`) bucket:

```sql
SELECT panel,
       funnel_stage_derived,
       COUNT(*) n_rows,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY panel), 4) AS share
FROM `<project>.ml_scratch.<stitched_view>`
GROUP BY panel, funnel_stage_derived
ORDER BY panel, n_rows DESC;
```

**Flag rule:** if any panel has > 1% of rows in the fallthrough bucket
(`funnel_stage_derived = 'Other'` or equivalent `ELSE` label), STOP. Do not launch
tracks. Diagnose the unmatched input codes, add CASE branches, re-materialise, and
re-probe. Log the root-cause fix in `scoping/orchestrator_decisions.md`.

**Why this exists:** the v3 run's `stitched_view.sql` had a silent fallthrough —
`application_status_code IS NULL AND enr_dep_status IN ('P','W')` (deposited students
without a formal application status code) mapped to `'Other'` instead of
`'Deposited'`. The Deposited cohort was therefore understated in both panels. The
probe would have surfaced this at Phase 0 instead of at PR review.

See `references/stitched_view_case_completeness.md` for the full diagnostic sub-skill.

## 0.4 Compute SHAP for cohort known-knowns

If the primary model isn't yet scoring today's serving data (freshly-promoted
model, Dataform serving-features not yet rematerialised, etc.), you may not have
SHAP on current data at all. Fall back to **training-features SHAP**:

1. Load the model's joblib from GCS/equivalent.
2. Read a stratified sample (~50K rows per term sub-model) from the training
   features table.
3. Run `shap.TreeExplainer(model).shap_values(X)`.
4. Aggregate per `(cohort_dim, cohort_value, feature)` → `mean_abs_shap` + sign.
5. Take top-N (typically 20) within each cohort cell. Write to
   `scoping/known_knowns_by_cohort_<model_version>_training.jsonl`.

Also write full per-row SHAP to a scratch table (`<scratch>.<model>_training_shap_snapshot_<date>`) so it can be re-queried later without re-computing.

This takes ~5–30 min depending on sample size and model complexity. Budget
<100 GB read for a ~50K sample.

## 0.5 Build consolidated known-knowns JSONL

Merge sources into `scoping/known_knowns_by_cohort.jsonl`:

Priority order:
1. Current-model training SHAP (if available) — primary for new features
2. Prior-model serving SHAP (if available, e.g. 7-day window) — reinforcement for shared features
3. Current-model training-time feature importance from the joblib — global-only fallback
4. Hard-coded traps: percentile-bucket identity, tautologies, definitional orderings. Include one row per trap with `cohort_dim: "_trap"` so the reviewer can filter them distinctly.

Row schema: `{cohort_dim, cohort_value, feature, family, rank_within_cohort, mean_abs_shap, direction, source}`.

Also write `scoping/feature_families.jsonl` — ~20–40 families grouping raw features
with their band/level variants (e.g. `accepted_nd_tenure` family includes the raw
feature + its band-0-30d / 31-90d / 91-180d / 180plus encodings). Prevents the
novelty gate from being fooled by near-duplicate features.

See `references/cohort_novelty_gate.md` for the novelty-matching logic itself.

## 0.6 Extract new-feature CTE snippets

If the project's production serving-features table is missing some features the
tracks will want (e.g. very recently added), extract the derivation CTEs from the
training-features SQLX/DBT/etc. into `scoping/new_feature_cte_snippets.sql`.

Tracks can then inline these CTEs into ad-hoc queries to access features from raw
sources (Salesforce tables, campaign tables, etc.) without waiting for a new
materialisation.

Include a header comment in the snippets file noting what's attenuated by
snapshot-vs-SCD limitations (e.g. Salesforce status fields reflect "current"
state, not historical; campaigns are upsert-mode). Reviewers will use this to
flag attenuation-risk claims.

## 0.7 Commit & push, then launch tracks

One commit with everything from Phase 0. Push to the overnight branch. Tracks
read from disk going forward — no context pass-through needed.

## Common pitfalls

- **Skipping 0.0 (schema reality check)** because "we know the schema." The v1.0.0 run
  learned this the hard way: plan assumed long-format `term_enrollment_daily`; actual
  was wide (per-term score columns). Row counts matched, so the mismatch wasn't
  caught until Task 3 materialized the stitched view against the wrong schema. Cost:
  one retry + blocker escalation. 0.0 adds ~15 min up front to save ~2 hours of
  mid-Phase-0 rework. Always run it.
- **Using `CURRENT_DATE()` somewhere in Phase 0**. Pin everything to `target_date`
  from the config. Grep for it before committing.
- **Listing every feature in known-knowns**. Top-20 per cohort is enough. More
  creates noise + slow novelty lookups.
- **Skipping `canonical_numbers.md`**. Without it, the data-analyst review persona
  has nothing to verify against. Every subsequent review becomes less useful.
- **Hardcoding a numeric value you didn't verify live**. Run the `bq query` first,
  paste the result, commit together.
- **Fancy stitched-score algorithms**. Simpler is better. Bucket / percentile /
  outcome are enough; don't derive a new continuous scalar across versions.
- **Writing the stitched-view SQL before 0.0 is done**. The view's shape depends on
  whether the source is wide or long. Writing it first locks in an assumption that
  may need a rewrite.
