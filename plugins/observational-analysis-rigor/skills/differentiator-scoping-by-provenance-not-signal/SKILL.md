---
name: differentiator-scoping-by-provenance-not-signal
description: |
  Use when scoping, defining, filtering, or categorizing something whose stated
  purpose is to "highlight our strength / differentiator", "show what makes us
  different", "surface what we have that [a competitor / a prior vendor / another
  team] doesn't", or "lead with our unique value". Triggers during brainstorming
  / requirements / taxonomy design — the moment you're about to lock the
  definition of the category the deliverable hangs on. The trap: defining the
  category by its broad CONCEPT (the signal) instead of by PROVENANCE (the
  source). A concept-based definition silently includes items that match the
  concept but come from the same source the competitor already has — so badging
  them as "our differentiator" invites "we already had that". Use also when
  reviewing such a definition, or when an advisor / reviewer flags that a scope
  answer "conflates source with signal".
author: Claude Code
version: 1.0.0
date: 2026-05-20
---

# Differentiator scoping — define by provenance, not by the broad signal

## Problem

When the goal of a piece of work is to **highlight a differentiator** — what
your team / product / dataset has that someone else doesn't — the foundational
decision is *what counts* as that differentiator. The natural move is to define
the category by its **broad concept**: "behaviour", "engagement", "AI-powered",
"real-time", "first-party". That definition is clean and easy to agree to.

It is also the wrong cut. A differentiator claim is about **provenance** — the
*source* of the thing, not the *concept* of the thing. A concept-based
definition will quietly sweep in items that genuinely match the concept but are
**sourced from exactly the system the competitor already has**. Badge those as
"our unique strength" and the client can fairly answer: *"we already had that."*

The conflation is hard to see because the concept word and the
differentiator claim feel like the same set — but they aren't.

## Context / Trigger conditions

- A user / client / stakeholder asks to "highlight our strength", "show our
  differentiator", "surface what we have that the other agency / competitor / prior
  vendor doesn't", "lead with our unique value".
- You are in brainstorming / requirements / taxonomy design and about to **lock
  the definition** of the category, filter, tag, or tier the deliverable hangs on.
- You are choosing between a "broad" and a "narrow" definition and the broad one
  looks cleaner / is the recommended-looking option.
- An advisor or reviewer says the scope answer "conflates source with signal",
  "over-claims the differentiator", or "the framing is source-specific but the
  definition isn't".

## Solution

1. **Separate the two dimensions explicitly.** For the category in question, name:
   - **Signal / concept** — what the thing *is* (the broad idea).
   - **Provenance / source** — *where the data comes from* / *who can observe it*.
   These are different axes. The differentiator lives on the provenance axis.

2. **Run the leak test.** Try to name one item that **matches the concept** but
   is **sourced from the thing the competitor already has**. If you can — the
   concept-based definition is leaking, and badging that item as "our
   differentiator" is an over-claim.

3. **Define the category on provenance.** Cut the scope by *what only we can
   see / produce*, not by the broad concept. If the concept genuinely spans both
   provenances and both are valuable, the honest resolution is usually a
   **two-tier split**: a strict "core differentiator" tier (provenance = ours
   alone) plus a broader "supporting" tier (real, but shared provenance) —
   labelled honestly so the supporting tier is never sold as the unique thing.

4. **Re-ask the scoping question with the source dimension made explicit.** If
   you already asked and got a concept-based answer, that answer was made
   *without the source dimension on the table* — it doesn't count as informed
   consent to the conflation. One extra clarifying round-trip is far cheaper than
   shipping a mislabeled differentiator to a client who will notice.

## Verification

- Every item inside the "differentiator" category passes the leak test: none of
  them is sourced from what the competitor already has.
- If a two-tier split was used, the supporting tier is labelled with its true
  provenance and is never referred to as the unique strength.
- The person who asked can articulate *why* each tier is or isn't the
  differentiator — in provenance terms, not concept terms.

## Example

A dashboard client asked to "surface more on-site behaviour insight — highlight
our strength, because a prior vendor already did the CRM analysis."

- **Concept-based definition (the trap, initially recommended and accepted):**
  "behaviour = anything the user actively does" — web visits, logins,
  *and* campaign/event attendance, checklist completion.
- **Leak test:** campaign attendance and checklist completion are real
  "behaviour" — but they are recorded *in the CRM*. The prior vendor, who
  worked from the CRM, already had them. Badging them "our differentiator"
  would have been an over-claim.
- **Provenance-based resolution (two-tier):** Tier 1 = portal clickstream (web
  visits, searches, logins — observable only in *our own* first-party event
  stream, genuinely unseen by CRM-based work); Tier 2 = observed engagement
  (attendance, checklist — real, but CRM-sourced, labelled honestly as
  supporting). The whole deliverable was then built on the two-tier definition.

The concept-based answer had been *recommended by the model and picked by the
user* — it took an advisor pass to surface that the differentiator goal demanded
a provenance cut. The fix was one re-asked question.

## Notes

- The tell: the **concept word** ("behaviour", "engagement", "AI", "real-time")
  and the **differentiator claim** ("what others don't have") name different
  sets. Whenever a task pairs a broad concept with a "this is what makes us
  different" goal, suspect the gap.
- This is a brainstorming / scoping-time skill — it fires *before* implementation.
  Catching it after the taxonomy ships means re-cutting everything downstream.
- Honest labelling beats a bigger-looking category. A smaller, provenance-true
  differentiator that survives "we already had that" is worth more than a broad
  one that doesn't.
- Related: a two-tier split is also the honest move when a metric or claim mixes
  strong and weak evidence — see `single-anchor-point-estimate-needs-range-framing`
  for the dashboard-copy sibling of "don't let a clean framing overclaim".
