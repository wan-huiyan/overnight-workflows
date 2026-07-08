---
name: metric-window-spec-needs-event-time-distribution-probe
description: |
  When specifying a rolling-window or lookback-window parameter for a
  monitored metric — especially conversion rates, attribution windows,
  retention cohorts, or any rate defined as (numerator in window X) /
  (denominator in window Y shifted by lag Z) — the chosen window length
  is only defensible if it is calibrated against the actual measured
  distribution of the underlying event-time lag. A "14-day lookback"
  for a process whose median completion time is 35 days captures only
  ~30% of events; a "14-day lookback" for a process whose median is
  13 days captures ~50% and is defensible. Use this skill when:
  (1) writing a metric definition that includes a numeric window
  parameter (7d, 14d, 28d, 30d, etc.) without explicit justification,
  (2) reviewing a dashboard or signal catalog where multiple
  conversion-rate signals share the same window length by default
  (a sign that no one measured the actual lag distribution),
  (3) a stakeholder asks "why 14 days?" and the answer is "that's
  what the spec said", (4) anomaly detection on a windowed rate is
  noisy and you suspect Poisson-on-small-N because the window is too
  narrow. The fix: probe the event-time lag distribution
  (deciles / quartiles) for each conversion step, choose a window
  that captures at least the median (p50) or ideally the p70, and
  document in the metric's data-coverage cell what % of the underlying
  events the chosen window captures.
author: Claude Code
version: 1.0.0
date: 2026-05-27
---

# Metric Window Spec Needs Event-Time Distribution Probe

## Problem

When designing a monitored conversion rate or any windowed metric of
the form `(events in window) / (denominator in matched window)`, the
window length is a load-bearing parameter that determines what
fraction of the underlying event-time distribution the metric
observes. Pick the window too narrow and the metric only "sees" a
small share of events (high variance, alert noise, missed signal in
slow-converting cohorts). Pick the window too wide and the metric
lags real-world shifts (stale signal, slow detection).

