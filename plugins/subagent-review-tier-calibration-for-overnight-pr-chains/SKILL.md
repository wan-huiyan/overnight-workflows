---
name: subagent-review-tier-calibration-for-overnight-pr-chains
description: |
  Calibrate review intensity per-PR when orchestrating a long chain (10+ PRs)
  of independent tasks overnight from a single implementation plan. Use when:
  (1) running `superpowers:subagent-driven-development` against a plan that
  produces many independent PRs (not stacked PRs from an issue cluster — for
  that shape use `overnight-multi-issue-implementation`), (2) user has
  authorized auto-merge on green review, (3) the user is asleep / unavailable
  to merge between tasks, (4) the chain spans multiple risk tiers (some PRs
  rewrite views/handlers, others restyle CSS only). The skill prescribes
  three review tiers — full two-stage / combined single-agent / bash-only
  verification — and a decision rubric for picking the right tier per PR to
  maximize overnight throughput WITHOUT silently approving real bugs. Sister
  to `subagent-driven-development` (specializes its review step for
  throughput-tuned chains) and `overnight-multi-issue-implementation`
  (different problem shape — issues vs. plan tasks; stacked vs. independent).
  Symptom of having ignored this: orchestrator dispatches 14 × 2 = 28 review
  subagents in one session when 14 × 1 + 5 × 0 would have caught the same
  bugs in half the wall time.
author: Claude Code
version: 1.0.2
date: 2026-08-09
---

# Subagent Review-Tier Calibration for Overnight PR Chains

## Contents

