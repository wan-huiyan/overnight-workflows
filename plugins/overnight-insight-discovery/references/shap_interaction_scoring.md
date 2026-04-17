# SHAP Interaction Scoring — Normalize Against Marginal Product

Added in v1.1.0 after the v1.0.1 run shipped a biased SHAP-interaction ranking that
confounded genuine interaction with marginal-product artifacts.

## The failure mode

Track C's `scan_shap_interaction` ranked feature pairs by **raw mean product** of SHAP
values across visitors:

```python
interaction_score = mean_over_visitors(shap_a * shap_b)
```

Top-ranked hit in the v1.0.1 run: `application_stage_fall × app_major`
(mean_product = 1.75, sign_consistency = 0.83).

Track B's planning-board review caught the bug: the top two individual features by
`mean_abs(shap)` were `application_stage_fall` (mean_abs ≈ 1.85) and `app_major`
(mean_abs ≈ 1.16). Multiplying the marginals × sign_consistency:
`1.85 × 1.16 × 0.83 ≈ 1.78` — nearly identical to the reported "interaction."

**The ranking wasn't finding interactions. It was finding products of large marginals.**

## The fix

Normalize against the product of individual magnitudes to isolate interaction beyond
what you'd expect from the marginals alone:

```python
rho_shap = mean(shap_a * shap_b) / (mean(|shap_a|) * mean(|shap_b|))
# Range: roughly [-1, 1]; ±1 means perfectly coherent/anti-coherent pair
# 0 means the pair's co-movement is exactly what independent features would do
```

Keep pairs only if `|rho_shap| ≥ 0.15` (tunable) AND `sign_consistency ≥ 0.6`.

## What good scoring looks like

| Pair | mean_product | mean_abs_a × mean_abs_b | rho | Interpretation |
|---|---|---|---|---|
| application_stage × app_major | 1.75 | 2.15 | 0.81 | Large marginals, modest genuine interaction |
| distance_band × days_since_deposit | 0.04 | 0.07 | 0.57 | Both small individually but genuinely synergistic |
| login × campus_visit | -0.05 | 0.08 | -0.62 | Suppresses each other — genuinely anti-correlated |

Raw `mean_product` ranks the first above the second. `rho_shap` ranks the second and
third correctly as more informative.

## When to use raw mean_product instead

If the downstream consumer specifically cares about "which pair of features contributes
the most total prediction variance together" (business-facing ROI question), raw
`mean_product` is the right answer — it's an unambiguous magnitude.

But for **discovery of non-obvious patterns** (the whole point of this skill), always
use `rho_shap`. Marginal products are, by definition, already known to the model.

## Implementation note

Track C's `scan_shap_interaction.py` should compute BOTH and emit both columns:

```python
df["mean_product"] = (df["shap_a"] * df["shap_b"]).mean()  # keep for traceability
df["mean_abs_a"]   = df["shap_a"].abs().mean()
df["mean_abs_b"]   = df["shap_b"].abs().mean()
df["rho_shap"]     = df["mean_product"] / (df["mean_abs_a"] * df["mean_abs_b"])
df["sign_consistency"] = ((df["shap_a"].apply(np.sign) == df["shap_b"].apply(np.sign))).mean()
```

Rank by `|rho_shap|` in the narration phase. Keep `mean_product` as a reported
statistic but not as the surprise-score input.

## Related trap

The design doc's §5.2 Stage 2 ranking formula was written assuming interaction signal.
If you use raw mean_product as `lift`, the `log(lift)` term in `surprise_score` inherits
the marginal-product bias. Use `(1 + |rho_shap|)` as the `lift` proxy instead.
