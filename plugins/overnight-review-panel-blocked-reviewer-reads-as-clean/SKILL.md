---
name: overnight-review-panel-blocked-reviewer-reads-as-clean
description: |
  Overnight specialization of `code-reviewer-subagent-no-bash-blocked-on-pr-diff`
  (the general tool-gap mechanism). In an UNATTENDED overnight review panel, a
  reviewer that couldn't see the code reads as a CLEAN one — so a real bug ships
  by morning. The usual cause: code-review subagents (feature-dev:code-reviewer,
  voltagent-*, Explore) are frequently provisioned WITHOUT a Bash tool, so when
  prompted to "review PR #N, fetch the diff with gh pr diff" they return a
  BLOCKED report (no review performed), or silently review the current checkout
  (often `main`, which predates the PR). Use when: (1) an overnight review panel
  (e.g. `overnight-multi-issue-implementation` Phase C, or any
  `agent-review-panel` run) dispatches reviewers against GitHub PRs or branches
  not checked out in the working tree; (2) a reviewer returns "I have no
  shell/gh/git tool" or "the PR sources are not in the working tree"; (3) one
  reviewer in a parallel panel comes back BLOCKED while siblings succeeded. Fix:
  pre-generate per-base diffs to files, materialize PR branches as worktrees,
  hand each reviewer explicit paths, and in the morning synthesis treat BLOCKED
  as not-clean (re-dispatch before counting the vote). Sibling to
  `voltagent-reviewer-no-write-tool` (Write gap → inline output) and
  `stacked-pr-review-per-base-diff-and-attach`.
author: Claude Code
version: 1.0.1
date: 2026-08-09
---

# Overnight review panel: a BLOCKED reviewer reads as CLEAN by morning

## Contents

