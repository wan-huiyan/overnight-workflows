# Recipe — Bloomreach-event Tier 1b temporal proxy

**Added v1.6.0 (S98, 2026-04-21).** Companion to
`sf_endpoint_defined_cohort_hazard.md`. When the SF-snapshot hazard blocks a
feature, BR events are often the workaround.

---

## When to use this recipe

A candidate needs to stratify on "student reached SF milestone X by
historical target_date," but the SF column lacks a native timestamp (it's
a current-snapshot boolean or a status field without an event log). Before
declaring the candidate Tier 3 (blocked), check whether a Bloomreach event
table captures the same semantic milestone.

## Worked example — `ready_to_review` / `in_process_missing_credentials`

SF has no timestamp for "student has uploaded all credentials / is ready to
review." S96–S97 found that
`barry-cdp.bloomreach_events_export.file_upload` (27,767 events, 3,122
distinct accounts in 180d window at 2025-03-16) captures the underlying
behaviour: `file_description` enumerates `High School Transcript` /
`College/University Transcript` / `Test Scores` / etc., and each row has a
native `TIMESTAMP`.

This is the S97 `file_upload` scope — canonical CTE at
`docs/overnight/v5_file_upload_proxy_scope.md` in the project repo.

## Workflow checklist (6 steps)

1. **Name the SF milestone.** One sentence. E.g. "student has uploaded all
   required credentials."
2. **Find the BR event.** Query the bloomreach_events_export dataset for
   event names matching the semantic. Common candidates:
   `file_upload` · `step_complete` · `form_submit` · `page_visit` ·
   `campaign_response`.
3. **Verify native `TIMESTAMP`.** `bq show barry-cdp.bloomreach_events_export.{event}`
   → look for a `TIMESTAMP`-typed column (not Unix micros). If Unix micros,
   reach for `TIMESTAMP_MICROS()` — L-S96-2 applies.
4. **Verify join key.** `salesforce_account_id` (BR side) →
   `LOWER(salesforce_adm_account.id)` (SF side). Always `LOWER()`-normalise
   — case mixing in BR vs SF has bitten past runs.
5. **Probe coverage.** Minimum thresholds before adoption:
   - Total events over the panel time window ≥ 1,000.
   - Distinct accounts in the 180d window at target_date ≥ 20% of the
     panel's full row count. (S97 file_upload: 25% — a healthy baseline.)
   - Event activity spans target_date ± 30 days (not just recent).
6. **Build the CTE.** Standard three-feature pattern:
   - `days_since_last_{event}` (sentinel 999 if no events ever) —
     continuous recency.
   - `{event}_count_last_90d` — count-in-window.
   - `{event}_types_last_180d` — `ARRAY_AGG(DISTINCT type)` if the event
     has a categorical `description` / `type` field; else omit.

## Known BR-event inventory (S96 matrix)

The authoritative tier matrix is
`docs/sf_bloomreach_event_proxy_matrix.md` in the project repo. Summary:

| SF milestone | BR event | Tier |
|---|---|:-:|
| `ready_to_review` / credentials complete | `file_upload` | 1b ✅ |
| form progress | `step_complete` (153k events, mostly form-fill) | 2 |
| email response | `campaign_response` | 1b |
| outbound campaign touch | `campaign_received` | 1b |
| (deprecated) clicks on to-do items | `to_do_item_click` — tracking **stopped 2025-11-09** | ❌ |

**Tier 3 (still blocked, no BR proxy):** withdrawal · rejection ·
cancellation · orientation · FA verification · accepted sub-code
transitions. These remain blocked until daily SF snapshotting
(data-eng ticket `docs/tickets/2026-04-20_sf_daily_snapshot.md`) lands.

## Fail-loud contract

If the BR event fails a coverage probe, do NOT stretch coverage with
imputation. Tag the candidate `[BR-PROXY-INSUFFICIENT]` and either
demote the candidate to Tier 3 or reframe descriptively (see Option C
fallback in `phase_c_consolidation.md`).

## One-liner for morning_summary §4

> BR-event Tier 1b proxy recipe codified; use before declaring Tier 3
> when SF lacks a native timestamp.
