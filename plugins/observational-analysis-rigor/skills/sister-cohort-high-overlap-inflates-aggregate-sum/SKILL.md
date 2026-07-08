---
name: sister-cohort-high-overlap-inflates-aggregate-sum
description: |
  Decide whether to include a NEW sister/variant cohort in an existing aggregate
  metric (worklist total, "across cohorts" KPI, hero pipeline count) when the new
  cohort has high logical overlap with one of the existing contributors. Use when:
  (1) you're adding a cohort C' that is a sharp/broad/strict/loose VARIANT of an
  existing cohort C (e.g., a "sharp" ≥1-of-top-3-signals cut and a "broad"
  ≥4-of-8-behaviors cut of the same upstream pool, or a "high-confidence" subset
  of an existing alert), (2) the surface has an existing aggregate metric that sums
  sizes across cohorts (often disclaimed as "across cohorts, may overlap" or "not
  deduped"), (3) C and C' have >50% logical membership overlap by design, (4) you're
  tempted to extend the sum naively to include C'. Trap: the existing "may overlap"
  disclaimer is defensible at low overlap (<30%, e.g. cross-funnel-stage cohorts)
  but BREAKS DOWN at high overlap — including C' double-counts most of C's
  contribution and visibly inflates a stakeholder-facing metric. Provides the
  overlap-percent decision matrix (<30% / 30-70% / >70%) + the exclude-with-comment
  recipe + the follow-up issue template for cross-card dedup.
author: Claude Code
version: 1.0.0
date: 2026-05-10
disable-model-invocation: true
---

# Sister cohort with high overlap inflates aggregate sum

## Problem

You're adding a new cohort C' to a dashboard / report / pipeline. C' is a
**sister/variant** of an existing cohort C — same upstream pool, different
filter (sharp vs broad, strict vs loose, top-3-signal vs width-≥4). The
surface already has an aggregate metric M = `sum(size of each cohort)`,
typically disclaimed as "across cohorts, may overlap" or "not deduped" so
readers understand the count isn't a unique-entity total.

The naive extension is `M += size(C')`. The trap:

- **Low overlap (<30%)**: defensible. The disclaimer covers it. Readers
  intuitively read "across cohorts" as "if an entity is in 2 cohorts it
  counts twice". Real new members from C' justify the inclusion.
- **High overlap (>70%)**: **misleading**. C' contributes ~0% new members
  to the pool but inflates the metric by ~size(C'). The disclaimer no
  longer covers it because reasonable readers don't expect a sister
  cohort to be ~95% redundant. The headline metric grows visibly without
  the underlying pipeline growing.

This is a one-way ratchet: once shipped, removing C' from the aggregate
later looks like a bug ("the worklist count dropped 60 records!").
Better to make the right call at introduction time.

## Context / Trigger Conditions

- Implementation plan adds a new cohort card / segment / queue that is
  explicitly a **sharp/broad/strict/loose/sister** variant of an existing
  one (the design ADR usually says "two cards preserve each calibration"
  or "the sister card captures members whose engagement pattern differs").
- The existing dashboard / report has an aggregate KPI like:
  - `worklist_total = sum(cohort sizes)`
  - `pipeline_count = sum(stage counts)`
  - `total_alerts = sum(per-rule alert counts)`
  - `weekly_engaged_users = sum(per-segment counts)`
- The aggregate is rendered as a **hero number** (top-of-page strip,
  stakeholder-facing KPI, executive dashboard tile) — i.e., it gets read at
  a glance without the disclaimer being visible.
- The design doc / analysis doc already quantifies the overlap (e.g.,
  "~90% C∩C' daily overlap" or "Sharp ∩ Broad ≈ 900/1100 = 80%"). If
  it doesn't, **probe the overlap first** — see Step 1 below.

## Solution

### Step 1 — Quantify the overlap BEFORE deciding

If the design doc already has it, use that number. Otherwise probe:

```sql
-- Generic shape — adapt to your cohort definitions.
WITH c AS (SELECT entity_id FROM <C cohort filter>),
     c_prime AS (SELECT entity_id FROM <C' cohort filter>)
SELECT
  (SELECT COUNT(*) FROM c) AS n_c,
  (SELECT COUNT(*) FROM c_prime) AS n_c_prime,
  (SELECT COUNT(*) FROM c JOIN c_prime USING (entity_id)) AS n_intersect,
  -- Overlap as % of the SMALLER cohort (most conservative read)
  ROUND(100.0 * (SELECT COUNT(*) FROM c JOIN c_prime USING (entity_id))
              / LEAST(
                  (SELECT COUNT(*) FROM c),
                  (SELECT COUNT(*) FROM c_prime)
                ), 1) AS overlap_pct_of_smaller
```

Always compute `overlap_pct_of_smaller` (intersect ÷ min(n_c, n_c_prime)),
NOT `intersect ÷ union` (which understates overlap when the cohorts are
unequal sizes). The "smaller-cohort %" is what readers perceive: if
89 of 91 C members appear in C' too, that's ~98% overlap from C's
side regardless of C's size.

### Step 2 — Apply the decision matrix

| Overlap % of smaller | Decision | Notes |
|---:|---|---|
| **<30%** | Include in aggregate | "Across cohorts, may overlap" disclaimer is honest. C' contributes meaningfully new members. |
| **30-70%** | Flag for product/UX decision | Borderline — depends on whether the aggregate is a hero metric (exclude) or a debug count (include). Surface in PR description. |
| **>70%** | **Exclude from aggregate** | Including C' doubles-counts >70% of one cohort. Add code comment explaining why; file follow-up issue for cross-cohort dedup. |

### Step 3 — Implement the exclusion (if >70% overlap)

```python
# Hero strip: live worklist size = sum of action cohorts (C1–C5 + C6 +
# C7). Cohorts may overlap; we surface this as "across cohorts" not
# "deduped" to avoid the unverifiable unique-count claim flagged by an
# earlier metric audit.
# C_PRIME is DELIBERATELY EXCLUDED from worklist_total: per the design ADR
# it is by-design an alternate framing of the SAME upstream pool as cohort C
# (>90% overlap today), and including it here would visibly inflate the
# hero "across cohorts" count by a near-duplicate. Operators still see
# C_prime's own size on its card. Cross-card dedup tracked as a follow-up
# issue.
worklist_total = sum(
    v for v in [
        cohort_sizes.get("c1"), cohort_sizes.get("c2"),
        cohort_sizes.get("c3"), cohort_sizes.get("c4"),
        cohort_sizes.get("c5"),
        applied.get("n_total"),
        accepted_not_deposited.get("n_total"),
        # NOTE: c_prime deliberately excluded — see comment block above
    ] if v
)
```

Three things the code comment MUST do:

1. State the EXCLUSION explicitly (not just a missing line — silent
   omissions get re-added by the next contributor)
2. Cite the overlap percentage and the source (ADR / analysis doc)
3. Link to the follow-up issue for the proper dedup work

### Step 4 — File the follow-up issue

```markdown
Title: Cross-cohort deduplication (C ∩ C' ~N% overlap)

The aggregate metric M deliberately excludes C' to avoid double-counting
the ~N% C∩C' daily overlap. This is correct for the hero number but
readers may want one of:

- A "deduped pipeline count" that excludes C' from M (current behavior)
- A separate "unique entities across cohorts" tile that does the actual
  set-union via entity_id
- A cohort-overlap badge on each card ("~N entities also on [other card]")
- A backend dedup that removes overlap members from one card's list
  (typically the lower-precision card)

This issue is the placeholder for that product/UX decision.
```

### Step 5 — Flag in PR description

Don't bury the exclusion in the diff. The PR description should call it
out as a judgment call so reviewers can disagree:

```markdown
## Notable judgment call

C' is deliberately excluded from `worklist_total` to avoid double-counting
the ~90% C∩C' overlap. Easy to flip if reviewer disagrees — see the
sister-cohort-high-overlap-inflates-aggregate-sum decision matrix in the
inline comment.
```

## Verification

After shipping the exclusion:

1. **Number sanity check on production**: `worklist_total` should equal
   the pre-change value plus/minus only the C2-C5 / C6 / C7 daily drift.
   If it jumps by ~size(C'), the exclusion didn't take effect.
2. **Narrative-facing test**: any auto-generated narrative (LLM cold-open,
   summary tile, hero-strip prose) should NOT cite the new C' size as a
   contributor to the pipeline total. If the LLM cold-open says "10 cohorts
   totaling N entities" where N includes C', the registry / cold-open data
   wasn't updated alongside.
3. **Anti-drift test**: pin the exclusion in code with a test asserting
   `cohort_sizes.get('c_prime')` is NOT a contributor to `M`'s
   computation. Otherwise a future "let me clean up this code" PR
   re-adds it.

## Worked example: introducing a broad sister cohort

- C = sharp cut (≥1 of top-3 signals) — ~90 entities today
- C' = broad cut (≥4 of 8 behavioral signals) — ~90 entities today
- Intersection: ~85 entities (C' ∩ C ÷ min ≈ 95% overlap)
- Existing aggregate: `worklist_total` (hero strip, "across cohorts")
- Decision: **exclude C'** per Step 2 (>70% overlap → exclude)
- Implementation: code comment block above + `cohort_sizes` dict still
  contains c_prime (the template needs it for the C' card itself) but the
  `worklist_total` sum comprehension omits it
- Follow-up: filed a cross-card deduplication issue as the placeholder for
  the eventual product/UX decision
- PR: calls it out as a judgment call in the description

## Notes

- **The "may overlap, not deduped" disclaimer is a fig leaf at high
  overlap.** Readers scanning a hero number don't reach for the
  small-print disclaimer to mentally subtract overlap. If the disclaimer
  is doing serious work (>30% overlap), the metric design is wrong.
- **Don't auto-dedup as the first move.** Real `set(entity_id)` union
  arithmetic is correct in principle but breaks the "each card preserves
  its calibrated rate" invariant — the deduped count won't equal any
  single card's published lift, which reviewers will notice. The
  exclusion-with-follow-up-issue path defers that design decision to a
  proper UX investigation.
- **Watch for the same trap in:** clickstream event aggregations (segment
  unions), feature-flag exposure counts (control + variant cohorts),
  experiment readouts (treatment ∪ holdout), funnel reports (stage A +
  stage B for stage-pair entities).

## References

- Inline implementation pattern: the `worklist_total` sum comprehension
  + the exclusion comment block (Step 3).
- Design ADR: the "why two cards, not a single union" decision + its
  negative-consequences section quantifying the overlap (>90%).
- Analysis doc: the overlap probe (Sharp ∩ Broad ≈ 900/1100 on backtest).
- Follow-up issue: cross-cohort deduplication (the deferred product/UX
  decision).
