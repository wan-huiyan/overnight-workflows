---
name: two-quantities-compared-must-share-a-frame
description: |
  Use when a finding rests on comparing two numbers that were DERIVED DIFFERENTLY —
  "the movement is bigger than the object", "the error exceeds the effect", "the
  gap is wider than the band", "noise dominates signal" — and especially when a
  coordinate correction, rescaling, projection, unit conversion, normalisation or
  reprojection was applied somewhere upstream. The trap: a transform gets applied
  to POSITIONS but not to the SIZES, extents, widths or errors derived from the
  same raw source, and both are then printed in the same unit (px, m, %, days,
  dollars) so nothing looks wrong. The comparison is between two rulers. Symptom to
  watch for: a headline that turns on a narrow margin ("28.9 against 28.7"), a
  ratio near 1.0 that would be far from 1.0 in one frame, or a claim of the form
  "A exceeds B" where A and B came from different code paths. Trigger also when a
  reviewer asks "is that width in the corrected frame?", when a correction factor
  is applied via a helper that only takes coordinates, or when you are about to
  publish an absolute-vs-absolute comparison you could have published as a ratio.
author: Claude Code
version: 1.0.0
date: 2026-08-07
disable-model-invocation: true
---

# Two numbers you compare must be in the same frame — a correction applies to sizes, not just positions

## Problem

An upstream correction rescales coordinates: `u' = 0.5 + (u − 0.5) / f`, or a
projection, or a unit conversion, or a normalisation. You apply it where the code
obviously needs it — the positions — and then compute something else from the RAW
values without thinking of it as a coordinate at all:

```python
ecc   = abs(centre(box) - 0.5)
shift = abs(ecc / lo - ecc / hi)     # corrected: a fraction of the PICTURE
width = box.x1 - box.x0              # NOT corrected: a fraction of the WRAPPER
print(f"{shift * PX:.1f} px", f"{width * PX:.1f} px")   # same label, two frames
```

Both print as "px". The comparison `shift > width` then means nothing, and it will
be **most wrong exactly where the correction matters most**, because that is where
the two frames diverge furthest.

A size is a coordinate difference. **Anything derived from corrected coordinates
inherits the correction — extents, widths, radii, standard errors, distances,
durations, and every margin computed from them.**

## Trigger conditions

- A correction/transform helper takes *points* or *coordinates* and there is a
  second quantity (a width, an extent, an error bar, a tolerance) computed
  straight from the raw source.
- A headline reads "X is bigger than Y" where X and Y come from different code
  paths, and the margin is thin.
- A test guards the claim and passes by a hair — the margin *is* the mismatch.
- Two things are printed with the same unit suffix but assembled in different
  functions.
- The transform is a division/multiplication by a factor: whichever quantity is
  missing the factor is off by exactly that factor, which is often close enough to
  1 to look plausible and far enough to flip a comparison.

## Solution

1. **Convert at the boundary, once.** Put every quantity into the frame you will
   report in as it enters the analysis, not at the print statement. If a raw and a
   corrected version both exist, name them so (`width_raw`, `width_px`) and never
   let the bare name be ambiguous.
2. **Publish the RATIO, not the two absolutes.** `shift / width` cannot hide a
   frame: if one side is missing the factor, the ratio is wrong by that factor and
   its interpretation ("69% of its own width") makes the error visible in a way
   that "28.9 vs 28.7" does not. A ratio also survives a later change to the unit.
3. **State the frame in the output.** One line above the table — "widths are in the
   picture frame, quoted at f = 0.69" — costs nothing and is where a reader catches
   it.
4. **Distrust a thin margin.** A conclusion that turns on 28.9 against 28.7 should
   be re-derived from first principles before it is published, precisely because a
   frame error of a few percent produces exactly that.
5. **Keep the qualitative finding, which usually survives and is usually bigger.**
   The frame error normally distorts a comparison the two arms share, so the
   *contrast between arms* often stands after the fix — and is the stronger claim
   anyway.

## Verification

- Recompute the comparison with BOTH quantities in each candidate frame. If the
  verdict changes between frames, you had a frame bug, not a finding.
- Assert the invariant rather than the value: a guard that says
  `shift_over_width_armA > 4 * shift_over_width_armB` cannot be satisfied by a
  units slip the way `shift_px > width_px` can.
- Grep for every use of the raw source alongside the corrected one; the transform
  is usually applied in one function and skipped in a sibling.

## Example

Region boxes on 700 px cards carried a sideways correction `1/f` because the page
recorded x as a fraction of a container wider than the picture. `displacement()`
corrected the *centres* and left the *widths* raw, printing both as "px of 700":

> "A removal mark's centre travels **28.9 px** across the admissible window while
>  the mark itself is only **28.7 px** wide. We do not know which part of the route
>  he circled to within its own width."

Put the width in the picture frame (`width / f`) and it is **41.6 px**: the shift
is **69%** of the mark's width, not 101%. The headline was withdrawn. The guard
asserting it had passed on the 0.2 px margin the mismatch created — the margin was
the bug.

**What survived was larger and had been hidden underneath it:** the shift eats 69%
of a removal mark's width against **12%** for every other mark — a six-fold
difference concentrated on the arm the question was about. Reported as a ratio, it
is both correct and stronger, and no frame can hide inside it.

## Notes

- The same shape appears without any geometry: a spend figure converted to one
  currency compared against a threshold in another; a p50 latency in ms against a
  budget in s; an effect in log-space against a tolerance in raw units; a duration
  in business days against a window in calendar days.
- **A test can institutionalise the bug.** If the guard is written against the same
  two mismatched expressions, it will pass forever and read as protection. Assert
  the ratio or the between-arm contrast instead.
- See also: `stability-across-a-nuisance-sweep-selects-on-the-outcome` (the sibling
  defect in the same analysis — both turned a real association into a wrong
  published claim, and both were caught by a separate reader rather than the
  author), and `numeric-rederive-confirms-value-not-label-or-cohort`.
