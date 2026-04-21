# Gotcha — SF `profile` is per-cycle, not per-person

**Added v1.6.0 (S98, 2026-04-21).** Empirically verified S92 C10 (2026-04-17).

---

## The pitfall

`bloomreach_imports.salesforce_adm_application.profile` **regenerates per
application cycle**. It is not a stable person-level identifier.

Verified:

| Recruitment year | Distinct profiles | Overlap with other year |
|---|---:|---:|
| 2024 | 32,090 | 0 |
| 2025 | 36,389 | 0 |

Zero intersection. Treating `profile` as a cross-year identity key → 0%
re-applicant rate by construction. This is exactly the pathology that killed
S92 C10.

## What NOT to do

Do **not** use `profile` to join students across recruitment years:

```sql
-- WRONG — will return 0 rows for any cross-year analysis
SELECT a.profile AS prior_year_profile
FROM app_2024 a
JOIN app_2025 b ON a.profile = b.profile
```

## Phase 0.0 — mandatory cross-year identity probe

Any candidate that requires cross-year student identity resolution must pass
the following probe **before dispatch**:

```sql
SELECT
  COUNT(DISTINCT a.profile) AS n_2024,
  COUNT(DISTINCT b.profile) AS n_2025,
  COUNTIF(b.profile IS NOT NULL) AS n_overlap
FROM <2024_slice> a
FULL OUTER JOIN <2025_slice> b USING (profile);
```

If `n_overlap = 0` → the candidate is **blocked**. Tag findings
`[BLOCKED — cross-year identity]` and do not dispatch.

## Upstream identity alternatives

When cross-year identity resolution is required, candidates are:

| Source | Join key | In v10 pipeline? |
|---|---|:-:|
| SF Account | `LOWER(salesforce_adm_account.id)` | ✅ |
| Banner student ID | `student_id` (not currently joined) | ❌ |
| SF Contact | Contact record | ❌ |

Confirm the upstream identifier is present in the stitched view **before**
committing a candidate to re-applicant logic.

## One-liner for morning_summary §4

> SF `profile` verified per-cycle; cross-year candidates now gated behind an
> upstream identity probe.
