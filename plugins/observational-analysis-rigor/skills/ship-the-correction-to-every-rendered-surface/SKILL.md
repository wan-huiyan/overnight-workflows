---
name: ship-the-correction-to-every-rendered-surface
description: >-
  The LAST MILE of a de-stale (protocol step 9): a corrected observational finding is not shipped until
  every RENDERED surface matches the ledger. Use AFTER the honest ledger (step 8) whenever a figure or a
  marker-vs-lever / causal verdict changes and that number already appears on a live surface — a
  served/baked HTML page, an embedded chart image, a client+dashboard "twin" pair, a dashboard payload
  or cache table. The trap: you correct the TEXT/captions and believe you shipped, but the baked chart
  image still renders the retired number (and its old causal title), a twin doc still asserts the retired
  claim, or a cached payload still serves the old value — so a corrected caption sits next to a chart that
  contradicts it. Also covers HOW to draw a retired-causal / marker-not-lever finding honestly. Trigger
  when you edit a figure that is displayed anywhere, or review a de-stale PR for completeness.
author: wan-huiyan + Claude Code
version: 1.0.0
---

# Ship the correction to every rendered surface

Step 8 gets the **ledger** right — the verdict and the replacement value. This step gets the **delivery**
right. A de-stale fails its whole purpose if the corrected number never reaches the eyes it was meant to
correct: the analysis is right, the stakeholder still sees the wrong chart. Editing the sentence that
quotes a number does **not** touch the other places that number is rendered — and those other places are
exactly where a stale figure keeps doing damage.

## The rendered surfaces a figure hides in (enumerate before you call it shipped)

1. **The served / baked copy of the doc.** Many client surfaces serve a *built* artifact (an assembler
   inlines fragments, base64-embeds assets, applies neutralizations), not the source you edited. Editing
   the source without re-baking ships nothing. Re-bake, and confirm the deploy actually re-baked — a
   content change may be date-gated or read from a cache table (the baked-payload-stale-after-merge trap).
2. **Embedded / baked CHART images — the biggest blind spot.** A caption edit does **not** regenerate the
   chart. If charts come from generators (e.g. matplotlib `make_*.py` → PNG, base64-inlined), the image
   still renders the OLD number *and* any retired causal framing baked into the title/annotation. **Grep
   every generator** for the stale figure AND the retired phrasing, regenerate the affected ones, re-bake,
   and **view the rendered image** (source shows presence; render shows what ships). Do **not** conclude
   "there is no chart pipeline" without grepping for generators first — that false assumption is what
   ships a text-only de-stale.
3. **Twin / mirrored copies.** Client copy + dashboard copy, an appendix bundle, an embed + its source.
   A partially-swept twin becomes internally self-contradictory (a corrected "confirmed leak" row beside
   an uncorrected "✅ real segment" callout citing the same retired figure). Sweep each twin spot-by-spot;
   scan each file for a claim that contradicts a SIBLING claim in the same file.
4. **Dashboard payloads / cache tables.** A separate baker/job may hold the number; the serving service
   reads the bake, not your edit. Trigger the bake (or confirm the schedule) and verify the live value.

## Match the corrected value to the TARGET cell's construct (don't cross constructs)

The same numeral can be a different **metric construct** in different cells — in-window@Nd share vs
cumulative/ever share vs enroll@120d vs full-cycle rate — with different by-level splits. When you swap a
stale figure for its "corrected cousin", confirm the target cell's *definition* matches the replacement's
construct, or you relabel an in-window cell with a cumulative number: a silent, plausible-looking error
that survives grep because the numeral "looks right". A `@Nd` / "in-window" qualifier ⇒ windowed; no
qualifier ⇒ usually cumulative. Re-derive on the source-of-truth cohort and compare by-level splits to
identify a cell's construct before swapping. (This sharpens the step-8 "Z% — as-of when?" red flag: it's
not only *as-of date*, it's the whole metric definition.)

## How to DRAW a retired-causal / marker-not-lever finding honestly

When step 4/5 retires a "lift" to an adjusted null (or downgrades it to a selection marker), the CHART
must say so — a bar chart still showing a hot `4×` bar is a stronger residual than any text tooltip.
- **Lead with the adjusted estimate as the visual headline** — the point estimate with its **CI whisker
  straddling the no-lift line (1×)**, so "null" is the thing the eye lands on.
- **Keep the raw gradient but subordinate it** — muted color, below/behind the headline, explicitly
  labelled *raw · self-selected · uncontrolled*. Keeping it is honest (it is the descriptive layer the
  caption still cites); leading with it is not.
- **Kill causal language in the title/annotations** — no "X convert far more", no "the gap peaks in the
  decision zone" arrow. The title states the finding ("adjust for level + program and the 'lift' vanishes").
- **The chart's numbers and words must match its caption verbatim** so chart and text cannot drift.

## Verify

- **Diff the served/baked artifact against the pre-edit base with asset payloads stripped** — the
  text-stripped diff is identical except the intended change; the only binary blobs that change are the
  charts you meant to regenerate. Re-bake on a branch based on the CURRENT merged base (the one carrying
  the text de-stale), never a stale feature branch, or you clobber/re-bundle merged work.
- **View every regenerated chart as an image** and read its numbers + title against the corrected caption.
- **No file contains two sibling claims that contradict each other** (grep the verdict badge and the raw
  figure in the same file, across every twin).
- The **live surface** (not just the repo) shows the corrected value after deploy/bake.

## Example

Barry `/actions` dossier de-stale (#1666). A first PR corrected the dossier TEXT — spine 14.7→18.7
cumulative; a card in-window rate re-derived to 13.5; a recruitment-event `~4×` "lift" retired to an
adjusted null (OR 1.03, p=0.81) — and shipped. But the two baked chart PNGs still rendered the retired
figures: one still titled "Event attendees enrol far more" with 4.0×/2.4× bars and a "gap peaks in the
decision zone" arrow; the other's trendline still anchored at 14.7. The corrected captions sat next to
charts that contradicted them. The claim "there is no chart-generation pipeline" was false — 27
`make_*.py` generators existed. A follow-up PR regenerated them (the event chart now leads with
`1.03× · null` + a CI whisker on the no-lift line, raw ratios muted + "self-selected, uncontrolled"; the
trendline baseline 14.7→13.5), re-baked on a branch off current main, and proved the served-copy diff was
image-stripped-identical except the 2 intended PNG blobs. A separate near-miss: the card's in-window 13.5
was almost "corrected" to the spine's 18.7 cumulative — same numeral, different construct.

## Notes
- This is the delivery counterpart to the analysis steps: steps 1–8 make the finding *true*; this step
  makes the *shipped surfaces* true. A de-stale PR review should check it explicitly.
- Companion traps in this bundle: the step-7 re-derivation skills (a re-bake is a fresh construction to
  verify) and step-8 framing. External companion: a dedicated construct-swap skill
  (`verify-metric-construct-before-swapping-a-stale-figure`) covers the in-window-vs-cumulative trap in full.
