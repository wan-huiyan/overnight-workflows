---
name: target-leakage-guard-misses-transforms-of-the-target
description: |
  Use when reviewing/building a regression, forecasting, BSTS, or causal-inference
  pipeline that assembles a covariate / feature / regressor list and guards against
  including the outcome — i.e. you see a filter like `[c for c in x_cols if c != y_col]`,
  `cols.drop(target)`, `features.remove(label)`, "outcome must not be a covariate",
  or "exclude the target column". The trap: that guard excludes ONLY the exact
  target column NAME, so a column that is a deterministic TRANSFORM of the target —
  `log_<target>` (= log1p(y)), `<target>_scaled`, `<target>_lag0`, a standardized or
  Box-Cox version, a ratio whose numerator is the target — slips through and becomes
  a regressor. A feature that IS the target (on another scale) gets a fitted
  weight ≈ 1.0, "explains" the outcome trivially, and CONTAMINATES the whole fit
  (near-perfect counterfactual → meaningless effect estimate / CI / p-value), not
  just a display. Trigger also when a per-feature weight/importance panel shows one
  covariate ≈ 1.0 dominating everything else by ~100x, or when a "log/scaled
  transform of the outcome" enrichment is auto-added to the feature set, or when an
  advisor/user says "it doesn't make sense that <X> is the top driver of <X> itself".
  Symptom: one regressor's weight ≈ 1.0, all others ~0.01.
author: Claude Code
version: 1.0.0
date: 2026-06-05
disable-model-invocation: true
---

# A target-leakage guard that excludes `c != target` still leaks TRANSFORMS of the target

## Problem

A modeling pipeline drops the outcome from the regressor set with a guard like:

```python
x_cols = [c for c in x_cols if c != y_col]      # "outcome must not be a covariate"
```

This *looks* complete — it excludes the target. But it only excludes the **exact
column name**. A column that is a **deterministic transform of the target** has a
different name and sails right through:

- `log_<target>` (= `log1p(y)`) — the classic. Common when a "log-transform"
  feature/enrichment is meant to drive a *log-target* model flag but instead gets
  auto-added to the covariate list.
- `<target>_scaled` / standardized / Box-Cox / winsorized target.
- `<target>_lag0`, a same-period ratio whose numerator is the target, a rolling
  mean that includes the current period.

Because such a column is a monotone (often near-linear) function of `y`, the model
fits it a weight ≈ 1.0 and it **trivially explains the outcome**. This is **target
leakage**, and it does not stay contained to a feature-importance display: in a
forecasting / BSTS / causal pipeline it makes the **counterfactual near-perfect**,
so the **effect estimate, confidence interval, and p-value for the entire run are
contaminated**. The headline number looks fine; only a per-covariate weight view
exposes it.

## Trigger conditions

- You see `!= y_col` / `drop(target)` / `remove(label)` / "exclude the outcome"
  as the ONLY leakage guard when finalizing covariates/features.
- A transform-of-the-target column (`log_<target>`, scaled/lagged/ratio) can enter
  the feature set — especially via an "auto-add all generated/enrichment columns"
  step that doesn't distinguish target-transforms from real features.
- A per-feature weight / SHAP / importance view shows ONE feature ≈ 1.0 dominating
  the rest by ~100×.
- A user/advisor says "it makes no sense that <feature> drives <the same quantity>".
- The same `!= target` guard is **copy-pasted across many sites** (assembly,
  validation, decomposition, the cloud worker, the SCA/sweep path) — none upgraded.

## Solution

1. **Exclude target TRANSFORMS, not just the raw target name.** Minimum: also drop
   `f"log_{y_col}"`. Better: derive the full set of target-transform columns from
   wherever transforms are defined (e.g. the enrichment catalog — every
   log/scale transform whose `source_col == y_col`), so future transforms are
   covered automatically.
2. **Don't auto-add a target-transform column to the feature set at all.** A
   "log-transform of the outcome" feature should feed a *log-target* model flag
   (fit on `log1p(y)`), not the regressor list. Fix it at the source (the auto-add
   step), so it never enters the saved config.
3. **Defense-in-depth at the fit**, not only at assembly — already-saved configs
   carry the leaked column; filter target-transforms again right before the fit so
   re-runs of an old contaminated config are safe.
4. **Fix in one place, then grep every sibling.** This guard is almost always
   duplicated; upgrade ALL of them (a shared `is_target_transform(col, y_col)`
   helper / exclusion set beats N copies of `!= y_col`).
5. **Flag the contaminated runs.** Any run whose config already includes the
   leaked column has a corrupted effect estimate — it must be RE-RUN without it;
   patching the guard does not retroactively clean stored results.

## Verification

- Re-fit on a config that previously leaked: the dominant ≈1.0 weight is gone and
  the genuine covariates carry the signal; the effect estimate / CI / p-value
  change (they were contaminated before).
- Grep the codebase: every covariate-finalization site excludes target transforms,
  not just `!= y_col`.
- A regression test that puts `log_<y_col>` in `x_cols` asserts it is dropped
  before the fit at each layer.

## Example

A causal-impact webapp's covariate assembly ran
`x_cols = [c for c in x_cols if c != y_col]` (target = `revenue`). A "Log revenue
(log1p)" enrichment — described as *"use as alternative target"* — had its
generated column `log_revenue` auto-added to `x_cols`. `log_revenue` ≠ `revenue`,
so the guard missed it. The fitted BSTS weight for `log_revenue` was **+1.015**
(band `[+0.987, +1.042]`); every real covariate sat at ~0.01. The headline causal
effect / CI / p-value for that run were all contaminated. The leak was invisible
until a per-covariate weights panel surfaced it. The same `!= y_col` guard existed
at 7 sites; none excluded `log_<target>`.

## Notes

- The fix is cheap; finding the bug is not — the guard reads as correct and the
  headline numbers look plausible. A per-feature weight view (or "why is X the top
  driver of X?" intuition) is the reliable detector.
- Distinct from generic train/test leakage: here the leaked feature is in the SAME
  rows as the target, a transform of it — the model is partly regressing y on y.
- See also: `null-bucket-hides-progressors-in-snapshot-training`,
  `sentinel-real-disjunction-clamp` (other quiet feature/label-integrity traps).
