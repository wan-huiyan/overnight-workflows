---
name: plant-the-failure-before-you-trust-the-check
description: |
  A check that has only ever passed has never been shown to work. Before trusting a guard,
  a test, an agent's "finding closed", or a cost fix, make the thing it exists to catch
  actually happen and confirm the check turns RED — and plant that failure in the
  PRODUCTION path, not in the fixture. Use when: (1) you wrote a guard or a test and it
  passed the first time; (2) a subagent reports findings closed and you are about to relay
  that upward after grepping the source and seeing the mechanism there; (3) you are about
  to declare a cost, volume or performance problem fixed; (4) a scan, canary or leak check
  came back clean and you cannot say what would have made it dirty. One defect wearing four
  faces, all of them evidence that cannot come out negative: a fixture that already carries
  the field the code was supposed to fetch, so deleting the fetch keeps the test green; a
  mechanism present in the source but never executed against a bad input; a canary searching
  the message body when the token sits in the filename; a fix that moved cost-per-run when
  the problem was volume. Adjacent to `verifier-rederive-from-raw-not-the-checked-artifact`,
  which hardens where a check gets its expected values; this skill asks the prior question,
  whether the check is capable of failing at all.
author: wan-huiyan + Claude Code
version: 1.0.0
date: 2026-08-17
---

# Plant the failure before you trust the check

## Problem

Every artifact below reported success, and every one of them was empty:

- A guard was written, a test was written for it, and the test passed. Then the code was
  changed so it **stopped asking GitHub for the field** the guard checks — and the test
  **still passed**, because the fixture already contained that field. In production the
  field would have been empty and **the guard would have protected nothing while looking
  correct.**
- An agent reported **eleven audit findings closed**. The closures were checked by grepping
  the source: every mechanism was present, so "all eleven closed" was relayed upward. A
  parallel lane then **executed** them. A delete row with the bucket changed to
  `wrong-bucket` **passed**. Swapping one destination proof for a different but valid
  digest **passed**. Reducing all cross-verification to a single row **passed**. **Eight of
  the eleven were still open.**
- A leak canary came back clean. The token it was hunting sat in the **filename**; the
  canary searched the message **body**.
- A continuous-integration cost problem was diagnosed **three times and got it wrong three
  times** — twice fixing cost-per-run when the problem was volume, once assuming drafts
  were the volume when drafts were **5.2%** of it.

The common shape: **evidence that cannot come out negative.** A passing check, a present
mechanism, a clean scan and a shipped fix are all indistinguishable from their empty
versions, and nothing in the report tells them apart. The absence of a red signal was read
as the presence of a working one.

## Context / Trigger conditions

Fire this skill when any of these is true:

- You wrote a guard, an assertion, a schema check or a test, and **it has never been red**.
- A subagent, a review lane or your own earlier turn reports an item **closed / fixed /
  verified**, and your evidence for relaying it is that you **read the code and the
  mechanism is there**.
- You are about to report a cost, latency, volume or spend problem as fixed.
- A scan (secret scan, leak gate, canary, lint) returns clean, and you cannot name the
  concrete input that would have made it dirty.
- Any number is about to be repeated rather than re-measured — a count carried from a brief,
  a figure transcribed from another document.

## Solution

### 1. Break it on purpose, in the production path, before you trust it

Write the guard, then **make the bad thing happen and watch the check go red.** Not a
thought experiment: an actual run with the failure planted.

**Plant it where production would fail, which is usually not the fixture.** The guard above
survived a corrupted fixture and died on a corrupted *fetch* — the fixture carried the field
whether or not the code ever asked for it, so every artifact-side mutation left the test
green. Delete the call that retrieves the data, drop the field from what the real source
returns, point the code at an empty response. If the test stays green, the fixture is doing
the work the code was supposed to do.

Two checks, in this order:

```
1. Delete the body of the guard        -> its own test must go RED.
2. Keep the guard, break the CODE PATH -> the same test must go RED.
```

A guard that passes step 1 and fails step 2 is a guard over a fixture, not over the system.

### 2. Verify a closure by running the negative case, never by grepping for the mechanism

**The presence of a mechanism is not evidence that it works.** Grep answers "does this claim
about the code hold" — that is a real and useful question, and
`task-framing-claims-need-subagent-grep-verify` is right to prescribe it. It does not answer
"does this guard stop the thing it names." Only an execution answers that.

For each closed finding, write down the **input that must be rejected**, feed it in, and
record the verdict:

| Finding says | The negative case to run |
|---|---|
| "destination is now validated" | submit a row with the bucket set to `wrong-bucket` |
| "the proof is bound to the object" | submit a different but individually valid digest |
| "cross-verification is enforced" | reduce cross-verification to a single row |

All three of those **passed** on a build whose source contained every mechanism. Run the
negative cases in a lane that did not write the fix, and treat a closure without an executed
negative case as **open**, not as closed-pending-evidence.

