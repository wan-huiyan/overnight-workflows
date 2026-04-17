# Chart divergence check — visual prominence vs statistical significance

Added in **v1.3.0 (S92 P1-3)** after the S91 Finding 1 chart shipped with a
linear y-axis that compressed the 3.17× Intl cliff against the 0 floor. The
stats were solid; the eye saw "already-low line gets a bit lower." User's
gut call caught it; no automated check did.

This reference defines a post-chart-generation check that flags
**visual-vs-statistical divergence** — where the claim-carrying line/bar is
statistically distinct from comparators but visually indistinguishable in the
default encoding — and proposes a redesign.

## Where it runs

**Phase A, after each chart is saved** (Track B + Track C). Before the brief
citing the chart is finalised. If divergence is flagged, the brief links
**both** the original and the redesign so the panel (Phase B) can judge.

## Inputs the check needs

Per chart:
1. The chart's source data (the DataFrame or pivot table the chart was built from).
2. The **claim line identifier** — which series carries the headline (e.g.,
   `distance_band = 'International'` for Finding 1's cliff).
3. The **claim's x-locus** — the x-value where the claim's effect is
   asserted (e.g., `tenure_bin = '31-90'`).
4. The **claim's effect statistic** — typically a ratio + 95% CI, or a raw
   delta + p-value, produced by stats-verification (`phase_b_review_loop.md`).

The chart-generation pipeline already has (1). (2) + (3) + (4) must be
emitted alongside the chart as a companion JSON (`chart_meta.json`) by
whatever script generates it.

## The divergence score

```
visual_prominence(claim_line, x_locus) =
    |y_value(claim_line @ x_locus) - mean(y_value(others @ x_locus))|
  / (chart_y_range)
```

- Numerator: absolute distance at the claim's x-value from the mean of all
  other lines at that same x-value.
- Denominator: the chart's full y-axis range (tick-max − tick-min).
- Result: a normalised "how much of the visual chart area does the claim
  take up as a gap."

**Divergence flag triggers when:**

```
visual_prominence < 0.15  AND  stats_significant == True
```

The 0.15 threshold is calibrated to "eye can barely see the gap" —
empirically the user's call on Finding 1's original chart, which scored
~0.09 (Intl at 0.009 vs domestic mean ~0.035; y-range was 0.0 to ~0.10, so
0.026 / 0.10 = 0.26 on a per-bin basis, but with the marker offset from the
adjacent-bin dominant value of 0.040 the effective gap at x=`31-90` against
the eye-catching 0-30d end was smaller than 0.15).

Tune over time. Log each check's `(prominence, stats_significant,
user_read_it_correctly)` tuple to `chart_divergence_log.jsonl` in v2+.

## Redesign menu

If flagged, auto-generate at least one of the following alternatives and
embed alongside the original:

| Redesign | When to prefer | Why it helps |
|---|---|---|
| **Log y-axis** | All values positive; ratio is the claim; dynamic range > 10× | Compresses the high-value visual floor; amplifies near-zero differences. Caveat: non-specialist audience may misread. |
| **Delta from baseline** | One cohort is the reference; others vary around it | Zeroes out the shared trend, leaving only the cross-cohort signal. |
| **Per-cohort small multiples** | 4+ cohorts; each has its own tenure pattern | Gives each cohort its own y-scale. Removes the "near-floor" visual trap. |
| **Difference plot** | Two cohorts (claim vs reference); effect is the gap | Plots `claim - reference` directly; no compression. |
| **Forest plot of ratios + CIs** | Claim is a ratio statistic; multiple cohorts | Shows effect size + uncertainty in one glance; removes the raw-value floor entirely. |

For the Finding 1 case, **forest plot of bootstrap ratios per band** would
have made the Intl 3.17× (CI [1.57, 5.75]) vs Distant 1.04× (CI [0.82,
1.32]) visually obvious in one row.

## Emission contract

When a chart is flagged:

1. Redesign is written to the same directory as the original, suffixed
   `_redesign.png` (or `_redesign_{variant}.png` if multiple).
2. `chart_meta.json` is appended with:
   ```json
   {
     "chart": "002_intl_ug_tenure_cliff.png",
     "divergence": {
       "score": 0.09,
       "threshold": 0.15,
       "claim_line": "International",
       "x_locus": "31-90",
       "stats": "3.17× ratio, 95% CI [1.57, 5.75]",
       "flagged": true,
       "redesign": "002_intl_ug_tenure_cliff_redesign_forest.png"
     }
   }
   ```
3. The brief's Finding section must cite **both** images and include one
   sentence narrating the divergence:
   > "The linear-scale view makes this look like a small drop near the floor;
   > the bootstrap-ratio view shows the 3.17× effect is materially sharper
   > than any domestic band's cliff."

## Panel integration

The `qa-expert` persona checks for `chart_meta.json`'s presence and — if
divergence was flagged — that the brief cites **both** images. Missing the
redesign is a P1 flag.

The `client-trust-evaluator` persona is asked specifically: "Does the chart
make the claim obvious to a non-technical reader in under 10 seconds?" If
no and divergence was not flagged, escalate to P0 — the threshold is
mis-calibrated for this chart type.

## Out of scope for v1.3.0

- Automatic redesign *generation*. v1.3.0 defines the check + the menu; the
  actual redesign requires human or LLM-assisted choice of variant because
  the wrong redesign (e.g., log-scale for a non-specialist audience) can
  hurt trust. v2+ may auto-pick based on chart type + audience tag.
- **Applies to line/bar charts with ≥ 3 comparison series.** Heatmaps,
  scatter plots, and single-series charts are out of scope — they have
  different visual-prominence geometries.
