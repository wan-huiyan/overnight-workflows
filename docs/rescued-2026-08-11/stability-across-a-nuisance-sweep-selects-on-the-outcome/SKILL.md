---
name: stability-across-a-nuisance-sweep-selects-on-the-outcome
description: |
  Use when an analysis depends on a nuisance parameter nobody has pinned exactly — a
  correction factor with an admissible range, a threshold, a cut-off, a matching
  radius, a model or prompt version, a date window — and you are about to restrict
  the analysis to the units whose ANSWER is the same at every value in that range.
  Phrasings that signal it: "only the marks/rows whose verdict is stable across the
  window", "restricted to units where the conclusion does not depend on the
  parameter", "we report only what survives the sweep", "the robust subset",
  "conservative: we drop anything ambiguous". The trap: when the per-unit answer is
  BINARY, one class is usually stable BY CONSTRUCTION (a unit that never fires is
  stable at every value with probability 1), so the filter can only delete units of
  the OTHER class — and it deletes them at different rates per arm. Conditioning on
  it does not make a comparison conservative. It MANUFACTURES A NULL. Symptom: a
  strongly significant unfiltered association becomes "not distinguishable from
  nothing" once you restrict, and the restricted arm sizes differ sharply from the
  full ones. Trigger also when a reviewer asks "what exactly does your filter
  delete, and is it correlated with the outcome?", or when the honest answer to a
  sweep is "the result flips inside the admissible range" and someone is looking for
  a subset that makes a single headline sayable.
author: Claude Code
version: 1.0.0
date: 2026-08-07
disable-model-invocation: true
---

# Restricting to units that are "stable across the sweep" is selection on the outcome

## Problem

Your measurement depends on a parameter you do not know exactly. The honourable
instinct is to sweep it and report across the range. The next instinct — the one
that is wrong — is to say:

> "The parameter is only pinned to ±0.03. So I will report only the units whose
>  answer is the same at every value in that window. That is the conservative
>  thing to do."

It is not conservative. **`stable` is a deterministic function of the per-unit
verdict vector**, so it is a variable computed FROM the outcome, and conditioning
on it is textbook selection on the outcome.

The asymmetry that makes it lethal is easy to miss. With a binary verdict:

- A unit that reads **negative at every parameter value** is stable — with
  probability 1, by construction.
- A unit that reads **positive anywhere** is stable **only if** it reads positive
  *everywhere*.

So the filter can delete positives and nothing else. And because the arm you care
about is usually the one the parameter disturbs most (see *Notes*), it deletes
them **differentially by arm**. The result is a null you built.

## Trigger conditions

- A nuisance parameter has an admissible RANGE, not a value, and the analysis
  sweeps it.
- Anywhere the words "stable", "robust subset", "survives the window",
  "unambiguous", "consistent across settings" gate which units are counted.
- The per-unit output is binary (fires / does not fire, passes / fails, YES / NO).
- The filtered result is a null and the unfiltered result is not — or the filtered
  denominators are much smaller in one arm than the other.
- Someone is looking for a way to state ONE headline when the honest answer is
  "the result depends on the parameter".

## Solution

1. **Report the answer at EVERY parameter value, unfiltered.** One row per value,
   with its own n and its own p. That table is the finding.
2. **If the answer flips sign inside the admissible range, say so as the result.**
   "Unresolved, and here is the one number that would resolve it" is a real
   conclusion and often a more actionable one than either sign. Name what would
   narrow the parameter — usually cheaper than more data.
3. **Never let a filter derived from the outcome choose the analysis population.**
   If you want a robustness statement, make it about the ANSWER ("the association
   holds at f = 0.66 and 0.69 and not at 0.75"), never about a subset of units.
4. **Keep the stability statistic — as a diagnostic, not a rate.** "Which units are
   unstable, and are they concentrated in one arm?" is a genuine and often strong
   result about your measurement's precision. Report it as a comparison of
   *stability*, never as a comparison of outcomes *within* the stable set.
5. **Make the module refuse.** If code prints a rate over a stability-filtered
   subset, delete that code path and leave a comment saying why, so the next
   person does not restore it as an obvious improvement.

## Verification

Three checks, all cheap, and the first two are decisive:

    # 1. Does the filter only ever delete one class?
    never_positive = [u for u in units if not any(u.verdicts)]
    assert all(u.stable for u in never_positive)      # if this passes, the filter is one-sided
    dropped = [u for u in units if not u.stable]
    assert all(any(u.verdicts) for u in dropped)      # every deletion is a positive

    # 2. Does it delete unevenly by arm?
    #    survival among ever-positive units, per arm — if these differ, the
    #    filtered comparison is biased, not conservative.

    # 3. Compute the headline WITH and WITHOUT the filter at every parameter value.
    #    If they disagree, the unfiltered per-value table is the answer.

## Example

Owner-drawn region boxes on route cards had to be corrected for a sideways squeeze
the grading page introduced; the correction factor was pinned only to **0.69 over
an admissible 0.66–0.75**. The analysis asked whether a box where the owner asked
for something to be removed sits on an out-and-back stretch of route.

Reported over the marks whose verdict was stable across the window: **6 of 22
(27%) against 13 of 97 (13%), Fisher p = 0.12** — published as *"not
distinguishable from nothing; the thing he circles is not a dead-end stub."*

Unfiltered, per factor:

| f | removal arm | other marks | p |
|---:|---:|---:|---:|
| 0.66 | 24/37 · 65% | 22/108 · 20% | <0.00001 |
| **0.69** *(pinned)* | **22/37 · 59%** | 22/108 · 20% | **0.00002** |
| 0.75 | 7/36 · 19% | 19/108 · 18% | 0.81 |

The measured asymmetry: **all 33 boxes the filter dropped read positive somewhere,
and every one of the 92 never-positive boxes survived.** Among ever-positive units,
6 of 26 removal marks survived against 14 of 27 others.

The correct headline was the opposite of the published one — *unresolved, and the
correction decides it* — and the actionable conclusion was "tighten the factor",
not "no". The defect was found by an adversarial reviewer, not by the author, and
the author's own shipped ruling ("a narrow box at this precision cannot carry a
conclusion") already forbade the conclusion the analysis published.

## Notes

- **Why the bias points where it does.** The arm you care about is usually the one
  the parameter disturbs most — here the removal marks were the narrowest and most
  off-centre boxes, so the correction moved them ~6× further relative to their own
  size. The units whose answers are least stable are the units the question is
  about. That is not bad luck; it is the same property that made the parameter
  matter in the first place.
- **This is a distinct way of manufacturing a null** from the under-extraction case
  in the parent skill's null section. There, a parser silently drops exposed rows.
  Here, extraction is perfect and a *post-hoc filter* removes the positives. Both
  end at a clean negative, which is the answer that stops further work.
- **A range-reported figure is not a usable figure.** A ruling that says "report
  every box-derived number across 0.66–0.75" is satisfied by printing both ends —
  and says nothing about whether the ANSWER survives them. Reporting a range and
  asking whether the conclusion is stable are different questions; only the second
  tells you whether you have a result.
- See also: `single-anchor-point-estimate-needs-range-framing` (the same parameter
  problem before anyone filters), `verifier-rederive-from-raw-not-the-checked-artifact`
  (how this one was caught), and the parent skill's null section.
