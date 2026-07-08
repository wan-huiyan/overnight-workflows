---
name: observational-version-comparison-confounded-by-time
description: |
  Use when a user or task asks to COMPARE performance, cost, speed, or quality across
  model versions (e.g. an older vs newer LLM release), app/prompt/config versions, or any setting that
  was switched once — using OBSERVATIONAL usage logs, analytics, or a dashboard rather than a
  controlled experiment. The version is almost always COLLINEAR WITH TIME (you switched on a
  date), so any cross-version delta is confounded by everything else that changed then (task
  mix, effort, cohort, season). Trigger this whenever: (1) building or reviewing a "compare
  versions / configs" chart or a model/effort/version breakdown, (2) about to label a bar "v2 is
  faster/cheaper/better" from logs, (3) a stakeholder asks "is the new model performing better?"
  and the only data is historical usage. Prescription: reframe comparison → descriptive breakdown
  over time, surface the confound ON the view, strip superlative copy, and point to a controlled
  eval as the only honest performance verdict.
author: Claude Code
version: 1.0.0
date: 2026-06-01
---

# Observational version/config comparison is confounded by time

## Problem

Someone asks to "compare performance of v1 vs v2" (model version, prompt version, app release,
a config flag, a one-way migration) and the only data is **observational usage logs**. Because the
switch happened once, on a date, the two versions occupy **near-disjoint time windows**. Any metric
you compute per version is therefore a proxy for *when* and *what* the work was — not the version's
effect. Shipping a bar chart titled "v2: fewer tokens / faster / cheaper" reads as a causal claim the
data cannot support, and a caveat in a footnote does not undo the impression the bar creates.

## Context / Trigger Conditions

- A "compare model/app/prompt/config versions" feature, dashboard panel, or report request.
- Source is historical usage/telemetry, NOT a randomized or matched experiment.
- The setting being compared changed monotonically over time (a switch, an upgrade, a migration).
- You're about to write group labels with superlatives ("better", "faster", "more efficient").
- A stakeholder asks "is the new version performing better?" expecting a usage dashboard to answer.

## Solution

1. **Run the discriminating check first:** group sessions/events by the version and print each
   group's **date-span**, **count**, and **task/cohort mix** (top projects, top task types). If the
   date ranges are near-disjoint, the version is a time proxy — stop treating it as a comparison.
2. **Reframe "comparison" → "breakdown over time."** Same views, honest title: "usage/cost/behaviour
   by version, over time," not "v1 vs v2 performance."
3. **Put the confound ON the view, not in a footnote.** Make the confound banner the first element,
   and render each group's date-span + mix inline beside its bars so the reader sees the overlap
   problem while reading the numbers.
4. **Strip superlative/causal copy.** Report "used N tokens", "X% cache" — never "better/faster".
5. **Name the honest alternative.** The only way to actually compare version *performance* is a
   **controlled eval**: run the *same* inputs through both versions and compare outputs. If the user
   wants that, route them to an eval harness — a usage tracker structurally cannot produce it.
6. **Same logic for effort/config dimensions** that are self-selected (the user chose high effort for
   hard tasks) — that's selection bias, the same family of confound; disclose it the same way.

## Verification

- Cross-version date-spans printed and shown to overlap/disjoint — the reader can see it.
- No chart label asserts causation; the title says "breakdown over time".
- A one-line pointer to the controlled-eval path exists where the user would look for "which is better".

## Example

A usage-tracking tool: a user asked to "compare v1 vs v2 model performance" from the model field
in usage logs. Grouping by version showed the older release spanning roughly the first five weeks
(dozens of projects, several hundred sessions) vs the newer release spanning only the last few days
(a handful of projects, a few dozen sessions) — near-disjoint windows, and *all* high-effort sessions
fell in the newer-release window. "v1 vs v2" was really "before vs after the switch date." Resolution:
the feature was re-specified from "performance comparison" to "usage/cost breakdown by model & effort
over time," with the date-span/mix shown per group and a pointer to a controlled eval for true performance.

## Notes

- This is the same root as cohort/temporal confounds, but it bites at the **product-framing** layer
  (what the chart claims) — which is why a methodology footnote is insufficient; the fix is the title,
  the on-view banner, and the absence of superlatives.
- Per-unit pricing has a sibling trap: if both versions are priced at the same rate, a "$ comparison"
  only restates the token delta — disclose or compare on tokens/time instead.
- See also: `claude-code-ab-harness` (the controlled-eval path), `setting-only-logged-on-change-infer-from-default`
  (its companion: how to *label* the version/effort dimension honestly),
  `feature-importance-trend-panel-conflates-cohort-shift-with-model-drift`,
  `predicted-score-cohort-comparison-flips-on-realized-backtest`,
  `frozen-cohort-rebucket-newer-model-contemporaneity-leak` (the specific
  frozen-cohort-matrix re-bucket instance, with the ~100%-forward-rate tell).

## References

- Project lesson: slicing by a time-monotonic setting conflates it with time → breakdown not comparison.