The recurring mistake is to pick a round number ("14 days", "30
days", "one quarter") **by convention rather than by measurement** —
and then to ship the metric without ever probing the actual lag
distribution. The metric runs in production, the rate looks plausible,
and the gap between "what the metric thinks it's measuring" and "what
it's actually measuring" is invisible until someone measures the
underlying lag.

For example, consider three conversion-rate signals defined with default
14-day windows:

- **Lead → submit rate (14d)** — actual lead→submit lag: not
  measured. Defensibility: unknown.
- **Submit → decision rate (14d)** — suppose the actual submit→decision
  lag is long and right-skewed (say a median around a month, with a p90
  out past several months). The 14-day window then captures only a
  minority of decisions. A drop in this rate could mean (a) the decision
  rate fell or (b) decisions are slower this cycle and the 14-day tail is
  now thinner — and the metric can't distinguish these.
- **Decision → deposit rate (14d)** — suppose the actual decision→deposit
  lag is short (say a median around two weeks). The 14-day window then
  captures roughly half of deposits — defensible as a near-term
  sensitivity window.

Same nominal "14d" parameter; wildly different fitness-for-purpose.

## Context / Trigger Conditions

Use this skill when ANY of these apply:

1. You are writing a metric definition that includes a windowed
   computation like `(events in last Nd) / (denominator in matched
   Nd window)` or `(events in last Nd shifted by Md)`.
2. You are reviewing a dashboard or signal catalog where several
   conversion-rate metrics share the same window length without
   per-metric justification.
3. A "data coverage" or "availability" cell for a windowed metric
   says only the window length (e.g., "14d lookback") without
   stating what % of underlying events fall within that window.
4. A stakeholder asks "why this window length?" and the answer is
   silence, or "that's what the spec said", or "it seemed
   reasonable".
5. An anomaly detector on a windowed rate is producing noisy alerts
   that don't correlate with downstream movement — the window may
   be too narrow for the underlying process and the metric is
   capturing mostly Poisson noise.
6. You are migrating a metric from one product / tenant / customer
   to another and copy-pasting the window length without re-probing
   (event-time distributions are domain-specific; a subscription-renewal
   lag ≠ ecommerce checkout lag ≠ insurance-claims lag).

Specifically watch for:

- Multiple conversion rates with identical window lengths (a
  copy-paste-from-default smell).
- Window lengths that match round numbers (7, 14, 28, 30, 90) rather
  than measured percentiles.
- Metric specs that don't cite a measurement of the underlying lag.

## Solution

### Step 1 — Probe the event-time lag distribution

For every conversion step in the metric, measure the distribution
of times between the "start" and "end" events:

```sql
SELECT APPROX_QUANTILES(
  DATE_DIFF(DATE(<end_event>), DATE(<start_event>), DAY),
  10
) AS deciles
FROM <event_table>
WHERE <end_event> IS NOT NULL
  AND <start_event> IS NOT NULL
  AND DATE(<end_event>) >= DATE(<start_event>)
```

Output is the 11-element decile vector `[p0, p10, p20, ..., p100]`.
Read off:

- **p50** (median) — half of events complete within this lag.
- **p70** — 70% of events complete within this lag.
- **p90** — 90% of events complete within this lag (tail).

### Step 2 — Choose the window to capture the relevant percentile

Pick a window based on what fraction of events you want the metric
to observe:

| Window choice | Captures | When defensible |
|---|---|---|
| p50 (median) | 50% | Near-term sensitivity; metric responds to recent shifts but loses tail |
| p70 | 70% | Balanced — captures most while staying current |
| p90 | 90% | Stability over recency; metric is slow to react but doesn't miss tail |
| < p50 | <50% | **Rarely defensible** — most events are missing from the rate |

In practice: aim for **p50 minimum, p70 preferred**. A window that
captures <p50 of events should be redesigned, not shipped.

### Step 3 — Document what the window captures in the metric's data-coverage cell

The metric's coverage / availability documentation should state both
the window length AND what % of events it captures. Example for a
sheet's "Data Coverage" column:

- ❌ "14d lookback, time-shifted" (insufficient — doesn't say
  what 14d captures).
- ✅ "14d window time-shifted ~5 weeks. Captures ~30% of decisions
  (measured median submit→decision = ~35d, p70 = ~65d). Window
  extension to 28-42d under consideration for a future version."

The honest version makes the gap visible and explicit, enabling
both the consumer of the metric (PM, analyst) and the future
designer to make an informed call about whether to extend the
window.

### Step 4 — When a measurement-vs-window gap is found, document the call

When a probe reveals a gap (window captures < p50), the team has
three options. Pick one explicitly, do not ship ambiguous:

1. **Extend the window in production** — change the live metric
   definition. Requires a re-bake of historical values.
2. **Keep the window but document the limitation** — explicitly
   acknowledge the metric is a "fresh-cycle" sensitivity signal,
   not a complete-cycle accuracy signal. Add a complementary
   longer-window signal for completeness.
3. **Deprecate the metric** — if it can't be made fit-for-purpose
   at any window, replace it with something different.

The wrong move: leave the window as-is and don't document the gap.
That's how the metric ships into a stakeholder review with a hidden
defect.

### Step 5 — Don't extrapolate the measurement across domains

An event-time distribution measured for one cohort, product, or
customer does NOT generalize. Re-probe for each:

- New tenant / new customer: re-probe.
- New cycle (season, fiscal quarter): re-probe.
- New segment definition: re-probe.
- New event source: re-probe.

The window-spec audit is a recurring discipline, not a one-time
configuration.

## Verification

After applying this skill, verify:

1. Every windowed metric in the catalog has a coverage cell that
   states the % of events the window captures.
2. No conversion-rate metric has a window that captures < p50 of
   its underlying event distribution (unless explicitly documented
   as a "fresh-cycle" / "leading-edge" signal with a complementary
   stable signal).
3. The team has explicitly chosen one of (extend / accept-with-doc /
   deprecate) for any metric where the gap was found.
4. The window choice is cited against the measured deciles, not
   against a round number.

## Example

**Setting:** Anomaly-forecast catalog with three conversion rates,
all spec'd as 14-day lookbacks. Fact-check panel asks: why 14d?

**Probe (one query per metric).** All numbers below are illustrative
of the shape you tend to see — read your own deciles off the query:

```sql
-- Submit → decision
SELECT APPROX_QUANTILES(
  DATE_DIFF(DATE(decision_at), DATE(submitted_at), DAY), 10
) FROM application_events
WHERE decision_at IS NOT NULL AND submitted_at IS NOT NULL
  AND DATE(decision_at) >= DATE(submitted_at)
```
Deciles (illustrative): `[0, 2, 5, 12, 22, 35, 50, 65, 120, 200, 2300]`
Read-off: p50 = ~35d, p70 = ~65d, p90 = ~200d. **14d captures ~p30.**

```sql
-- Decision → deposit
SELECT APPROX_QUANTILES(
  DATE_DIFF(DATE(deposit_at), DATE(decision_at), DAY), 10
) FROM application_events
WHERE deposit_at IS NOT NULL AND decision_at IS NOT NULL
  AND DATE(deposit_at) >= DATE(decision_at)
```
Deciles (illustrative): `[0, 0, 2, 5, 8, 13, 19, 29, 55, 100, 1800]`
Read-off: p50 = ~13d, p70 = ~29d. **14d captures ~p52.**

**Conclusion:**
- 14d for submit→decision is **too narrow** (captures ~p30) — extend
  to 28-42d or accept-with-doc.
- 14d for decision→deposit is **defensible** (captures ~p52) — keep
  as-is, document the capture rate.

**Documentation patches:**
- Submit→decision signal: "14d window time-shifted ~5 weeks. Caveat:
  14d captures only ~30% of decisions (measured median
  submit→decision = ~35d, p70 = ~65d). Window extension to 28-42d
  under consideration for a future version."
- Decision→deposit signal: "14d lookback. Captures ~50% of
  decision→deposit conversions (measured median = ~13d, p70 = ~29d) —
  defensible as a near-term sensitivity window."

Same nominal 14d; two completely different documented justifications.

## Notes

- **The "round-number window" smell.** When a metric specification
  has a window of 7 / 14 / 30 / 90 days and no measured rationale,
  treat it as a hypothesis to be tested, not a constant. The cost
  of one `APPROX_QUANTILES` query is trivial; the cost of shipping a
  conversion rate that captures <p50 of events is a stream of false
  alerts and silently-missed cohort shifts.
- **Same metric across two cohorts may need two windows.** If the
  process measured (e.g., a decision step) has different
  characteristic times for different segments (e.g., fast-track
  segments resolve in days, full-review segments take weeks),
  a single windowed rate may be inadequate. Per-cohort window
  parameterization or per-cohort separate signals may be required.
- **The fix-window-or-document call is a team call, not a solo
  decision.** Extending a window changes the metric's definition;
  the team should explicitly endorse the new window or explicitly
  accept the documented limitation. Don't unilaterally extend.
- **This applies to attribution windows too**, not just conversion
  rates. The same logic governs "first-touch attribution windows",
  "post-impression conversion windows", "cohort retention windows".
  Probe the underlying time-to-event distribution before picking
  the window.

## See also

- `availability-tier-conflates-base-rate-with-data-gap` — sister
  skill on per-signal grading discipline (same probe-before-grade
  family: measure the underlying data before assigning a label).
- `verify-plan-constants-against-data` — for verifying numeric
  constants in plan docs (different surface, same probe-before-ship
  family).