- [Problem](#problem)
- [Context and trigger conditions](#context--trigger-conditions)
- [Solution](#solution)
- [Decision rubric](#decision-rubric-apply-per-pr-in-the-chain)
- [Verification](#verification)
- [Example](#example-s75-bold-editorial-redesign-real)
- [Notes and references](#notes)
- [Changelog](#changelog)

## Changelog

- **1.0.2 (2026-08-09):** adds the top contents list required by the routed
  Codex `WORKFLOW.md` edition. Plugin release: 1.0.1.

## Problem

`superpowers:subagent-driven-development` prescribes a strict two-stage review per task: spec compliance reviewer first, then code-quality reviewer. For a single feature or a 3-task plan, that's correct — quality dominates throughput.

For an **overnight chain of 10+ independent PRs** from one plan, blindly following two-stage means 20+ review subagent dispatches with sequential dependencies (next task gates on previous merge). Even when individual subagents take 5 minutes, the chain wall time balloons to 3-4 hours of review alone, and the orchestrator context burns through skill-list reminders.

The fix is **per-PR tier selection**, not blanket two-stage:
- **Tier 1 (two-stage, full)** — for PRs touching data contracts, handler architecture, or shared schemas
- **Tier 2 (combined single-agent)** — for moderate-risk PRs (new template + view + tests, well-scoped)
- **Tier 3 (bash-only verification)** — for low-risk PRs (pure visual restyle, no flow change, all tests green in implementer report)

Calibrate per-PR, not per-chain. Some chains run Tier 1 throughout (high-risk migration); others can use Tier 3 for the bulk and reserve Tier 1 for the 2-3 high-risk PRs.

## Context / Trigger Conditions

Apply this calibration when **all** of these hold:

1. You're orchestrating `superpowers:subagent-driven-development` against a plan with 10+ tasks
2. The user has authorized auto-merge on green review
3. Tasks are independent (each = its own PR; not stacked) — if stacked, use `overnight-multi-issue-implementation` instead
4. The chain risk profile varies (some PRs touch shared state, others are isolated restyles)
5. Orchestrator throughput matters (user is sleeping / context budget tight)

**Don't apply** when:
- Plan has <5 tasks (overhead exceeds savings)
- All tasks touch the same shared file (each tier-3 still risks regression on later tasks)
- User explicitly asked for "strict review every PR" (respect the instruction)

## Solution

### Tier 1 — Full two-stage review (`subagent-driven-development` default)

> **Standing convention (the heavyweight tier):** for any non-trivial PR, prefer
> the **`roundtable:agent-review-panel`** skill (all panel agents `model: opus`)
> over the two-stage single-reviewer pair — multiple independent opus reviewers
> catch what one misses. Tier 2 / Tier 3 (single-reviewer / bash-only) are for the
> trivial / low-risk PRs that "skip the full panel." Same threshold as below.

Use when:
- PR rewrites a view handler (request.form consumption, session-state shape)
- PR deletes a route or template (decommission blast radius)
- PR introduces a new data contract (new POST endpoint, new session key, new BQ schema)
- PR migrates auth or workspace boundaries
- PR is the FIRST of the chain (catches any plan-level misunderstanding)
- PR is the LAST of the chain (E2E + verification)

Dispatch: spec-reviewer subagent → fix loop → code-quality-reviewer subagent → fix loop → merge.

### Tier 2 — Combined single-agent review (most PRs)

Use when:
- PR is a new template + matching view + tests (e.g., new sub-page like `/retrieve/source`)
- PR is a visual transplant that ALSO changes a small amount of view logic (e.g., bumping `step=N`)
- PR's implementer self-report explicitly cites: tests green + baseline clean + smoke check passed
- The risk profile is "moderate" — not a hot path but not a one-line CSS swap

Dispatch: ONE reviewer subagent with combined spec + code-quality prompt → fix loop → merge.

**The combined prompt pattern**:
```
You are the combined spec + code-quality reviewer for [Task X]. Verify
BOTH:
  1. Spec compliance (acceptance criteria from plan)
  2. Code quality (P0/P1/P2 with examples per tier)
Return VERDICT: APPROVE | REQUEST_CHANGES | REJECT with categorized findings.
```

Why one agent ≠ two: the same opus reviewer reading the spec then the diff catches the same issues as two specialized reviewers, at half the cost. The two-stage advantage (clean re-review after spec-fix) only matters when spec-compliance findings frequently uncover cascading quality issues — rare for visual-restyle PRs.

### Tier 3 — Bash-only verification (low-risk PRs)

Use when:
- PR is a pure visual restyle (existing template, swap CSS classes, no markup restructure)
- Implementer self-report shows: all tests pass + baseline +N passes / 0 new failures + smoke check
- No `request.form` changes, no new routes, no template deletions

Verification recipe (run from orchestrator, no subagent):

```bash
# 1. Pull the branch
git fetch origin <branch-name> --quiet

# 2. Diff stat — sanity-check size
git diff origin/main origin/<branch-name> --stat | tail -10

# 3. Key safety markers (template restyle PRs)
#    NOTE: use -cE for portable alternation. BSD grep (default on macOS) does
#    NOT honor `\|` as BRE alternation — it matches the literal string.
git show origin/<branch-name>:webapp/templates/<file> | grep -cE "url_for|<existing-form-field>"
git show origin/<branch-name>:webapp/templates/<file> | grep -c 'onclick="history.back()"'
# Expected: form-field count preserved (or higher); onclick count = 0

# 4. Inline style size (per spec — keep under 200-line cap)
git show origin/<branch-name>:webapp/templates/<file> | awk '/{% block extra_css %}/,/{% endblock %}/' | wc -l

# 5. (Optional) confirm PR body documents any intentional content drops
gh pr view <N> --json body -q .body | head -30
```

If all green: `gh pr merge <N> --squash --delete-branch` and proceed.

If anything red (form fields dropped silently, onclick reappeared, style block too large): drop to Tier 2 and dispatch a combined reviewer.

### Decision rubric (apply per PR in the chain)

| Signal | Tier |
|---|---|
| Touches view handler logic (def X():) beyond a `step=N` value | 1 |
| Touches `webapp/views/analysis.py` POST handler bodies | 1 |
| Deletes a route or template | 1 |
| Adds a new route + new template + new view + tests | 2 |
| Renames a route with 308 redirect (broad consumer audit needed) | 2 |
| Replaces existing template body, new tests, no view changes | 2 |
| Pure CSS swap on existing template, no markup restructure | 3 |
| Implementer self-reports DONE_WITH_CONCERNS | 2 (never 3) |
| Implementer self-reports DONE + smoke check + all tests pass | tier per other signals |
| First PR of the chain | 1 (always) |
| Last PR of the chain | 1 (always — final verification) |

### Documenting the tier choice

In each task's merge-commit message footer OR in the chain's orchestrator summary, note the tier chosen:

```
Review-tier: 2 (combined opus reviewer + bash verification)
Review-tier: 3 (bash verification only — pure visual restyle)
```

This makes the throughput-vs-quality tradeoff auditable post-hoc. If a bug ships, the post-mortem can ask "was this PR Tier 3 when it should have been Tier 2?"

## Verification

After the chain completes:

1. **Bug regression check** — run the full test suite at the final main commit. Compare to pre-chain baseline. Expected: equal failure count, equal-or-higher pass count.
2. **Post-merge audit** — `gh pr list --state merged --limit <chain-length>`. For any PR that reviewers (Tier 1/2) flagged P1 issues that weren't fixed pre-merge, surface them as follow-up issues in the final handoff.
3. **Tier distribution sanity check** — count tiers used. If 90%+ were Tier 3, you under-reviewed (a hot-path PR likely got missed). If 90%+ were Tier 1, you over-reviewed (calibration failed). Healthy distribution for a 14-PR plan: ~20% Tier 1 (entry + cleanup + critical migrations), ~60% Tier 2 (most new-feature work), ~20% Tier 3 (pure restyles).

## Example (S75 bold-editorial redesign, real)

14-task plan, 16 PRs total. Tier distribution actually applied:

| Tier | Tasks | Total |
|---|---|---|
| 1 (two-stage full) | None — combined review used for all (in retrospect, a calibration miss — see below) | 0 |
| 2 (combined single-agent) | A, B, C (P1 fix loop), L | 4 |
| 3 (bash-only) | D, E, G, H, I, J, K1, K2, K3+K4, F | 10 |
| Final (E2E sweep) | M | 1 (Tier 1-equivalent — full E2E coverage) |

Distribution: 0% / 29% / 71% (counting M as Tier 1) — well above the prescribed Tier-3 share (~20%) and missing the "first PR is always Tier 1" rule (Task A was Tier 2). This skill codifies the corrected target distribution and the absolute first/last rule; the S75 run is the negative example that prompted both.

Catch rate: 1 of 14 PRs returned REQUEST_CHANGES (Task B, two P1s — pre-consent semantics + missing calendar section). Tier-3 PRs that *should* have been Tier 2 in retrospect: Task J (dropped significant UX controls) — its size and structural change merited a combined reviewer. Tier-2 PRs that *should* have been Tier 1 in retrospect: Task A (first PR of the chain — the rule "first PR is always Tier 1" came out of this miss). Lesson: when implementer self-reports "significant content drops", upgrade tier even if the markup change feels mechanical.

This skill was authored partly as a result of those misclassifications.

## Notes

- **Tier choice is per-PR, not per-chain.** Don't pre-commit to "all Tier 3" — recalibrate at each task dispatch based on the implementer's STATUS report.
- **Implementer-self-reported DONE_WITH_CONCERNS = upgrade one tier minimum.** Concerns aren't trivial; they need an independent eye.
- **Fix subagent uses the SAME tier as the original.** If the original review was Tier 2, the fix subagent's output also gets a Tier-2 re-review, not a free pass.
- **API overload contingency**: if opus is sustained-overloaded (3+ 529s in a row), fall back to sonnet for Tier 3 verification subagents (Tier 1/2 still need opus). Document the model fallback in the orchestrator summary so post-hoc audit can spot quality issues attributable to model degradation.
- **The bash-verification recipes are project-specific.** The recipe above is for Flask + Jinja webapp restyle PRs. For a React/Next.js chain, the markers shift to `data-testid` preservation, `onClick`/`onKeyDown` paired-events, etc. Codify the recipe per-project in the chain's implementation plan.
- **First and last PR are always Tier 1.** First catches plan-level misunderstandings before they propagate; last is the E2E verification that exercises the whole chain. Don't shortcut either.

## References

- Sister skill: `superpowers:subagent-driven-development` (the master pattern; this skill specializes the review step)
- Sister skill: `overnight-multi-issue-implementation` (different problem: stacked PRs from issues)
- Related: `subagent-pre-existing-misattribution` (don't credit subagent for fixes that were already in main)
- Related: `subagent-reports-complete-but-pr-unmerged` (verify the orchestrator-side merge actually happened)
- Related: `large-redesign-parallel-branch-collision-audit` (pre-flight check before STARTING a chain)
