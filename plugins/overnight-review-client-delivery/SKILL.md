---
name: overnight-review-client-delivery
description: |
  Run an overnight autonomous work session that produces a client delivery package the next morning,
  with a multi-agent review panel built in to catch factual errors before they reach the client. Use
  when: (1) you have client deliverables that need polishing/regenerating before a morning hand-off,
  (2) you want a thorough quality gate without blocking on synchronous review, (3) the work scope is
  bounded enough to fit in 1-3 hours of agent time, (4) you want to maximise agent throughput by
  running content work and review agents in parallel. Implements the Phase A (content work in
  foreground) / Phase B (8-agent review panel in parallel background) / Phase C (morning synthesis +
  user hand-off) structure validated on the a causal-impact project, including
  the locked-file escape hatch discipline for surgical P0 fixes, the "regenerate don't banner"
  rule for stale content refreshes, and parallel-agent branch hygiene to avoid silent commit
  drops. NOT for: synchronous code review (use ce:review or claude-code-guide instead), single-task
  overnight automation (use scheduled-tasks or cron), or work that requires user input mid-stream.
author: wan-huiyan + Claude Code (extracted from a causal-impact project)
version: 1.0.0
date: 2026-04-08
---

# Overnight Review Panel + Client Delivery

## Problem

Client deliverables (slide decks, reports, methodology notes, Google Doc updates) are usually written by an author who is too close to the work to spot factual errors, terminology slips, and miscommunication risks. Synchronous review is slow and blocks the author. Most overnight automation runs a single agent on a single task, missing the "many eyes catch what one misses" advantage. And when authors do polish deliverables overnight, they tend to add archive banners + partial header refreshes to stale content rather than regenerating it cleanly, which leaves readers confused about what's current vs historical.

This skill structures an overnight autonomous session as **three phases running mostly in parallel**:
1. **Phase A (content work, foreground)** — Author the actual deliverables
2. **Phase B (review panel, parallel background)** — Launch 8 specialised review agents simultaneously to audit code, methodology, numbers, ops, tests, architecture, and compliance
3. **Phase C (morning hand-off, user-gated)** — Synthesize findings, fix P0 errors before the user wakes up, prepare a clean morning summary with actionable next steps

The key insight: Phase A and Phase B run **concurrently**, not sequentially. The review agents are launched in a single message with multiple `Agent` tool calls (parallel batch), they run in the background while Phase A content work continues, and their findings land in `docs/reviews/` over the course of the overnight window. By the time the user wakes up, both the deliverables and the review panel reports are ready.

## Context / Trigger Conditions

Use this skill when ANY of the following apply:

- You have client deliverables that need to be polished/regenerated overnight before a morning hand-off (typical: marketing campaign reports, scientific findings docs, audit reports, executive summaries)
- The user wants a thorough quality gate but can't sit through a synchronous review session
- The work scope is bounded enough to fit in 1-3 hours of agent time
- You want to maximise agent throughput by running content work and review agents in parallel
- The repo has multiple specialised review agents available (`code-reviewer`, `architect-reviewer`, `data-scientist`, `data-analyst`, `qa-expert`, `sre-engineer`, `lit-researcher`, `compliance-auditor`, etc.)
- The deliverables exist as files in the repo (not just as a chat conversation), so review agents can grep + read them
- You can express the canonical source-of-truth numbers (e.g. "expected BQ row counts", "expected statistical values") so the data-analyst agent has something to verify against

