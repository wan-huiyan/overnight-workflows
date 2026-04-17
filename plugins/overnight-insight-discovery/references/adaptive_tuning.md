# Adaptive parameter-tuning loop

Track C's scans (funnel_leak, conditional_lift, shap_interaction,
cohort_divergence) have threshold parameters (`lift_min`, `cohort_min`,
`stat_support`, `p_max`, `at_risk_min`, `novelty_similarity_max`, etc.) that
are first-guess defaults, not laws. First run's yield may be 0 (all filtered
out) or 500 (nothing filtered). Adaptive tuning wraps Stages 1–3 so the loop
converges on a productive operating point without manual intervention.

## The loop

```
iter = 1
while iter ≤ MAX_TUNING_PASSES (default 3):
    run scan → rank → prune with current stage_config.yaml

    yield_class = classify_yield(pruned)
    # possible classes:
    #   goldilocks — 8–15 candidates, ≥3 B + ≥3 C flavor — BREAK
    #   starved   — < 8 candidates total
    #   flooded   — > 20 candidates after de-dup
    #   trivial   — all survivors have novelty_similarity > 0.7
    #   skewed    — not ≥3 of each flavor
    #   one-cluster — all surviving Jaccard > 0.6

    if yield_class == "goldilocks":
        break

    apply_relaxation(stage_config.yaml, yield_class)
    commit stage_config.yaml + yield_iter_<N>.json + rationale
    iter += 1

# If reached MAX_TUNING_PASSES without goldilocks, proceed with whatever
# pruned.parquet has. Flag in brief: "tuning converged at N iterations
# without reaching ideal yield; see yield_iter_*.json for trajectory."
```

## Yield-class → relaxation rules

| Yield class | What it means | Relaxation applied |
|---|---|---|
| **starved** | Filters too strict | `lift_min` 3×→2×; `cohort_min` 50→25; `p_max` 0.01→0.05; `at_risk_min` 100→50 |
| **flooded** | Filters too loose | `lift_min` 3×→4×; `cohort_min` 50→100; `stat_support_min` 0.8→0.9 |
| **trivial** | Filters pass rediscoveries | `novelty_similarity_max` 0.7→0.55; set `require_off_axis_cohort=true` (bias toward features outside known-knowns top-20 for the target cohort) |
| **skewed** | One flavor dominates | Force-promote 2 of the underrepresented flavor from rank 41–80 of `ranked.parquet` into `pruned.parquet` |
| **one-cluster** | Correlation-collapse over-aggressive | `jaccard_max` 0.6→0.4 (keep more variants); OR set `cohort_dim_diversity=true` (require surviving candidates span ≥3 distinct cohort_dim values) |

## Commit discipline

Each tuning iteration is fully committed, even if it's just a config change:

```
git commit -m "track-c: tune iter 2 — starved → loosen (lift 3→2, cohort 50→25)"
```

Attach `yield_iter_<N>.json` to the commit:

```json
{
  "iter": 2,
  "yield_class": "starved",
  "n_candidates_pre_prune": 3,
  "n_candidates_post_prune": 3,
  "flavor_counts": {"B": 2, "C": 1},
  "config_before": {"lift_min": 3.0, "cohort_min": 50, ...},
  "config_after": {"lift_min": 2.0, "cohort_min": 25, ...},
  "rationale": "all 4 scans produced < 10 total rows; relaxing primary filters"
}
```

Morning review can reconstruct WHY the loop ended where it did by reading the
yield_iter files.

## Post-tuning escape hatch during review

The adaptive loop in Stage 1–3 runs ONCE per track (before Stage 4 narrate).
Later, during the per-track review loop, the `plan-review-integrator` can
trigger ONE MORE Stage-1 retune if a review finding requires fresh candidates
(e.g., "all findings are SHAP top-20 rediscoveries"). See
`phase_b_review_loop.md` § Escape hatches.

Cap at 1 escape-hatch retune per review loop. More is pathological.

## What NOT to tune adaptively

- **Cohort dimensions** (which cohort_dim values the scans enumerate) — this is
  scoping-level, decided in Phase 0 from the known-knowns table
- **Scan types themselves** — adding/removing a scan is v2 work, not tuning
- **Narrative style / voice** — Stage 4's Opus prompt isn't tuned; that's
  writing-style, not a threshold
- **Review panel personas** — seeded at scoping, fixed across iterations
- **Novelty similarity bands** (0.30 / 0.55 / 0.70) — these are review-panel
  conventions, not per-run tunable

The principle: adaptively tune FILTERS, not DESIGN. Design decisions live in
scoping; filter thresholds live in `stage_config.yaml`.

## Initial config — reasonable defaults

Use these as v1 defaults; the tuning loop will adjust:

```yaml
# track_c/state/stage_config.yaml (initial)
lift_min: 3.0
cohort_min: 50
at_risk_min: 100
p_max: 0.01
stat_support_min: 0.8
novelty_similarity_max: 0.7  # above this = filtered as rediscovery
require_off_axis_cohort: false
jaccard_max: 0.6             # for cluster-collapse
cohort_dim_diversity: false
require_both_flavors: true   # require at least some B and some C
```

## Why yield classes beat one-dimensional "too many / too few"

Yield classes capture WHICH way the filter is failing:

- starved + trivial (both) → filters are strict AND the novelty gate is over-blocking. Relax both.
- flooded + skewed → lots of candidates but all one flavor. Tighten + flavor-balance.
- one-cluster only → the Jaccard collapse is being too aggressive on a legitimate diverse set.

A simple "increase/decrease lift_min" heuristic can't distinguish these. Yield
classes encode the diagnosis.

## Common failure modes

- **Oscillation**: iteration 1 says flooded → tighten; iteration 2 says
  starved → loosen; iteration 3 says flooded → tighten again. This is usually a
  sign the actual yield is borderline-goldilocks and the thresholds are right;
  accept whatever iteration 3 produces and let the review loop decide.
- **Skipping commits**: don't batch multiple tuning changes into one commit.
  Morning review can't reconstruct WHY without the per-iter trail.
- **Manually overriding the config mid-loop**: defeats the point. If you find
  yourself editing `stage_config.yaml` by hand, add a yield class for whatever
  case you're trying to handle and codify the rule.
