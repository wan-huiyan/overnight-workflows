---
name: overnight-multi-issue-implementation
description: |
  Run an overnight autonomous workflow that takes a cluster of related GitHub
  issues (typically a P1 review-panel finding set) and ships them to merged
  PRs by morning. Use when: (1) the user wants 6-15 related issues closed in
  one autonomous run, (2) the issues split naturally into two PRs (e.g.,
  hardening + features, or refactor + new-functionality), (3) the user is
  going to sleep and won't be available to merge PR1 between phases, (4) each
  issue has clear acceptance criteria so each task can be implemented +
  tested + reviewed independently. Specializes `superpowers:subagent-driven-
  development` for the "issues -> stacked PRs by morning" problem shape:
  stacks PR2 on PR1's branch (so PR2 doesn't wait for human PR1-merge mid-
  night), audits tracker IDs against main before claiming (concurrent
  sessions steal ids), runs final code-review subagent before proposing
  merge, and surfaces important findings as PR comments before squashing
  (so review trail survives). Sister plugin to `overnight-review-client-
  delivery` (deliverable polishing) and `overnight-insight-discovery`
  (ah-ha pattern surfacing) — different problem shape, same overnight-
  autonomous philosophy + multi-agent review-panel discipline. ALWAYS use
  this skill when the user says "implement these issues overnight",
  "ship #N–#M autonomously", "wake up to merged PRs", "two-PR overnight
  plan", or wants a stacked-PR autonomous run from an issue cluster.
  NOT for: synchronous single-PR work (use plain `subagent-driven-
  development`), polishing an existing deliverable (use `overnight-review-
  client-delivery`), or generating insights from data (use `overnight-
  insight-discovery`).
author: wan-huiyan + Claude Code
version: 1.0.0
date: 2026-05-08
---

# Overnight Multi-Issue Implementation

## Overview

An overnight autonomous workflow specialized for the "cluster of GitHub issues →
stacked PRs by morning" problem. Builds on `superpowers:subagent-driven-
development` with overnight-specific discipline: stacked PRs (so PR2 doesn't
wait on a human PR1-merge), pre-flight tracker-id audit (concurrent sessions
on main steal your IDs), final PR-level code review (not just per-task), and
review-finding preservation as PR comments before squash.

Sister to `overnight-review-client-delivery` (polishes existing deliverables)
and `overnight-insight-discovery` (generates insights from data). Different
problem shape, same overnight-autonomous philosophy.

## When to use

All of these conditions:
1. **Input**: 6-15 related GitHub issues, typically a P1 review-panel cluster.
2. **Output**: 2 stacked PRs (occasionally 1 large PR or 3 — see "PR shape").
3. **Human availability**: user is going to sleep / away for 6-10 hours; not
   available to merge PR1 between phases.
4. **Issue quality**: each issue has clear acceptance criteria (so per-issue
   tasks are implementable + testable independently).
5. **Codebase familiarity**: agent has implementation context (CLAUDE.md,
   existing tests, conventions) — this isn't for greenfield bootstrapping.

NOT for:
- Synchronous single-PR work → use plain `superpowers:subagent-driven-development`
- Polishing an existing deliverable → use `overnight-review-client-delivery`
- Generating insights from data → use `overnight-insight-discovery`
- One-shot experiments without merge intent → just iterate

## Phases

```dot
digraph overnight {
    "Brainstorm + design (1 PR? 2? 3?)" [shape=box];
    "Write impl plan (12-task TDD)" [shape=box];
    "Pre-flight: audit tracker IDs on main" [shape=box];
    "Phase A: PR1 tasks (subagent-driven)" [shape=box];
    "Open PR1 (base=main)" [shape=box];
    "Branch off for PR2 stack" [shape=box];
    "Phase B: PR2 tasks (subagent-driven)" [shape=box];
    "Open PR2 (base=PR1-branch)" [shape=box];
    "Phase C: morning hand-off" [shape=box];
    "Final code-review subagent on PR1+PR2" [shape=box];
    "User merges (or instructs to merge)" [shape=box];
    "Cleanup + handoff doc" [shape=doublecircle];

    "Brainstorm + design (1 PR? 2? 3?)" -> "Write impl plan (12-task TDD)";
    "Write impl plan (12-task TDD)" -> "Pre-flight: audit tracker IDs on main";
    "Pre-flight: audit tracker IDs on main" -> "Phase A: PR1 tasks (subagent-driven)";
    "Phase A: PR1 tasks (subagent-driven)" -> "Open PR1 (base=main)";
    "Open PR1 (base=main)" -> "Branch off for PR2 stack";
    "Branch off for PR2 stack" -> "Phase B: PR2 tasks (subagent-driven)";
    "Phase B: PR2 tasks (subagent-driven)" -> "Open PR2 (base=PR1-branch)";
    "Open PR2 (base=PR1-branch)" -> "Phase C: morning hand-off";
    "Phase C: morning hand-off" -> "Final code-review subagent on PR1+PR2";
    "Final code-review subagent on PR1+PR2" -> "User merges (or instructs to merge)";
    "User merges (or instructs to merge)" -> "Cleanup + handoff doc";
}
```

## Phase A/B execution discipline

Per task: **implementer subagent → spec-compliance reviewer → code-quality
reviewer → mark complete**. Standard `subagent-driven-development` protocol.

**Two pragmatic deviations** for overnight throughput:

1. **Combined spec+code review for low-risk tasks**: tasks that are pure
   plumbing (e.g., a 5-LOC slice fix, a tracker entry, a regen-and-push
   finalization) can use a single combined-review subagent instead of two
   sequential ones. The full 2-stage protocol stays for code-bearing tasks
   that change behavior.

2. **Light-touch reviews on finalization tasks**: Tasks "open PR1" and
   "open PR2" are themselves checkpoints that include tracker + site regen
   + push. These don't need a fresh reviewer subagent — the controller
   verifies inline (read git log, confirm PR is open, confirm tracker
   visible). The merge-time PR-level review (Phase C) is the real gate.

These deviations cost ~30% review-token budget and ~40% wall time vs strict
protocol. Costs accept-or-reject before starting; document the choice in
the implementation plan. If the cluster is high-stakes (security,
production data path), don't deviate — keep strict 2-stage on every task.

## Pre-flight: tracker-id audit

Multiple concurrent sessions on `main` will steal your category IDs. Before
claiming `cat7-7eX` (or whatever your project's tracker scheme is), scan
the latest `main`:

```bash
git fetch origin main
grep -oE 'cat7-7[a-z]+' <(git show origin/main:docs/generate_roadmap_backlog.py) \
    | sort -u | tail -5
