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
6. Track controller liveness, bounded execution leases, and repository-relative exact-path reservations in separate append-only records keyed by run and repository. Task transitions carry only their controller, lease, and reservation IDs; the latest operational record per ID is authoritative. A stopped controller is no longer live, but its active branch or pull request keeps its path reservation until verified release, abandonment, supersession, or named transfer.
7. Make every stage idempotent so a resumed run can inspect and continue rather than repeat.
8. Reserve capacity for review, integration, and recovery instead of spending every slot on implementation.

## Run safely

- Validate agent access to the full artifact before accepting a clean review.
- Poll durable state, not sleep timers alone.
- On agent loss or a yielded command, inspect its tool session, process, log, worktree, and journal before retrying. Do not duplicate a still-running test, generation job, paid call, or merge.
- Re-check current remote state before every merge or deployment.
- Keep PASS, FAIL, BLOCKED, and UNCHECKED distinct.
- Commit all integrator-owned edits first, then review one literal target ref and its dispatch SHA, immutable merge-base SHA, head SHA, tree SHA, literal deterministic diff argv array, and diff digest. For installed-package review, also freeze the complete evidence inventory, generation, source commit/tree, and manifest digest. A later commit, rebase, target change, merge-base change, or digest change invalidates the affected verdict.
- Treat worktree and branch inventories as evidence, not permission to clean another session's state.
- Stop on ambiguous destructive action, missing authority, repeated invariant failure, or evidence that integration would overwrite newer work.

## Morning report

Report completed and incomplete units, commits and PRs actually observed, verification by layer, blocked or unchecked work, assumptions used, and the safest next action.

## Route through this umbrella

Keep the seven packaged workflows as ordinary references. Select one route,
then read its `WORKFLOW.md` and only the relative resources that route names:

- large redesign with unmerged parallel branches → `references/workflows/large-redesign-parallel-branch-collision-audit/WORKFLOW.md`;
- new client-facing insight discovery from data → `references/workflows/overnight-insight-discovery/WORKFLOW.md`;
- related issue cluster, stacked pull requests, or large mixed live queue → `references/workflows/overnight-multi-issue-implementation/WORKFLOW.md`;
- polish an existing client deliverable → `references/workflows/overnight-review-client-delivery/WORKFLOW.md`;
- harden a panel whose reviewer could not read its input → `references/workflows/overnight-review-panel-blocked-reviewer-reads-as-clean/WORKFLOW.md`;
- scheduled fire-ASAP multi-track orchestration → `references/workflows/schedule-poll-orchestrator-pattern/WORKFLOW.md`;
- calibrate review tiers across a long independent pull-request chain → `references/workflows/subagent-review-tier-calibration-for-overnight-pr-chains/WORKFLOW.md`.

Each workflow's relative `references/`, `assets/`, and `scripts/` dependencies
live beside it. For a
live index or mixed backlog with stale, partial, owner-gated, or shared-file
items, also read
`references/workflows/overnight-multi-issue-implementation/references/large-live-queue-orchestration.md`
before classification or dispatch.

A request to summarize, explain, audit, or review a plan without executing it
does not enter an execution route. A route choice never grants commit, push,
pull-request, merge, deploy, network, paid-call, or external-write authority.
Translate Claude schedules, RemoteTrigger, model tiers, and workflow schemas
into the goal, automation, wait, and agent facilities actually available.

Before release, run `scripts/check_large_queue_guidance.py --self-test
--release-gate /path/to/codex`. This calls Codex 0.147.0 app-server
`skills/list(forceReload=true)` and retrieves every deterministic positive and
negative route contract through the loaded umbrella. An unavailable real loader
fails the local release gate. Implicit route selection by an unpinned model is
`UNCHECKED`; do not claim model-selection accuracy without a separately
authorized pinned-model evaluation.
