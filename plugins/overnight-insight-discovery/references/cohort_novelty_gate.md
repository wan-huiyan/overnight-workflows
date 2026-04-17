# Cohort-conditional novelty gate

Deep dive on the single biggest defence against "LLM narrates known facts as
surprising."

## The core insight

SHAP top features are **cohort-dependent**. A feature that's rank-1 in Cohort A
can be rank-50 globally. If you use a single global top-20 as your known-knowns
table, you either:

- Over-block — treating "missing_credentials drives In-Process cohort" as trivial
  when it IS the top signal for that specific cohort (novelty-fail incorrectly)
- Under-block — letting a claim slide because it's not in the global top-20
  while it's already the rank-1 driver inside its own cohort

Both produce bad briefs. The fix is cohort-conditional lookup.

## Table schema

`scoping/known_knowns_by_cohort.jsonl`, one row per known-knowledge-atom:

```json
{
  "cohort_dim": "acceptance_variant",
  "cohort_value": "pending_docs",
  "feature": "days_in_accepted_not_deposited",
  "family": "accepted_nd_tenure",
  "rank_within_cohort": 2,
  "mean_abs_shap": 0.142,
  "direction": "-",
  "source": "v6_1_training_shap"
}
```

## Pre-computed cohort dimensions

Target ~6–8 dimensions × their levels = ~30 cohort cells. Pre-compute top-20
features per cell during Phase 0.

Choose dimensions that:
1. Matter to the domain (e.g. funnel stage for admissions, patient segment for clinical)
2. Have enough cells to discriminate but few enough to stay tractable (3–10 levels each)
3. Are likely to shift SHAP rankings (i.e. different cohorts genuinely have different drivers)

## On-demand decomposition for compound cohorts

Tracks will inevitably find claims about compound cohorts — `Accepted ∩
pending_docs ∩ international`. These can't be pre-computed (too many
combinations). Rule:

- Any claim about a compound cohort must include a within-cohort SHAP
  decomposition at `track_X/findings/NNN_shap_decomp.json`.
- Novelty = `min(novelty_vs_component_A, novelty_vs_component_B, ...)`.
- Only features emerging in the interaction but absent from every component's
  top-20 count as strong novelty (genuine interaction signal).

This keeps the gate tight while still allowing genuine interaction findings.

## Semantic similarity bands

Applied by the `scientific-critical-thinker` review persona:

| Similarity | Verdict tag | Handling |
|---|---|---|
| ≥ 0.70 | `[novelty-fail]` | Reject outright; cannot appear in final brief |
| 0.55 – 0.70 | `[novelty-weak]` | Only as supporting context; cannot be headline |
| 0.30 – 0.55 | `[novelty-moderate]` | Secondary finding w/ "partial rediscovery" caveat |
| < 0.30 | `[novelty-strong]` | Headline-eligible, clean ah-ha |

## Similarity function

```python
def novelty_similarity(claim: dict, known_rows: list[dict]) -> float:
    """
    Tuple-match on (feature_family, cohort_dim, cohort_value, direction).
    Returns 1.0 if the claim's tuple is in known-knowns top-20, 0.0 otherwise.
    Family-based to catch near-duplicates (raw feature vs its band variant).
    """
    for known in known_rows:
        if (known["cohort_dim"] == claim["cohort_dim"]
            and known["cohort_value"] == claim["cohort_value"]
            and known["direction"] == claim["direction"]
            and known["family"] == claim.get("family", claim["feature"])
            and known["rank_within_cohort"] <= 20):
            return 1.0
    return 0.0
```

More sophisticated implementations might use a cosine similarity in embedding
space, or a graded score based on rank within cohort. The binary version above
is enough for v1.

## Feature families

`scoping/feature_families.jsonl` — ~20–40 families grouping a raw feature with
its variants. Examples:

```json
{"family": "accepted_nd_tenure", "members": ["days_in_accepted_not_deposited", "accepted_nd_tenure", "accepted_nd_tenure_band_0_30d", ...]}
{"family": "acceptance_variant", "members": ["acceptance_variant", "acceptance_variant_clean_accept", "acceptance_variant_pending_docs", ...]}
{"family": "fafsa", "members": ["has_fafsa_submitted", "fafsa_filed", "will_apply_for_financial_aid", "days_since_isir_receipt", ...]}
```

Why families matter: `days_in_accepted_not_deposited` and
`accepted_nd_tenure_band_0_30d` are encoding the same signal. If the first is
in known-knowns top-20 and the second shows up in a claim, they're the same
finding — mark it as novelty-fail, not novelty-strong.

## Hard-coded traps

Add rows with `cohort_dim: "_trap"` for project-wide known pitfalls:

```json
{"cohort_dim": "_trap", "cohort_value": "percentile_bucket_identity", "feature": "bucket_count",
 "trap_type": "percentile_bucket_identity",
 "note": "Bucket counts are 1/5/30/70% by construction — any time-series trend is a population identity, not a signal."}
```

Traps stand apart from cohort-specific SHAP knowns because they're definitional
rather than learned. Review persona checks both the regular and `_trap` rows.

## Source priority when building the table

1. Current-model training SHAP (primary for new features)
2. Prior-model serving SHAP (reinforcement for shared features, captures
   current-distribution behaviour)
3. Current-model joblib's training-time feature importance (global-only fallback)
4. Hard-coded traps (definitional)

If sources conflict, higher-priority wins for that (cohort_dim, cohort_value,
feature) key. Document the merge in `build_known_knowns.py`'s output so future
auditors can trace.

## What NOT to include

- Full SHAP value distributions — the top-20 is enough; more creates noise
- Feature importance from cross-cohort aggregates — defeats the cohort-conditional point
- Per-visitor-level SHAP — not a known-knowns question, that's a per-student explainer
- Time-series SHAP trends — capture trend-availability in scoping instead, as a review-panel caveat

## Why this is the single biggest lever

Every pattern the skill captures is helpful, but the novelty gate is load-bearing.
Without it, Track B (LLM-autonomous) will happily narrate "students with filed
FAFSA enroll more" as a finding. Track C's scans will surface the same. The
review panel, lacking a concrete gate, will defer to "well, it's statistically
significant" and let it through. The consolidation will include it. The client
reads it and says "we already knew that."

Put the gate in one place — `known_knowns_by_cohort.jsonl` — and enforce it at
the reviewer persona, not the track prompt. Track prompts that try to enforce
novelty internally get worked around; reviewer personas with an authoritative
lookup don't.