- [Problem](#problem)
- [Context and trigger conditions](#context--trigger-conditions)
- [Root cause](#root-cause)
- [Solution](#solution)
- [Verification](#verification)
- [Example](#example)
- [Notes and references](#notes)

> **Overnight specialization.** The bare mechanism — a code-review subagent with
> no `Bash` tool can't `gh pr diff`/checkout a PR, so it returns BLOCKED — is the
> general skill `code-reviewer-subagent-no-bash-blocked-on-pr-diff`, applicable to
> any PR review. **This** skill is the overnight-specific consequence: in an
> unattended run there's no human to notice the BLOCKED report, so it silently
> counts as a passing reviewer and the bug ships.

## Problem

Every workflow in this repo leans on a **multi-agent review panel** to catch
errors before they reach the client (core pattern #1). The panel silently assumes
each reviewer can *see the code under review*. It can't always.

You dispatch a review subagent (e.g. `feature-dev:code-reviewer`, a `voltagent-*`
reviewer, or `Explore`) with a prompt like "use `gh pr diff <N>` to get each PR's
diff, then review." The agent returns a **BLOCKED report** — no findings —
explaining it has no shell/`gh`/`git` tool and the PR source isn't in the working
tree. Meanwhile a sibling reviewer in the same panel may succeed (if it happened to
find a materialized worktree on disk).

In an interactive session you'd notice. **In an unattended overnight run, a BLOCKED
report that you skim at 8am reads as "no findings = clean"** — and the bug it never
looked at ships to the client. This is exactly the failure class this repo exists to
prevent ("factual errors ship because a single tired reviewer missed it"), but
sourced from a tool gap rather than fatigue.

## Context / Trigger Conditions

- An overnight panel (`overnight-multi-issue-implementation` Phase C; any
  `agent-review-panel` fan-out) dispatches reviewers against a **GitHub PR or a
  branch not checked out in the main working tree**.
- A reviewer's report says any of: "No shell / `gh` / `git` tool is exposed",
  "The PR sources are not in the working tree", "WebFetch returns 404 (private
  repo)", or it reviewed files on the **current branch (often `main`, which
  predates the PR)** instead of the PR's changes.
- **Asymmetric panel outcomes** — some reviewers found the code, one came back
  blocked — because the successful ones discovered an existing worktree and the
  blocked one looked in the main checkout.

## Root cause

These agents' toolsets exclude `Bash`. For example `feature-dev:code-reviewer` has
`Glob, Grep, LS, Read, NotebookRead, WebFetch, TodoWrite, WebSearch, KillShell,
BashOutput` — note **`KillShell`/`BashOutput` are present but `Bash` itself is
not**, so the agent can read a background shell's output but cannot *start* a
command. No `Bash` → no `gh pr diff`, no `git checkout <pr-branch>`, no `git diff`.
`Read` hits the filesystem directly, so the agent only sees whatever branch is
checked out in the working tree it lands in (the main checkout, which predates the
PR). Private repos also defeat the `WebFetch` fallback (404).

## Solution

Do the git/`gh` work in the **orchestrator** (which has Bash) and hand each reviewer
**materialized source + pre-generated diffs** — never "run gh":

1. **Materialize each PR branch as a worktree** (or reuse existing ones):
   ```bash
   git worktree add /tmp/pr-<N> <pr-branch>
   ```
2. **Pre-generate each PR's diff against its own base** to a file. For a stacked PR,
   diff against its *own* base (not main) so the diff is scoped to that PR only:
   ```bash
   mkdir -p /tmp/diffs
   git diff <base-branch>..<pr-branch> -- app/ tests/ > /tmp/diffs/pr<N>.diff
   ```
3. **Prompt each reviewer with explicit paths + a no-Bash preamble**:
   > "You do NOT have a Bash/git/gh tool — only Read/Grep/Glob. The code is
   > materialized at `/tmp/pr-<N>/`; the scoped diff is at `/tmp/diffs/pr<N>.diff`.
   > Read the diff first, then read surrounding source in the worktree. Do NOT look
   > in the main repo checkout — it predates this work."
4. **For anything a reviewer wants to confirm empirically** (a probe, the test
   suite), have it return a `WANTS EMPIRICAL CONFIRMATION` list with the exact
   command; the orchestrator runs those afterward and folds results back in.
5. **In the morning synthesis, treat BLOCKED as not-clean.** A reviewer that
   couldn't see the code is not a passing vote — re-dispatch it (now with paths)
   before declaring the PR reviewed. A panel's "no findings" is only valid if every
   member actually read the diff.

## Verification

Each reviewer returns findings citing real `file:line` from the worktree, not a
BLOCKED report and not a review of unrelated `main` code. In a panel, all reviewers
produce symmetric, source-grounded output. The morning summary distinguishes
"reviewed, clean" from "could not review."

## Example

Stacked-PR tail review (#62/#63/#64) in a GA/GTM audit project: the "Correctness
Hawk" (`feature-dev:code-reviewer`) came back BLOCKED — it looked in the `main`
checkout (which predated the stack) and had no Bash to `git checkout` the branches.
Two siblings happened to find pre-existing worktrees and reviewed fine. Re-dispatched
the Hawk with the three per-base diffs pre-written to `/tmp/s13-diffs/` plus the
worktree paths and a "you have no Bash" preamble — it then found a **P0** (a
transactional version-check that broke on SDK retry, a data-loss bug 179 green tests
had missed). Had this been an unattended overnight run, the first BLOCKED report
would have read as a clean reviewer and the P0 would have shipped.

## Notes

- **BLOCKED ≠ CLEAN** is the load-bearing rule. The cost of an unread reviewer
  masquerading as a passing one is exactly what the panel exists to prevent.
- Some reviewers are also missing `Write` — see `voltagent-reviewer-no-write-tool`
  (orchestrator persists their inline output to files). A reviewer can hit both gaps
  at once: no Bash to fetch the diff *and* no Write to save the report.
- The general-purpose / default `Agent` DOES have Bash; this gap is specific to the
  read-only review/search specialist agents. If you need the reviewer to run
  commands, either pick a Bash-capable agent type or pre-stage everything.
- For scoping stacked-PR diffs per base and attaching reviews back to the right PR,
  see `stacked-pr-review-per-base-diff-and-attach`.

## References

- General version: `code-reviewer-subagent-no-bash-blocked-on-pr-diff` — the bare
  no-Bash → BLOCKED tool-gap mechanism, applicable to any PR review (this skill is
  its overnight specialization: the BLOCKED-reads-as-CLEAN consequence under autonomy)
- `stacked-pr-review-per-base-diff-and-attach` — per-base diff scoping for stacked PRs
- `subagent-bash-cd-wrong-worktree` — Bash-capable agents landing in the wrong worktree
- Empirically observed 2026-05-29 in a GA/GTM audit project's stacked-PR review panel
