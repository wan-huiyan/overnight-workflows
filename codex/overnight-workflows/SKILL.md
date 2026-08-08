---
name: overnight-workflows
description: Design and run durable unattended multi-stage work that can recover from agent failure, rate limits, stale branches, blocked reviewers, and partial completion. Use when the user asks for overnight work, unattended execution, work by morning, a long autonomous issue batch, a large mixed live queue, scheduled multi-track work, or a durable workflow that should continue without live supervision.
---

# Overnight Workflows

An overnight request requires persistence, not broader authority. Define allowed external actions before the run.

## Design the run

1. Fix the objective, time boundary, stop conditions, budgets, repositories, and separate grants for commit, push, PR, merge, deploy, network, paid calls, and owner-only decisions.
2. Enumerate the complete source queue, reconcile one parent record per source occurrence mechanically, put differently gated parts in child slices, and verify prompt claims against current code, decisions, artifacts, and remote state.
3. Split executable slices into reviewable units with durable IDs, explicit dependencies, write sets, review tiers, working budgets, latest starts, and autonomous stop points.
4. Use isolated worktrees for writers. Give shared central files to one integrator and serialize their integration.
5. Write a durable journal that keeps classification state, lifecycle state, blocker reason, and verification result separate, and records branch, commit, checks, review artifacts, and next action.
6. Make every stage idempotent so a resumed run can inspect and continue rather than repeat.
7. Reserve capacity for review, integration, and recovery instead of spending every slot on implementation.

## Run safely

- Validate agent access to the full artifact before accepting a clean review.
- Poll durable state, not sleep timers alone.
- On agent loss or a yielded command, inspect its tool session, process, log, worktree, and journal before retrying. Do not duplicate a still-running test, generation job, paid call, or merge.
- Re-check current remote state before every merge or deployment.
- Keep PASS, FAIL, BLOCKED, and UNCHECKED distinct.
- Commit all integrator-owned edits first, then review one immutable target SHA, merge-base SHA, head SHA, and deterministic diff digest. A later commit, rebase, target change, merge-base change, or digest change invalidates the affected verdict.
- Treat worktree and branch inventories as evidence, not permission to clean another session's state.
- Stop on ambiguous destructive action, missing authority, repeated invariant failure, or evidence that integration would overwrite newer work.

## Morning report

Report completed and incomplete units, commits and PRs actually observed, verification by layer, blocked or unchecked work, assumptions used, and the safest next action.

Read the relevant source workflow under `references/workflows/` for specialized patterns. For a live index or mixed backlog with stale, partial, owner-gated, or shared-file items, read `references/workflows/references/large-live-queue-orchestration.md` before classification or dispatch. For a related issue cluster or stacked PR sequence, read `references/workflows/overnight-multi-issue-implementation.md`. Translate Claude schedules, RemoteTrigger, model tiers, and workflow schemas into the goal, automation, wait, and agent facilities actually available.
