---
name: held-out-cut-tuned-on-its-own-errors-manufactures-the-lift
description: |
  Use when a model, score, rule or threshold is being evaluated against a held-out set, and
  especially when a round reports a headline that clears its target. Two failures, one set.
  First: a cut chosen AFTER looking at which held-out items came back wrong is not a
  measurement, it is a fit — one such round reported balanced accuracy 0.8019 against a
  target, at the cost of 9 of 39 true positives, where the same signal chosen honestly was
  worth 0.7721. The number moves in the right direction and nothing in the pipeline says
  anything, so an adversarial verifier has to hunt this specific thing BY NAME rather than
  review the methodology in general. Second: a held-out set degrades with every look, and
  looks are spent silently — one run read the same set three times. Budget them, count them,
  and state the count next to every number; require any revived idea to clear
  training-half validation before it is allowed to spend another look, because the training
  half will endorse ideas the held-out half refuses (an idea worth +0.034 on the training
  half came back at −0.041). Use when: (1) reporting or verifying a held-out metric; (2)
  choosing a threshold, cut, inclusion rule or feature set; (3) reviving an idea that was
  already tried; (4) any round whose result just clears a pre-set target.
author: wan-huiyan + Claude Code
version: 1.0.0
date: 2026-08-17
---

# A cut tuned on its own held-out errors manufactures the lift

## Problem

A round reports **balanced accuracy 0.8019**, apparently over the target it was chasing. The
pipeline is clean: the split is real, the held-out half was never trained on, the arithmetic
reproduces.

The cut was chosen **after** inspecting which held-out items the model had got wrong. That
one look converted the held-out set into training data for the threshold, and it was not a
free conversion: the tuned cut **cost 9 of 39 true positives**. Chosen honestly — fixed
before the outcomes were seen, or fixed on the training half — the same signal was worth
**0.7721**.

Both numbers are real. Neither is a fabrication. The difference between them is the entire
value of the held-out set, and nothing downstream can tell them apart, because the *only*
evidence that the first number is inflated is the **order in which two things happened** —
and order does not appear in a metrics table.

The second half of the problem is that this is cumulative and quiet. **Every look at the
held-out set degrades it**, whether or not anything is tuned; a look is spent by reading the
per-item errors, by re-scoring after a tweak, by "just checking whether the new variant
helps". In one run the same held-out set was read **three times**, and nobody was counting.

## Context / Trigger conditions

- A threshold, cut point, inclusion rule, feature set or ensemble weight is being chosen, and
  held-out outcomes are visible to whoever chooses it.
- A round's headline **just clears** a pre-set target — the shape that gets waved through.
- An idea that was tried and dropped is being revived on the strength of a training-half
  result.
- You are verifying somebody else's evaluation and their methodology section reads fine.
- Any per-item error analysis on the held-out set — the most useful-feeling and most expensive
  thing you can do to it.

## Solution

### 1. The verifier must hunt this by name

A general instruction to "check the methodology" does not find it. Ask the specific question,
in these words, of every reported number:

> **Was any threshold, cut, inclusion rule, feature set or stopping point chosen after
> anyone saw held-out outcomes — including per-item errors? If yes, this is a fit, not a
> measurement. Report the value the honestly-chosen rule gives.**

Then require the honest counterfactual **as a number**, not as a caveat: re-run with the cut
fixed on the training half alone, and report both. The pair `0.8019 tuned / 0.7721 honest` is
the finding. A caveat saying "the threshold was selected with reference to validation
performance" is not — it reads as routine and it survives review.

Ask for the **cost of the tuning in units of the thing being predicted**, too: nine lost true
positives out of thirty-nine is a sentence anyone can act on, where a change in the third
decimal place of balanced accuracy is not.

### 2. Count the looks, and state the count next to the number

Treat the held-out set as consumable. Keep a look ledger — a file, not a memory — with one
line per look: what was read, by whom, why, and what changed afterwards. Then:

- **Every reported held-out number carries its look count.** "0.77 (look 3 of a budget of 4)"
  tells a reader something that "0.77" does not.
- **Set the budget before the first look**, at the same time as the target, and treat exhausting
  it as a result rather than an accident.
- **Reading per-item errors is a look.** It is the most degrading kind, because it is the one
  that teaches you which way to move the cut.

### 3. A revived idea must clear the training half before it may spend a look

The training half will endorse things the held-out half refuses, and this is not rare: in the
same run an idea measured at **+0.034** on the training half came back at **−0.041** on the
held-out half. That is the mechanism the split exists to expose — so let it work, and pay the
look only for candidates that have already survived the free check.

The gate, in order:

```
revived idea
  -> re-validate on the TRAINING half            (free; repeat as often as you like)
  -> if it does not clear there, it is dead      (record it; do not spend a look)
  -> if it clears, and the look budget allows    -> one look, logged, count stated
```

### 4. Fix the rule before the data, and say so in the same sentence as the result

Where a criterion was pre-registered, computing it after the outcome is known defeats the
registration — the honest autonomous action is to **disclose that it was never satisfied**
rather than to recompute it favourably. Where nothing was pre-registered, write the rule
down before the look, in a file that is committed, so "chosen before" is a checkable fact
rather than a recollection.

## Verification

- The evaluation report states, per number, whether the rule that produced it was fixed
  before or after held-out outcomes were visible.
- Any post-hoc rule is reported **beside** its honestly-chosen counterpart, with both values.
- The tuning cost is expressed in outcome units (true positives gained or lost), not only in
  the headline metric.
- A look ledger exists, the count in it matches the count quoted next to the number, and the
  budget was set before the first look.
- Every revived idea has a training-half result recorded before its held-out look.

## Example (anonymized)

A scoring round chased a balanced-accuracy target on a held-out half. The reported result was
**0.8019** — over the line, and it would have shipped as such. The threshold behind it had
been picked after reviewing the held-out items the model was getting wrong, which is exactly
the information a threshold is supposed not to have. Fixed honestly, the same signal scored
**0.7721**, and the tuned version had thrown away **9 of 39 true positives** to get its
headline.

In the same run: an idea that measured **+0.034** on the training half measured **−0.041** on
the held-out half — a sign flip, on the same idea, between the two halves. And the held-out
set was read **three separate times** over the run without anyone tracking that a budget was
being spent.

## Notes

- **Balanced accuracy is not the point.** Substitute AUC, F1, precision at k, lift over base
  rate — anything chosen or tuned with sight of held-out outcomes has the same defect.
- **A "checked and retired" record is honest content.** An idea that dies on the training half
  is a result worth logging (see the flagship protocol's step 8 ledger); it also stops the
  same idea being revived in a later session and spending a look then.
- **This is the evaluation-set member of the same family as
  `blind-rederive-pass-when-orchestrator-already-read-the-answer`** — there, knowing the answer
  contaminates a verifier's prompt; here, seeing the answers contaminates a threshold. Both
  are fixed by controlling *who sees what, when*, rather than by trying harder afterwards.
- **Adversarial verification is what catches it**, and the adversary needs the specific
  question. A reviewer asked to check the analysis will confirm the arithmetic, which is
  correct.
- Marker-vs-lever discipline (step 5 of the flagship protocol) still applies on top: a signal
  validated honestly on a held-out half is a validated *predictor*, not a proven lever.