### 3. A fix that measures the wrong axis reads exactly like a fix that worked

Before fixing a quantity, decompose it and **measure every factor**, then say which one
moved. Cost is a product:

```
cost  =  runs  ×  minutes-per-run  ×  rate
```

Three diagnoses in one run, three misses: two of them cut minutes-per-run while the growth
was in `runs`, and the third guessed which runs made up the volume — drafts, which turned
out to be **5.2%**. Each fix shipped, each looked plausible afterwards, and none of them
changed the bill, because a fix on a factor that was not the problem produces the same
report as a fix that worked: the change is real, the number does not move, and the next
measurement is weeks away.

The same arithmetic applies to any product you are about to attack on one term — request
count against payload size, rows scanned against bytes per row, agents against tokens each.

### 4. Put the canary where the leak is

A canary proves something only if the thing it hunts would actually reach the place it
looks. Enumerate the surfaces the payload can travel through — filename, path, headers,
message body, log line, metadata — and **name the surface your canary covers**. The one
above searched bodies; the token was in a filename; the report was clean and meant nothing.

Related traps in the same family, each of which produces a truthful-looking report:

- **A clean report whose scope is narrower than its apparent claim is a false report.** State
  the scope in the same sentence as the verdict, every time.
- **A transcribed count reads exactly like an observed one.** Re-measure rather than repeat,
  and say which one you did.
- **Evidence built out of the thing it checks proves nothing** — see
  `verifier-rederive-from-raw-not-the-checked-artifact`.
- **A decided item and an undecided one look identical without a marker.** Mark the decision
  explicitly; silence is not a decision.
- **Data retained but unfindable is data lost while every count reconciles.** Retrieve a
  sample by the path a consumer would actually use.
- **Fixes interact.** Closing one item reopened another in this run; re-run the whole negative
  set after the last fix, not once per fix.

## Verification

You have done this when, for each guard or closure you are about to report:

- You can name the exact planted failure, and you watched the check turn red on it.
- The planted failure was in the code path, and the fixture-only variant is recorded as
  insufficient rather than as a pass.
- Every closed finding has an executed negative case with a recorded verdict, not a source
  citation.
- Every "clean" verdict is stated with its scope in the same sentence.
- Any quantity reported as fixed has all of its factors measured, with the one that moved
  named.

## Example

The eleven-finding relay is the cheapest illustration. The audit lane produced eleven
findings; the implementing lane fixed them and reported all eleven closed; the orchestrator
grepped the source, confirmed each mechanism was present, and passed "all eleven closed"
upward. A second lane then executed the negative cases and found **three guards that accepted
the exact input they were written to reject** — wrong bucket, substituted digest,
single-row cross-verification — and eight of the eleven findings still open.

Nothing in the first pass was careless. The mechanisms really were in the source; the reading
really was accurate; the report was wrong anyway, because reading a mechanism and running it
answer different questions.

## Notes

- **This is not a reason to distrust grep.** Grep is the right tool for "is this claim about
  the code true". The rule is narrower: it cannot stand in for an execution when the claim is
  that a guard *stops* something.
- **The order matters.** Planting the failure after the guard is green is still worth doing,
  but planting it first is what stops you from writing a guard around a fixture in the first
  place — you cannot write the always-green version if the red case exists before the guard.
- **Cheap to run, and it stays cheap.** The negative cases become the regression suite; the
  planted failures become the adversarial fixtures. Keep them, or the next change quietly
  restores the tautology.
- **Where a check is over a rendered or derived artifact** — a report, a dashboard, a
  serialized payload — pair this with `verifier-rederive-from-raw-not-the-checked-artifact`.
  That skill stops the check reading its expected value from the thing under test; this one
  stops the check being unable to fail whatever it reads.
- For an adversarial fixture that must corrupt a value covered ONLY by the new check, see
  `test-fixture-nondiscriminating-verify-by-mutation` — corrupting a value an older check also
  catches leaves the new detector with zero real coverage.

## References

- `verifier-rederive-from-raw-not-the-checked-artifact` — a conformance judge must re-derive
  its expectations from raw inputs rather than read them off the artifact it is checking
  (same family, the input side).
- `task-framing-claims-need-subagent-grep-verify` — when a grep IS the right verification:
  checking a dispatcher's factual claims about what a codebase contains.
- `overnight-review-panel-blocked-reviewer-reads-as-clean` — the review-panel member of this
  family: a reviewer that could not see the code reads as a clean one.
- `cross-lane-audit-quote-verbatim-and-pin-the-commit` — how the negative cases above got run
  at all: a second lane auditing the first, against a pinned commit.
- `overnight-multi-issue-implementation` — "Never truncate a findings payload" and "what an
  autonomous run may and may not decide", the overnight workflow these checks sit inside.