# pick the next-available id; if working on a 2-PR run, reserve N AND N+1
```

If your overnight run is the only writer, this is a no-op. If you're running
in parallel with another session (common during P1 sweeps after a review
panel), the concurrent session WILL take your reserved IDs by the time you
reach Phase B's finalization. Resolve via the project's standard
PR-conflict skill (e.g., `pr-conflict-site-regen` for the
the project) — hand-union the generator + regenerate site.

## Stacked-PR strategy (load-bearing for overnight)

The user is asleep. PR1 will not merge before Phase B starts. Solution:

1. **PR1**: open with `--base main` from branch `<feature>` (where the agent's
   worktree lives).
2. **Branch off**: `git checkout -b <feature>-pr2` from the same HEAD.
3. **Phase B**: all PR2 tasks commit to `<feature>-pr2` (new branch).
4. **PR2**: open with `--base <feature>` (PR1's branch, NOT main).
   Use `gh pr create --base <feature>` explicitly.

GitHub renders PR2's diff as PR2's contents only (since PR1's diff is
already in the base branch). When PR1 squash-merges in the morning,
GitHub's standard behavior depends:

- **If user merges PR1 via the UI's merge button (which deletes the branch
  via the UI's button afterwards)** → GitHub auto-retargets PR2's base to
  `main`. PR2 may need a rebase or merge of `main` to resolve conflicts,
  but the PR stays open and reviewable.
- **If the user (or an agent) deletes PR1's branch via the API directly**
  (`gh api -X DELETE refs/heads/<branch>`) → PR2 is **silently auto-closed**
  and **cannot be reopened**. Recovery requires opening a fresh PR.
  See sister skill `stacked-pr-base-branch-deletion-auto-closes-dependent`
  for the trap details + recovery + prevention patterns.

For overnight unattended runs, **always use the UI merge button or
`gh pr merge --merge/--squash` (NOT `gh api -X DELETE` after merge)**.
If `gh pr merge --delete-branch` fails locally with the worktree-checkout-
trap, leave the branch undeleted overnight; user can clean up morning.
See sister skill `gh-pr-merge-worktree-checkout-trap`.

## Phase C: morning hand-off discipline

Before proposing merge to user:

1. **Final code-review subagent on the full PR diff** (not just per-task).
   Per-task reviews catch implementation bugs; PR-level review catches
   cross-task integration concerns. Use `voltagent-qa-sec:code-reviewer`
   or invoke `code-review:code-review` skill. Run PR1 + PR2 reviews in
   parallel (single message, multiple Agent calls).

2. **Surface findings as PR comments BEFORE merge**. Squash discards the
   in-branch commit messages; review-finding text only persists if posted
   as a comment on the PR (or filed as a follow-up issue). Use:
   ```bash
   gh pr comment <PR> --body "$(cat <<'EOF'
   ## Code-review findings (Approved with follow-ups)
   [...findings classified by severity...]
   EOF
   )"
   ```

3. **File follow-up issues for Important findings** (severity 2 of 3).
   Critical = block merge + fix in this PR; Important = file follow-up
   issue, OK to merge; Minor = one-line note in PR comment, no issue.
   Apply project's standard issue labels (e.g., `enhancement`, `security`,
   `bug`, `review-panel`).

   **Common harness gotcha**: `gh issue create` may be denied mid-batch
   under default-auto permissions (claimed as "external-system write the
   user didn't request"). Add an explicit allow rule to settings.json:
   ```json
   "permissions": {
     "defaultMode": "auto",
     "allow": ["Bash(gh issue:*)"]
   }
   ```
   The user must do this once; agent cannot self-grant.

4. **Ask before merging**. Merging is a hard-to-reverse, affects-shared-
   state action. Even with explicit "merge if review passes" instruction
   from the previous evening, confirm with `AskUserQuestion` once findings
   are surfaced. The 30-second confirmation cost is cheap compared to a
   "wait, that wasn't supposed to merge yet" recovery.

## Recovery patterns

### Subagent stall mid-execution

A subagent dispatched to handle a long-running task (e.g., a finalization
task that runs the full pytest suite + `gh pr create`) may pause waiting
for a Monitor event or long shell. Don't wait indefinitely. Take over
directly: read git log to see what landed, finish the remaining commands
inline. Don't re-dispatch — the partial state is harder to recover via
fresh subagent than via direct controller continuation.

### Conflict resolution mid-merge

When PR1 squash-merges and PR2's base no longer matches, follow project's
PR-conflict skill (e.g., `pr-conflict-site-regen`). Standard
recipe:
- Hand-union generator entries (don't pick a side; rename your IDs to
  the next available)
- Regenerate site from merged generator
- Commit the merge resolution
- Push

### Auto-closed dependent PR

If you accidentally trigger the stacked-PR delete trap, see
`stacked-pr-base-branch-deletion-auto-closes-dependent`. Open a fresh PR
with `--base main` from the same head branch; preserve review comments
by linking the closed PR's comment thread.

## Output (morning checklist)

By morning the user should have:

- [ ] PR1 open against `main`, all tests green, ready to merge
- [ ] PR2 open against PR1's branch (or main if PR1 already merged)
- [ ] Both PRs have review-finding comments (Critical/Important/Minor)
- [ ] Follow-up issues filed for Important findings (or queued in chat
      with exact `gh issue create` commands if harness denied)
- [ ] Tracker entries reserved (`cat7-7eX` and `cat7-7eY`) + site regenerated
- [ ] Session handoff doc at `docs/handoffs/session_NNN_handoff.md`
- [ ] MEMORY.md updated with one-line index entry per skill convention
- [ ] Any new project-feedback files saved under `memory/feedback_*.md`

## Anti-patterns

- **Don't** use `gh api -X DELETE refs/heads/<branch>` to clean up PR1's
  branch while PR2 is still open. Auto-closes PR2 irreversibly. See
  sister trap skill.
- **Don't** skip the pre-flight tracker-id audit. Two parallel sessions
  on the same day WILL collide; resolving at PR-merge time is more
  expensive than reserving IDs upfront.
- **Don't** start implementation before the design + plan are committed.
  Plan changes during execution are recoverable; but if a subagent
  context-corrupts mid-run, you need the plan on disk to resume.
- **Don't** swallow review findings in commit messages. Squash-merge
  drops them. Use PR comments + follow-up issues.
- **Don't** auto-merge after the final code review without asking.
  Overnight authorization to "implement" is not authorization to "merge"
  — explicit confirmation each time.
- **Don't** auto-deploy after merge. The user is asleep; even if your
  project has auto-deploy-on-merge wired, the deploy preflight (e.g.,
  `deploy-from-stale-worktree-silent-rollback`) needs human review for
  high-stakes changes.

## References / sister skills

- `superpowers:subagent-driven-development` — the per-task protocol this
  skill builds on (implementer + spec-reviewer + code-quality-reviewer
  per task).
- `superpowers:writing-plans` — produces the implementation plan that
  feeds into Phase A/B.
- `superpowers:brainstorming` — produces the design doc that feeds into
  the implementation plan.
- `overnight-review-client-delivery` — sister overnight pattern for
  polishing an existing deliverable.
- `overnight-insight-discovery` — sister overnight pattern for surfacing
  ah-ha findings from data.
- `gh-pr-merge-worktree-checkout-trap` — handles the "merge succeeded
  but local cleanup failed" gotcha.
- `stacked-pr-base-branch-deletion-auto-closes-dependent` — handles the
  trap of deleting a base branch while a stacked PR is still open.
- `pr-conflict-site-regen` (project-specific example) —
  conflict-resolution recipe for the project repo's site-regen pattern;
  template for "your project's PR-conflict skill" referenced above.

## Worked example — 2026-05-08 chatbox session

Issues #437–#442 (6 P1s from a chatbox-review panel). Brainstormed → 2-PR
shape decided (hardening + knowledge-gap). 12-task implementation plan
written + committed. Subagent-driven execution: 6 tasks for PR1 (#456),
6 tasks for PR2 (originally #461, recovered as #465 after stacked-PR
delete trap). Final reviews ran in parallel via
`voltagent-qa-sec:code-reviewer`. Both PRs merged with 5 follow-up issues
queued (1 filed, 4 blocked by harness pre-allow-rule — surfaced for user
to file from PR comments).

Total wall time: ~6 hours overnight + ~30 min morning hand-off.
Total subagent invocations: 12 implementers + ~24 reviewers + 2 final
PR-level reviews = ~38 dispatches.
Lessons fed back: 1 new global skill (`stacked-pr-base-branch-deletion-
auto-closes-dependent`), 1 new project feedback (always run code-reviewer
pass before merging PRs), this skill.
