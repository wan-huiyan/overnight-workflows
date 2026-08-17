---
name: cross-lane-audit-quote-verbatim-and-pin-the-commit
description: |
  Run two independent lanes that audit each other's work, hand each lane the other's words
  VERBATIM rather than your summary of them, and pin any commit a lane is auditing with
  `git update-ref refs/audit/<name> <sha>` so a rewrite cannot move it out from under the
  audit. Use when: (1) two agent sessions, models or workstreams are working the same
  problem and you want real coverage rather than two agreeable reports; (2) you are about
  to forward another lane's specification, ruling, constraint or finding into a brief, a
  test, or a checklist; (3) an agent is auditing a commit while another agent may still
  amend, rebase or squash it. The failure this prevents has one shape: the other lane
  specified something precisely and you carried it forward approximately — a blanket clock
  where an upper bound was granted, an open path prefix where the rule named exact files,
  "before commit" where they wrote "before edit". No self-check catches these, because your
  checks were built from your own restatement and agree with it perfectly. Twenty-six such
  errors surfaced in one run, none of them by either lane's own checks. Sister to
  `overnight-review-panel-blocked-reviewer-reads-as-clean` (a reviewer that could not see
  reads as clean) and `plant-the-failure-before-you-trust-the-check` (a check that cannot
  come out negative).
author: wan-huiyan + Claude Code
version: 1.0.0
date: 2026-08-17
---

# Two lanes, quoted verbatim, against a pinned commit

## Problem

A single lane checking its own work has a blind spot with a precise shape, and it is not
carelessness.

You receive a specification from somewhere authoritative — the other lane, the owner, a
review finding. You restate it, in your own words, into a brief or a plan. You then build
your verification from that restatement. From then on every check you run agrees with your
restatement rather than with the source, so the drift is invisible from inside the lane no
matter how thorough the checking is.

**In one run, two lanes reading each other's work turned up 26 things that neither lane's
own checks had found. Every one of the mistakes had the same shape: the source specified
something precisely and it was carried forward approximately.** Three of them:

| The source said | What got carried forward |
|---|---|
| an upper bound was granted on a clock | a blanket clock |
| the rule named exact files | an open path prefix |
| "before **edit**" | "before **commit**" |

Each restatement is defensible read on its own. Each one widens or moves a boundary that
somebody had set deliberately, and each one passed every check the lane that wrote it ran,
because those checks were written from the same restatement.

There is a second failure that only shows up once two lanes are running: **the thing being
audited moves.** An agent amended the audited commit **twice** during the run. The audit
survived only because the commit had been pinned. A rewritten commit **vanishes from the
branch while every branch-based check keeps reporting normally** — the branch is healthy,
the tests pass, and the findings now point at a commit nobody can reach.

## Context / Trigger conditions

- Two sessions, models, or workstreams are on the same problem and you want the second one
  to be more than a rubber stamp — a different model or a different harness is ideal, since
  the point is a different set of habits, not a second opinion from the same ones.
- You are about to write another party's constraint into a brief, a guardrail, a test name,
  or a checklist item, and you are typing it rather than pasting it.
- An agent is auditing, reviewing, or measuring a specific commit while other agents on the
  same branch may still `--amend`, rebase, squash or force-push.
- A finding cites a commit, a line number or a file revision that a later session will need
  to reach.

## Solution

### 1. Quote the other lane verbatim; never paraphrase a constraint

Carry the sentence, not your understanding of the sentence. In practice:

- Paste the source's own words into the brief, in a quoted block, attributed, and keep your
  interpretation **beside** it rather than instead of it. If the two disagree later, the
  quote is the tiebreaker and it is right there.
