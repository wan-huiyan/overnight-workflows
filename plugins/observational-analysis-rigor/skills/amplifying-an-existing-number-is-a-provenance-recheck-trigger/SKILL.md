---
name: amplifying-an-existing-number-is-a-provenance-recheck-trigger
description: |
  A request to ELEVATE / amplify / feature an existing number that's already in a
  deliverable — turn a prose figure into a chart, make it a headline, restore "the
  breakdown we had" — is itself the trigger to re-verify that number's provenance
  BEFORE amplifying it. Use when: (1) asked to add a visual/chart for a number that
  already exists as text, (2) asked to make an existing stat more prominent, (3) a
  stakeholder says "we had this insight, put it back / show it bigger", (4) a number
  was seeded earlier as "client feedback" / "stakeholder feedback" / "per the reviewer" and
  is being built on. Pre-existing and especially stakeholder-contributed numbers carry
  FALSE AUTHORITY — they look already-vetted, so they skip the provenance scrutiny a
  brand-new number gets, and may trace to nothing. Verify (grep the source, re-derive
  with triple-probe) before making them louder. Prevents amplifying a fabrication.
disable-model-invocation: true
author: Claude Code
version: 1.0.0
date: 2026-07-01
---

# Amplifying an existing number is a provenance re-check trigger

## Problem

A number is already sitting in a client-facing deliverable as quiet prose. A stakeholder
asks you to **make it louder** — "add a chart for that", "where's the breakdown, put it
back / show it bigger", "feature this stat". The natural move is to take the number at
face value (it's already shipped / reviewed, someone put it there) and just render it
more prominently.

That's the trap. **The number's presence is not evidence of its truth.** Numbers that were
*contributed* — seeded as "client feedback", "reviewer per-item feedback", "per the stakeholder",
or carried over from a prior session — carry **false authority**: they read as already-vetted,
so they skipped the provenance scrutiny a brand-new number would get. Amplifying such a number
(chart, headline, PDF) both **increases the blast radius** if it's wrong and **launders it** —
a chart looks computed even when the figure was never computed.

## Context / Trigger Conditions

- "Add a visual / plot / chart for the X finding instead of just words."
- "Where's the <breakdown/number> — I thought we had it? Put it back / make it prominent."
- You're about to elevate a figure that currently lives in a muted note / caption / aside.
- The number's git blame / comment says it was baked in as *feedback* ("reviewer feedback",
  "per client", "stakeholder said"), not from an analysis file or a cited query.
- A caveat/QA gate **hard-asserts** the exact number (e.g. a test requires the literal
  `70/65/25/15` to be present) — that gate is now a *fabrication-enforcer*, not a check.

## Solution

1. **Before amplifying, trace the number to a real source.** Grep the repo/analysis dir
   for the figure and the finding it belongs to. Ask: is there a computed artifact (query,
   notebook, analysis doc) that produced it — or only prose that *asserts* it? A note that
   the split was a "not-yet-done follow-up" is a red flag the number was never computed.
2. **If it traces to nothing, re-derive it — don't amplify on faith.** Use the project's
   verified derivation path (mirror the production join, not a hand-rolled one) and
   triple-probe (≥2 anchors × ≥2 reasonable definitions). See
   `finding-verification-live-bq-triple-probe`.
3. **Compare re-derived vs published.** Often the *ordering/story* reproduces but the
   *magnitudes* don't (the tell of a plausible-but-fabricated figure). Replace the numbers
   with the derived set; keep the qualitative claim if it survives.
4. **Amplify the verified number, with the caveat baked into the visual** (small-n,
   raw/not-adjusted, selection-inflated) — see
   `preserve-caveats-when-refactoring-annotated-content`.
5. **Fix any gate that hard-asserted the old number** so it asserts the *derived truth*,
   not the fabrication (update the literal it checks).
6. **Widen the blast check:** if this number was seeded in a batch ("baked stakeholder feedback
   across cards 01/04/08/…"), the *siblings* likely share the gap — flag them for the
   same spot-check, don't assume only the one you touched is wrong.

## Verification

- The amplified figure now traces to a named query/analysis file (or an inline `table.column`
  + SQL), not to prose that merely repeats it.
- Re-derived numbers are recorded (probe table + method) in an analysis doc.
- Any test/gate that referenced the old literal now references the derived one.

## Example (a decision-support dossier)

Card 03 carried, as a muted note, "among accepted prospects, deposit rates ≈ Info Session 70 /
Open House 65 / Webinar 25 / non-attender 15" (illustrative). The stakeholder asked to **elevate it
to a chart**. Instead of charting it, I traced it: the figures had been baked in as reviewer per-item
feedback in a prior commit, appeared in **no** computed analysis, and the by-type split was explicitly
logged elsewhere as a *not-yet-done follow-up*. Re-derived live (triple-probe, 2 anchors × 2
denominators, mirroring the production event-attendance join) → **85 / 80 / 50 / 25**. The **ordering
held** (in-person ≫ webinar ≫ non-attender) but the **magnitudes were far off** — classic
plausible-but-uncomputed. Charted the *verified* numbers with the raw/small-n caveat baked into the
PNG; updated the caveat gate's literal `70/65/25/15` → `85/80/50/25` (it had been enforcing the
fabrication); and flagged the sibling cards seeded by the same commit for a spot-check. Had I taken
the "just add a chart" brief at face value, I'd have laundered a fabricated number into a
client-facing plot.

## Notes

- The **elevation request is the gift**: it's the moment a stale/unsourced number gets fresh eyes.
  Treat "make X prominent" as "prove X first."
- Distinct from `dashboard-redesign-gated-on-finding-revalidation` (a *known* label-fix in flight
  during a redesign) — here the number's unsourced-ness is *undiscovered* until the amplify request
  forces the check.
- Distinct from `preserve-caveats-when-refactoring-annotated-content` (that governs keeping caveats
  attached; this governs verifying the *number itself* before making it louder).

## See also

- `finding-verification-live-bq-triple-probe` — the re-derivation mechanics (triple-probe, anchors).
- `data-provenance-verifier`, `verify-plan-constants-against-data` — provenance/constant checks.
- `preserve-caveats-when-refactoring-annotated-content` — bake the caveat into the amplified visual.
- `dashboard-redesign-gated-on-finding-revalidation` — the redesign-time sibling.
- `handoff-fabricated-locked-decision-vs-open-roadmap` — fabricated-authority in handoffs.