Do NOT use this skill when:
- The work requires user input mid-stream (use a synchronous session instead)
- The scope is too small for a review panel (single-typo fix, one-line clarification)
- The deliverables don't exist yet (need to write them first, then review)
- The repo has no clear "what counts as canonical" definition (review agents can't verify against floating ground truth)

## Solution

### Phase A — Content work (foreground, ~1-2 hours)

Execute the actual deliverable polishing. Common tasks:

1. **Refresh stale HTML/markdown reports with current data**
2. **Apply small targeted edits to existing files** (P0 fixes, wording adjustments, footnote additions)
3. **Write new handoff documents for follow-up sessions**
4. **Move/archive files that have been superseded**
5. **Investigation memos for known open questions** (read-only investigation, no code changes)

#### **Critical rule for stale file refresh: REGENERATE, don't BANNER + PARTIAL UPDATE**

When refreshing a stale client-facing file, the temptation is to add an "archive banner" at the top + update the headline numbers in place + leave the rest as a historical snapshot. **This is wrong** for client-facing deliverables. The hybrid approach:

- Confuses readers: they can't tell which parts are current vs which are historical
- Leaves stale charts and tables with old numbers in the body, undermining the headline refresh
- Creates ambiguity when the user asks "is this current?" — it's neither fully current nor fully archived
- Fails the "would you stake your reputation on this analysis?" test

The correct pattern is **archive-and-regenerate**:

1. **Archive the current version** to `deliverables/archive/<name>_<context>.html` (e.g. `_session41_hybrid.html`, `_2026_04_02_pre_fix.html`) preserving it as a historical snapshot
2. **Regenerate the active version** at `deliverables/<name>.html` from the current source-of-truth data (BQ query, JSON audit file, etc.)
3. **No banners on the new version** — it's current, no banner needed
4. **Banners are appropriate ONLY when the file is being moved to archive permanently** — e.g. session-vintage reports that are kept for the audit trail but should not be referenced as current

This rule was learned the hard way on the prior project: session 41 used the hybrid approach (banner + partial header refresh), then session 44 had to redo it as proper archive-and-regenerate because the user explicitly asked for the cleaner version. **Skip session 41's mistake — go straight to archive-and-regenerate.**

When the file is regenerated from a script (e.g. `aggregate_sca_results.py` → `sca_report.html`), make sure the script reads from the current source of truth, not a hardcoded historical config. Sessions that reuse stale generators silently produce stale output even with a "fresh regeneration" label.

### Phase B — Multi-agent review panel (parallel background, ~1-3 hours)

**Launch the agents BEFORE starting Phase A content work** so they run concurrently. Use a single message with multiple `Agent` tool calls (parallel batch). Critical: don't launch them sequentially — the whole point is parallelism.

#### Default 8-agent panel composition

| Agent | Slice | Output file |
|---|---|---|
| code-reviewer | Production code (Python, JS, etc.) | `docs/reviews/code_reviewer_<scope>.md` |
| architect-reviewer | Full project structure + architectural coherence | `docs/reviews/architect_reviewer_full.md` |
| data-scientist | Methodology + narrative consistency in deliverables | `docs/reviews/data_scientist_deliverables.md` |
| data-analyst | Number tracing — every numerical claim verified against canonical source | `docs/reviews/data_analyst_numbers.md` |
| qa-expert | Test coverage gaps + script quality | `docs/reviews/qa_expert_tests_scripts.md` |
| sre-engineer | Operational robustness, idempotency, cost control | `docs/reviews/sre_engineer_ops.md` |
| lit-researcher | Methodology framing vs cited literature (verifies citations + finds missing references) | `docs/reviews/lit_researcher_methodology.md` |
| compliance-auditor | Data provenance, client trust, audit-trail integrity | `docs/reviews/compliance_auditor.md` |

For more complex projects, add **deep research agents** as a Phase B.3 batch (4 more agents) covering literature surveys for the methodology framing, post-fix decomposition validity, training-length confound, etc. These are lower priority than the primary 8 — they're nice-to-have reference material rather than blocking findings.

#### Scoping doc — write this BEFORE launching the agents

Before launching any review agent, write a `docs/reviews/session_NN_scoping.md` file that contains:
- Project background (what the deliverables are about)
- File scope per agent (which files each agent should read)
- **Canonical numbers table** — every key statistic, count, fingerprint, BQ row reference that the data-analyst agent should verify against
- Severity rubric (P0 BLOCKING / P1 IMPORTANT / P2 NICE-TO-HAVE)
- Out-of-scope items (e.g. "no webapp/ edits overnight, no Cloud Run jobs > $0.50")

Pass this scoping doc as the FIRST file each agent should read. It anchors all 8 agents on the same source of truth and makes their findings reconcilable.

#### Locked-file escape hatch (CRITICAL)

In a typical overnight session, certain files are LOCKED (e.g. the 3 client delivery files going to the client tomorrow morning). The default rule is: agents and the foreground session do not modify locked files.

But the **whole point** of running a review panel is to catch P0 errors in those locked files before they reach the client. So the discipline is:

**The locked-file escape hatch** — modify a locked file IF AND ONLY IF all four conditions hold:

1. **The prompt has an explicit escape hatch** (don't invent one — the user must have authorised "fix P0 BLOCKING errors found by review")
2. **The finding is backed by independent verification** (e.g. a BQ query showing the wrong number, OR a second review agent confirming the same finding)
3. **The fix is surgical** — only the wrong bit, not the surrounding narrative; no rewrites, no silent additions
4. **The morning summary documents the fix prominently** with the reviewer's reasoning inline, the diff, and the verification evidence

If any condition fails, document the finding in the morning summary as "REQUIRES USER DECISION" and let the user fix it after waking up.

**Real example from sessions 41-44:** the data-scientist review agent found 2 P0 BLOCKING errors in the locked client-delivery files (BSTS VI "CI excludes zero" was factually wrong; decomposition table "already correct" was wrong). Both fixes were applied surgically in commit `bae28e9`, both were BQ-verified, both were documented prominently in the morning summary. The user reviewed and approved the next morning. Without the review panel, both errors would have shipped to the client.

### Phase C — Morning synthesis + hand-off (user-gated, ~30 min)

When the user wakes up, they should find:

#### 1. A morning summary document (`docs/handoffs/session_NN_morning_summary.md`)

Lightweight, scannable, tells them everything they need to decide what to do first. Structure:

```markdown
# Session NN Morning Summary

**Branch:** ...
**Cost:** ... (must be under cap)
**Critical first thing to read:** ...

## 1. The one thing that needs your attention NOW
[If a P0 fix was applied to a locked file, FLAG IT HERE with the diff and rationale]

## 2. What got done overnight
[Phase A summary with commit refs]

## 3. Phase B review panel findings
[Severity-ranked list with file pointers]

## 4. Outstanding items (ordered by priority)
1. Must do before hand-off
2. Should do
3. Could do
4. Blocked

## 5. Cost tally
[Actual vs cap]

## 6. Sanity check queries
[5-10 BQ/SQL queries the user can run to verify the work before signing off]

## 7. Signs that something went wrong
[What to look for in commit history, branch state, tests]
```

#### 2. A review synthesis document (`docs/reviews/session_NN_synthesis.md`)

Consolidates findings from all 8 review agents into a single ranked action list:

- **Tier 1**: Must fix before any further client-facing work
- **Tier 2**: Must fix before next deployment
- **Tier 3**: Architectural clean-up (next session)
- **Tier 4**: Refactor (later sessions)
- **Tier 5**: Documentation

For each finding: severity (P0/P1/P2), source agent, file:line reference, recommended action.

#### 3. A clean session handoff PR

The branch should be pushed to origin with a PR opened. The PR body should summarise the work, list the agents that ran, point at the morning summary, and explicitly call out any locked-file edits that need user approval before merging.

## Verification

Verify the overnight session worked correctly by checking:

1. **All agents completed.** `ls docs/reviews/ | wc -l` should match the number of agents launched.
2. **Morning summary exists.** `ls docs/handoffs/session_NN_morning_summary.md`
3. **Synthesis document exists.** `ls docs/reviews/session_NN_synthesis.md`
4. **PR is open + mergeable.** `gh pr view <N> --json state,mergeable,mergeStateStatus` returns `OPEN / MERGEABLE / CLEAN`
5. **Cost is under cap.** Check `gcloud billing` or scratch notes for actual spend.
6. **All locked-file edits are documented in the morning summary.** Grep `git log --oneline --all` for locked-file edit commits and cross-check against §1 of the morning summary.
7. **Branch state is clean.** `git status` shows no uncommitted changes; `git log <branch> ^origin/main` shows the expected commits.

## Example

A complete overnight session structure (from sessions 41-44):

```
Session 41 overnight (~5 hours wall clock, ~1.5 hours of agent active time):

Phase A (content work, foreground):
  A.1 Merge PR #54 (squash, fast-forward, 5 min)
  A.2 Refresh 4 stale HTMLs with post-fix BQ data (60 min)
       NOTE: session 41 did this as banner+partial-update which user later asked to redo cleanly.
       For new sessions, go straight to archive-and-regenerate.
  A.3 Resolve Spec 359 effect estimate placeholder (10 min)
  A.4 Move generate_summary_docx.py from deliverables/ to scripts/ (5 min)
  A.5 Write 2 investigation memos (sign-flip + CausalPy) (30 min)

Phase B (review panel, parallel background, launched BEFORE A.1):
  B.1 Setup scoping doc (10 min before Phase A starts)
  B.2 Launch 8 review agents in single batch:
       - code-reviewer/webapp     -> code_reviewer_webapp.md
       - architect-reviewer/full  -> architect_reviewer_full.md
       - data-scientist/deliverables -> data_scientist_deliverables.md  ← caught 2 P0 errors
       - data-analyst/numbers     -> data_analyst_numbers.md
       - qa-expert/tests+scripts  -> qa_expert_tests_scripts.md
       - sre-engineer/ops         -> sre_engineer_ops.md
       - lit-researcher/methodology -> lit_researcher_methodology.md
       - compliance-auditor       -> compliance_auditor.md
  B.3 Launch 4 deep-research agents (later, lower priority)
  B.4 Synthesize findings -> session_41_synthesis.md

Phase C (morning, user-gated):
  C.1 Morning summary -> session_41_morning_summary.md
       Top of the file: "P0 fixes applied to locked client-delivery files,
       backed by data-scientist review agent + BQ verification, see §2"
  C.2 Open PR #55 with full session 41 work
  C.3 User wakes up, reads morning summary, runs sanity-check queries,
      reviews P0 fix diff, approves merge

Cost: £0 Cloud Run + £0 Cloud Build (no jobs needed for the review panel itself)
Outcome: 2 P0 BLOCKING factual errors caught and fixed before they reached the client.
```

## Notes

### Branch hygiene for parallel agents

**Critical gotcha:** when running parallel Claude sessions on the same git repo, your in-progress commits can be silently dropped by another session's rebase clean-up. Defensive practices:

- **Use unique branch names per session** — `feature/session-NN-claude-A` vs `feature/session-NN-claude-B`. Don't share branches.
- **Push immediately after every commit** so origin has a copy that survives any local rebase.
- **Treat `git log feature/branch ^origin/main` as the source of truth after a sync** — if commits you remember making don't appear there, check `git reflog HEAD@{N}` immediately. The reflog is the safety net.
- **In a multi-agent overnight setup, give each agent its own branch and merge serially in the morning** rather than letting them collaborate on a single branch.

Real example: in the session 41-44 sequence, my jargon-reduction commit `d7c39b0` was silently dropped by a parallel session 43 rebase. Recovered via `git reflog HEAD@{17}` then re-applied as a clean commit on a fresh branch. If I hadn't checked the reflog, the work would have been lost.

### Cost cap enforcement

Always set an explicit cost cap in the prompt (e.g. "£2 Cloud Run + £1 Cloud Build, fail-fast if exceeded"). The review panel itself is free (just agent token costs), but Phase A content work can include Cloud Run jobs, BQ queries, Docker rebuilds, etc. Without a cap, an unattended session can run up significant cost.

**Recommended caps for an overnight session:**
- **Aggressive (recommended):** £0 Cloud Run, £0 Cloud Build, all work is BQ queries + local file edits
- **Moderate:** £2 Cloud Run, £1 Cloud Build, allow small verification jobs
- **Liberal:** £10 Cloud Run, £5 Cloud Build, allow re-runs of moderate batch experiments

The session 41 used the aggressive cap and stayed at £0 for the entire overnight window — review panel + content work entirely on existing BQ data + local file regeneration. Validated that the aggressive cap is achievable for most overnight client-delivery sessions.

### When to skip the review panel

If the work is simple enough that a review panel is overkill (single-line fix, one-typo correction, no client deliverables involved), skip the panel and just do the work directly. The review panel pattern is for **client-facing work** with stakes high enough to warrant multi-agent quality gates. Don't overuse it.

### When to have MORE than 8 agents

For very complex sessions (e.g. a multi-deliverable research paper draft), you can extend the panel with:
- A second `data-analyst` agent for cross-validating numerical claims against multiple sources
- Domain-specific review agents (e.g. a `causal-inference-expert` agent for methodology depth)
- A `client-trust-evaluator` agent specifically focused on whether the deliverables would survive a careful client read

Cap at 12 agents — beyond that, the synthesis cost outweighs the additional findings.

### Anti-patterns to avoid

- **Sequential agent launch.** Don't `Agent` -> wait -> `Agent` -> wait. Use a single message with multiple parallel `Agent` calls. The whole point is concurrency.
- **No scoping doc.** Without a canonical-numbers table, the data-analyst agent can't verify anything and the other agents drift on definitions.
- **Banner-and-partial-update for stale files.** See the "REGENERATE, don't BANNER" rule above. This is the single biggest mistake authors make when "refreshing" deliverables.
- **Locked-file edits without the escape hatch discipline.** If you fix something in a locked file, you MUST document it prominently or the user loses trust.
- **Forgetting to launch agents BEFORE Phase A.** If you launch them after Phase A starts, you lose the parallelism benefit and the overnight wall-clock time blows up.
- **No cost cap.** An unattended Cloud Run job can run up real money fast.
- **Sharing a branch between parallel sessions.** Use unique branch names per agent.

## See also

- **`session-handoff`** — companion skill for end-of-session knowledge capture. Use this skill DURING the overnight session; use `session-handoff` AT THE END to write the morning summary doc.
- **`agent-review-panel-workspace`** — reference workspace with iteration outputs from earlier review panel runs (useful for understanding how synthesis findings should look).
- **`plan-review-integrator`** — sister skill for integrating review panel findings into a plan document. Use after Phase C synthesis if the findings need to feed into a follow-up implementation plan.
- **`claudeception`** — meta-skill for extracting reusable knowledge from work sessions. Use after the overnight session completes to capture any new patterns into project lessons.

## References

- prior session morning summary: `docs/handoffs/session_41_morning_summary.md` (the canonical example)
- prior session review synthesis: `docs/reviews/session_41_synthesis.md`
- prior session handoff: `docs/handoffs/session_44_handoff.md` (which captured the "stale file refresh = archive-and-regenerate, not banner-and-partial" lesson the morning after)
- Project lessons #211 (run review panel BEFORE client delivery), #212 (locked-file escape hatch), #218 (parallel session commit-dropping defense) in `~/.claude/projects/<project>/memory/lessons.md`