- **Watch the four words that move boundaries**: a bound becoming a blanket ("up to N" to
  "N"), a specific becoming a prefix (three named files to a directory), a verb moving in
  time ("before edit" to "before commit"), and a qualifier dropped ("usually", "for this
  run", "if X"). These are where the 26 findings clustered.
- When you must summarize, mark it as your summary and keep the quote adjacent. A summary
  presented as the spec is the defect.

### 2. Give each lane the other's raw artifact, not your digest of it

The orchestrator is the leak. If lane B receives your notes on lane A, it audits your notes.
Hand over the actual file, the actual finding text, the actual diff — the same discipline as
`factcheck-subagent-needs-complete-sources`, applied to a peer lane rather than a verifier.

Ask each lane to report, per item, whether it is quoting or restating, and to flag any place
the two lanes' wordings differ. **The disagreement is the product.** Two lanes that agree on
everything either did the same work twice or read the same restatement.

### 3. Pin every commit a lane is auditing

Before dispatching an audit, create a ref that no ordinary branch operation touches:

```bash
git update-ref refs/audit/<short-name> <sha>          # pin it
git rev-parse refs/audit/<short-name>                 # confirm it resolves
```

Then have the audit lane read `refs/audit/<short-name>`, and cite it in every finding
alongside the branch name. Recover after a rewrite with the same ref:

```bash
git show refs/audit/<short-name> --stat               # the tree the audit actually read
git diff refs/audit/<short-name> <branch>             # what moved under the audit
```

A pinned ref survives `--amend`, rebase, squash and branch deletion, and it costs one
command. **The reason to do it before rather than after: after a rewrite there is nothing
left to pin.** Delete the ref (`git update-ref -d refs/audit/<short-name>`) when the audit
is closed, so the pinned objects can be garbage-collected.

### 4. Reconcile at the end, in both directions

Walk both lanes' findings against each other and classify every difference: the same finding
worded differently, a real finding one lane missed, or a **carry-forward drift** where the
two lanes are working from different versions of the same rule. The third kind is the one
this skill exists for, and it will look like a disagreement about the answer when it is
actually a disagreement about the question.

## Verification

- Every constraint in the brief is either a quote with attribution or is explicitly marked as
  a restatement sitting next to its quote.
- The second lane received the other lane's original artifacts, and can name the file it
  read.
- `git rev-parse refs/audit/<name>` resolves for every commit under audit, and each finding
  cites that ref.
- The reconciliation lists at least one item where the lanes differed. Zero differences
  across a non-trivial scope means the lanes were not independent — check what they were both
  reading.
- After any rewrite, `git diff refs/audit/<name> <branch>` was run and its output was read,
  rather than assuming the branch still contains what the audit saw.

## Example

Two lanes ran the same problem, each with its own checks, each reporting clean by its own
standards. Cross-reading produced **26 findings neither lane's own checks had raised**. They
were not exotic: an upper bound that had been granted was implemented as a blanket rule; a
constraint scoped to an explicit list of files was implemented as a path prefix; a hook that
was specified to run **before edit** was implemented to run **before commit**. Each shipped
inside a lane that had verified its own work against its own restatement, and each was
caught in seconds once the other lane read the original wording.

During the same run, an agent amended the commit under audit twice. The findings still
resolved, because the commit had been pinned to `refs/audit/<name>` before the audit
started. Without the pin the branch would have looked entirely healthy and the audit's
citations would have pointed nowhere.

## Notes

- **This is not code review.** A reviewer looks for defects in an implementation. A cross
  lane looks for divergence between what was specified and what was understood — and it
  reads the specification, not only the diff.
- **Cost.** A second lane is roughly a second run. It bought 26 findings here; scale the
  decision to what a wrong boundary would cost, and note that the cheaper tier is usually
  enough for the quoting check, which is a text comparison, while the judgement calls need
  the stronger model.
- **Pinning is worth doing even with one lane.** Any long-running audit, measurement or
  review over a branch that other sessions can rewrite deserves a `refs/audit/*` ref. It is
  the cheapest insurance in this file.
- **Do not let the second lane inherit the first lane's summary via the orchestrator's
  context** — that is the same anchoring problem as
  `blind-rederive-pass-when-orchestrator-already-read-the-answer`, one level up: there, the
  orchestrator leaks a number into the verifier's prompt; here it leaks a paraphrase into the
  peer lane's brief.

## References

- `blind-rederive-pass-when-orchestrator-already-read-the-answer` — keep the answer out of the
  verifier's prompt; this skill keeps your paraphrase out of the peer lane's brief.
- `factcheck-subagent-needs-complete-sources` — hand a checker the complete originals, never
  your own partial dump.
- `plant-the-failure-before-you-trust-the-check` — what the second lane should actually run:
  the negative case, not a reading of the source.
- `overnight-review-panel-blocked-reviewer-reads-as-clean` — the panel version of a lane that
  reports clean without having seen the work.
- `overnight-multi-issue-implementation` — "Coordinating with sessions you do not control",
  for the case where the other lane is a live session rather than a branch.
