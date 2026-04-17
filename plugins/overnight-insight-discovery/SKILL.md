---
name: overnight-insight-discovery
description: |
  Run an overnight autonomous B-vs-C parallel insight-discovery workflow that surfaces
  ah-ha findings from data for a client. Use when: (1) a client wants interesting or
  surprising insights (not just monitoring or action items); (2) you want to hedge LLM
  creativity against deterministic rigor by running two tracks in parallel and
  consolidating; (3) the work fits an 8-hour autonomous window with review-panel gates;
  (4) the underlying data supports both exploratory querying and mechanical candidate
  scans. Specializes `overnight-review-client-delivery` for INSIGHT DISCOVERY rather
  than deliverable polishing — the two are sister skills with different problem shapes.
  ALWAYS use this skill when the user asks for "ah-ha insights", "surprise patterns",
  "funnel leaks", "hidden findings", "overnight analysis to surface X", or wants a
  dual-approach (creative + mechanical) for client-facing insight work — even if they
  don't explicitly name the pattern. NOT for: synchronous analysis (use exploratory-
  data-analysis), single-track LLM exploration (use deep-research), or work that needs
  user input mid-stream.
author: wan-huiyan + Claude Code
version: 1.3.0
date: 2026-04-17

# Changelog
# 1.3.0 (2026-04-17, S92 P0-2 + P1-3 — stats verification + chart divergence)
#   - Added stats-verification sub-phase to Phase B review loop. The
#     `data-scientist` persona now emits structured `stats_requests.jsonl`
#     entries (≤3/track/round) with `{claim_id, question, snippet_path,
#     bq_queries, expected_output_shape}`. Orchestrator runs each snippet
#     between Round N and Round N+1, captures stdout to `stats/<claim_id>.txt`,
#     and feeds results back to the integrator and round-N+1 panel via
#     `verified_stats.json`.
#   - Auto-promotes contradicting verification results to **P0 must-fix**
#     (brief's headline number gets overwritten before round N+1).
#   - Exit criterion 3 updated: a P1 marked `[VERIFIED]` via stats-verification
#     counts the same as one resolved via panel consensus.
#   - Added in direct response to the v1.0.1 retroactive-panel miss where
#     `data-scientist` flagged the missing cross-band significance test in
#     round 1, but the test itself only ran post-hoc — revealing the 4.4× →
#     3.17× overstatement only after the client-brief shipped.
#     See references/phase_b_review_loop.md §"Stats-verification sub-phase".
#   - Added chart-divergence check in Phase A. After each chart is saved,
#     compute `visual_prominence = |claim - mean(others)| / y_range` at the
#     claim's x-locus. If `< 0.15` AND `stats_significant`, flag and emit a
#     redesign alongside (log y-axis / delta-from-baseline / small multiples /
#     difference plot / forest plot of ratios+CIs). `chart_meta.json` records
#     flag + redesign path. Panel's `qa-expert` checks both images are cited
#     in the brief; `client-trust-evaluator` asks "obvious in 10 seconds?".
#     Added in response to Finding 1's linear-scale chart obscuring the
#     3.17× cliff — user's eye caught what no automated check did.
#     See references/chart_divergence_check.md.
#
# 1.2.0 (2026-04-17, post-v1.0.1 production run — second claudeception pass)
#   - PROMOTED: Phase B review panel from "recommended" to NON-NEGOTIABLE.
#     v1.0.1 skipped Phase B entirely; retroactive panel then flagged a P0
#     consolidation contradiction both self-reviews missed, plus an overstated
#     4.4× → 3.17× effect size. Without review-and-iterate loop,
#     self-reviews alone do not catch cross-doc / cross-persona issues.
#     See references/phase_b_review_loop.md §NON-NEGOTIABLE.
#   - Added mandatory iterate-until-approved-or-capped protocol. MAX_ROUNDS=3;
#     cap ships with "awaiting human signoff" banner if unresolved P1s remain.
#   - Orchestrator contract: no longer may a subagent decide whether the
#     panel runs. Phase A → Phase B is a workflow invariant.
#
# 1.1.0 (2026-04-17, post-v1.0.1 run)
#   - Added Phase 0.-1 credential-liveness pre-flight (ADC token + BQ + dataset +
#     tool + Python libs). Prevents the "Track B blocked on expired ADC" failure
#     mode that wasted a full 6.5-hour background-agent dispatch.
#     See references/phase_0_preparation.md §0.-1.
#   - Added SHAP-interaction normalization (rho_shap = mean_product /
#     (mean_abs_a * mean_abs_b)) as the required ranking statistic for
#     scan_shap_interaction. Raw mean_product confounds interaction with
#     marginal product; rho_shap isolates genuine co-movement beyond marginals.
#     See references/shap_interaction_scoring.md.
---

# Overnight Insight Discovery

## Problem

Clients often want **genuinely surprising insights** from their data — things that make
them pause and re-read, not just another dashboard chart. But surfacing true ah-ha
findings is hard:

- **Pure LLM exploration** is creative but unreliable. Opus 4.7 with 1M context will
  happily narrate known facts as "surprising", or drift into statistical sloppiness.
  You can't distinguish a real find from a hallucination after the fact.
- **Pure mechanical scans** are rigorous but narrow. A conditional-lift miner surfaces
  every statistically significant pair, most of which are already known to domain
  experts or are restatements of the model's top SHAP features.
- **Single-round review** rubber-stamps whatever the analyst wrote. No independent
  voice checks novelty, client relevance, or methodological soundness.

Without structure, overnight insight jobs produce low-signal briefs that get one read
and then quietly discarded. The morning surprise wears off; the client doesn't act.

This skill structures an overnight autonomous insight job as:

1. **Two independent tracks run in parallel** — one LLM-autonomous (creative latitude),
   one hybrid deterministic-with-LLM-narration (mechanical rigor). Each produces its
   own brief against the same scoping doc.
2. **Each track runs a review loop** — `agent-review-panel` (6 personas + Supreme
   Judge) scores the brief, `plan-review-integrator` applies findings, fix-subagents
   re-query BQ and regenerate charts, repeat ≤ 3 rounds with composite exit.
3. **A consolidator subagent merges** both approved briefs into one client-facing
   document, flagging agreement vs. divergence as itself a signal.
4. **One more review pass** on the consolidation with stricter exit criteria.
5. **Deterministic HTML rendering** + morning summary + PR with DO NOT MERGE banner.

The key design property: the **novelty gate is cohort-conditional** and applied by an
independent reviewer persona, not trusted to either track's own self-assessment. This
is the single biggest lever against rediscovery of known SHAP top features.

## When to use

Use this skill when ALL of the following apply:

- Client wants insight-flavored output (funnel leaks, surprise patterns, hidden
  correlations, unexpected gradients) — not monitoring dashboards or action queues.
- You have 4+ hours of autonomous-run time tolerable for the work.
- The underlying data has both (a) enough history/volume to support exploratory
  querying and (b) a known set of "things the client already knows" (SHAP top features
  per cohort, published reports, existing dashboards) that can seed a novelty gate.
- At least one external reference for "correct numbers" exists (canonical counts,
  known baselines, etc.) so the `data-analyst` review persona has something to verify
  against.
- You want to ship a reviewable artefact (PR, HTML brief), not just drop findings
  into Slack.

Do NOT use when:

- The work requires user input mid-stream — switch to a synchronous session.
- Scope is a single narrow question — `deep-research` or `exploratory-data-analysis`
  handles that better.
- You don't know what "novel" means for this domain yet — do a cheaper scoping round
  first, come back with a known-knowns inventory.
- The deliverable is just polishing an existing document — use the sister skill
  `overnight-review-client-delivery` instead.

## Sister skill relationship

`overnight-review-client-delivery` (the predecessor) structures Phase A (content
work) / Phase B (parallel review panel) / Phase C (morning hand-off) for **generic**
client deliverables — polish a stale HTML report, fix P0 errors in a locked file,
regenerate a slide deck.

`overnight-insight-discovery` (this skill) replaces Phase A's "author the
deliverables" with a more opinionated **two-track insight-generation engine**, and
keeps the review-panel / morning-hand-off structure from its sister. If you already
have the brief and just need review, use `overnight-review-client-delivery`. If you
need to *generate* the brief from scratch overnight, use this one.

Both skills share: locked-file escape hatch, parallel-branch hygiene, aggressive cost
cap, archive-and-regenerate for stale files.

## The six phases

Full detail for each phase lives in `references/`. Read the phase doc when you're
executing that phase — don't load all of them upfront.

| Phase | Duration | Purpose | Reference |
|---|---|---|---|
| 0 | ~90 min | **Schema reality check first** (0.0), then scoping, stitched data views, cohort-conditional known-knowns | `references/phase_0_preparation.md` |
| A | ~5–6 hr | Tracks B and C run in parallel, each produces a brief | `references/phase_a_tracks.md` |
| B | ~2 hr (parallel) | Per-track review loop, up to 3 rounds | `references/phase_b_review_loop.md` |
| D | ~45 min | Consolidation subagent + one stricter review pass | `references/phase_c_consolidation.md` |
| E | ~30 min | Deterministic HTML rendering | `references/phase_c_consolidation.md` |
| F | ~15 min | Morning summary + PR opener | `references/phase_c_consolidation.md` |

Wall-clock target: 8 hours end-to-end. Budget cap tunable via scoping config.

## Core patterns

These are the five transferable patterns this skill captures. Each has a deeper
reference doc; the summaries below are enough to remember what the pattern DOES, not
how to implement it.

### Pattern 1 — B-vs-C parallel tracks

Two tracks against the same scoping doc:

- **Track B (LLM-autonomous):** Opus 4.7 1M given free exploration latitude, BQ +
  Python tools, a 1-paragraph brief spec, and hard constraints (don't use
  `CURRENT_DATE()`, reject rediscovery, file-first discipline, commit per finding).
- **Track C (hybrid):** Python candidate scans (funnel leak, conditional lift, SHAP
  interaction, cohort divergence) → surprise-scored rank → pruned → Opus narrates
  from pruned candidates only → Opus self-critiques → refine.

**Why both:** Track B finds things C's scans aren't designed for (creative
reframings, unexpected feature combos). Track C finds things B misses (systematic
cohort enumeration, statistical rigor). Where they agree, confidence is high. Where
they disagree, the *divergence itself* is a signal the consolidator surfaces.

### Pattern 2 — Cohort-conditional novelty gate

A single global SHAP top-20 is too coarse. "Missing credentials drive In-Process" is
trivial *for that cohort* but genuinely novel elsewhere. So the known-knowns table
is keyed by `(cohort_dim, cohort_value, feature_family, direction)`:

- **Pre-computed at Phase 0**: ~6–8 cohort dimensions × their levels × top-20
  features each = ~30 cells × 20 features = ~600 known-known rows.
- **On-demand decomposition** for compound cohorts (Accepted ∩ pending_docs ∩
  international): claim must include within-cohort SHAP decomposition file; novelty
  checked against each component's top-20.
- **Semantic similarity bands** applied by the `scientific-critical-thinker` review
  persona: ≥0.70 → auto-reject, 0.55–0.70 → weak/context-only, 0.30–0.55 → moderate
  with caveat, < 0.30 → strong ah-ha eligible.
- **Feature families** (~30 families like `accepted_nd_tenure` grouping the raw
  feature + its band variants) prevent near-duplicate rediscovery.

Reference: `references/cohort_novelty_gate.md`.

### Pattern 3 — Adaptive parameter-tuning loop

Track C's scans have thresholds (`lift_min=3×`, `cohort_min=50`, `stat_support=0.8`)
that are first-guess defaults. First run may produce 0 candidates (starved) or 500
(flooded). Wrap Stage 1–3 in an adaptive loop up to 3 iterations:

- Run scan → rank → prune with current `stage_config.yaml`.
- Classify yield: `starved / flooded / trivial / skewed / one-cluster / goldilocks`.
- Apply predefined relaxation/tightening per class (e.g., starved → lift 3→2, cohort
  50→25, p 0.01→0.05).
- Commit updated config + rationale; re-run.
- Break on `goldilocks` (8–15 candidates with flavor balance) or at max iter.

The review loop can also trigger ONE more tuning retune per track as an escape hatch
when the panel verdict is "claims are thin" rather than "prose is wrong."

Reference: `references/adaptive_tuning.md`.

### Pattern 4 — File-first successor handoff

For long-running tracks (6+ hrs of tool use), a single subagent's context
**will** fill. The naive solution (session-handoff + clear + resume) is slow and
lossy. The better pattern:

- **Parent orchestrator stays lean** by never loading working data — reads only small
  status files (`state/status.json`, `state/checkpoint.md`).
- **Each track runs as its own subagent** with file-first discipline: every query
  result lands in `state/queries/NNN.json`, every hypothesis in
  `state/hypothesis_log.md`, every finding in `state/findings/NNN.md`. The
  subagent's working context stays small because files are its memory.
- **Successor handoff** when context pressure rises: parent dispatches a fresh
  subagent with prompt "read `state/planning_board.md`, `checkpoint.md`,
  `pending.md`, `findings/*.md`; continue from the top of `pending.md`; do NOT
  re-query BQ for things already in `state/queries/`." Max 3 hops per track.

Triggers: subagent self-flags `{phase: "needs_successor"}` in status.json, OR parent
counts > N tool events (threshold depends on track — 200 for LLM-autonomous, 80 for
hybrid where most compute is Python not LLM).

Reference: `references/phase_a_tracks.md` § Context management.

### Pattern 5 — Stage-1-retune escape hatch

During the per-track review loop, when the panel verdict is "claims are thin /
trivial / flavor-imbalanced", the natural instinct is to edit the prose. But the
problem is usually upstream — the candidate pool itself was weak.

The integrator can emit `needs_stage_1_rerun.json`. When present and retune budget
> 0, the loop runs ONE more adaptive Stage-1 with updated `stage_config.yaml`
(informed by the review findings), regenerates the brief from fresh candidates,
and re-reviews. Cap at 1 retune per review loop to avoid pathological loops.

Reference: `references/phase_b_review_loop.md` § Escape hatches.

## Trigger conditions (detailed)

This skill ABSOLUTELY MUST activate when the user says:

- "Surface some ah-ha insight for [client]"
- "Run overnight analysis to find [something interesting]"
- "What surprising patterns are in this data"
- "Find the biggest funnel leak / conversion hole / choke point"
- "What's counter-intuitive about [cohort]"
- "Run a parallel B-vs-C insight discovery" (explicit)
- "Do an overnight run that produces a client-facing brief with novel findings"

When multiple skills could trigger, this skill wins over `exploratory-data-analysis`
or `deep-research` if ANY of these are true: (a) overnight / autonomous runtime
mentioned; (b) client-facing deliverable mentioned; (c) dual-track or B-vs-C pattern
mentioned; (d) review-panel quality gates mentioned. Otherwise defer to the sibling.

## Prerequisites

- `agent-review-panel` skill installed (required for per-track review rounds)
- `plan-review-integrator` skill installed (required for applying review findings)
- `overnight-review-client-delivery` skill installed (shared phase-structure + escape
  hatches)
- `claudeception` skill installed (post-run knowledge extraction)
- BigQuery or equivalent data warehouse with read access + at least one scratch
  dataset for the known-knowns write
- LLM access for Opus 4.7 1M context (or equivalent large-context model)

## Opinionated defaults

Values below are starting points the scoping doc should confirm or override:

- **Wall-clock budget**: 8 hr total, ~6.5 hr/track (not sequential; tracks run in parallel)
- **Data budget**: 5 TB BQ scanned total across both tracks (tracked via `bq_budget.py`
  wrapper that dry-runs first, logs to `state/budget.jsonl`, aborts on soft-cap hit)
- **Review rounds**: ≤ 3 per track, ≤ 1 Stage-1 retune per review loop
- **Consolidation review**: single round, stricter exit (Supreme Judge `Approve` only,
  not `Approve with revisions`)
- **Panel personas** (6 seeded + architect-reviewer as one-off post-consolidation):
  data-scientist, data-analyst, scientific-critical-thinker (novelty enforcer),
  client-trust-evaluator (the ah-ha gate), compliance-auditor, qa-expert
- **HTML deliverable**: 5 files — consolidation.html (client-facing) + brief_b /
  brief_c / review_panel_final / workflow_learnings as traceability + index.html

## Model policy (IMPORTANT — overrides `subagent-driven-development` default)

This workflow mandates **Opus (largest-context variant, e.g. Opus 4.7 1M)** for every
implementer subagent, every reviewer subagent, every consolidator subagent, and every
fix-dispatch subagent inside the review loop.

**Why the override matters.** The `superpowers:subagent-driven-development` skill has a
built-in model-selection heuristic that picks cheaper models (Sonnet) for tasks it
classifies as "mechanical implementation." Most tasks in this workflow look mechanical
in isolation (write a Python script, run SHAP, compute surprise scores, render HTML) but
depend on deep judgment that compounds across subagents: novelty-gate enforcement,
compound-cohort decomposition decisions, narrative quality that survives the
`client-trust-evaluator` persona's ah-ha gate. A single Sonnet-on-an-implementer-task
dispatch usually works in isolation but degrades downstream review signals in ways that
are hard to debug — the integrator rolls back edits that "were almost right" instead of
caught-wrong, review rounds spin, Stage-1 retune budget exhausts.

Explicit rule: **every `Agent` dispatch from the orchestrator must include
`model: "opus"` (or equivalent 1M-context variant).** If a subagent is dispatched
without this override and comes back using Sonnet, stop and re-dispatch the same task
with Opus. Sonnet output is acceptable for pre-Phase-0 exploratory scoping only.

The only exception: the `agent-review-panel` Supreme Judge already defaults to Opus per
that skill's own policy — no override needed. Every other dispatch needs the explicit
`model: "opus"`.

**When you're writing the orchestrator** (Task 26 of the typical implementation plan),
bake this into the dispatch helper so it's not a per-call decision. Example shape:

```python
def dispatch_implementer(task_text, context):
    return Agent(
        subagent_type="general-purpose",
        model="opus",  # REQUIRED for this workflow — override sub-skill default
        run_in_background=task_text.needs_background,
        description=task_text.name,
        prompt=build_prompt(task_text, context),
    )
```

Document the override prominently in the plan's header so fresh sessions don't
accidentally slip back to the sub-skill default.

## Workflow outline

Execute roughly in this order. Each phase's detailed instructions live in
`references/`. Read the reference when you start the phase.

```
Phase 0 — Preparation
  read: references/phase_0_preparation.md
  produce: scoping/config.yaml, stitched_score_view, known_knowns_by_cohort.jsonl,
           canonical_numbers.md, feature_families.jsonl, new-feature CTE snippets

Phase A — Launch tracks (parallel)
  read: references/phase_a_tracks.md
  Track B: dispatch Agent (general-purpose, opus, background) with Track B prompt
  Track C: run Python pipeline (scans → rank → prune with adaptive tuning → narrate → refine)

Phase B — Per-track review loop (up to 3 rounds each)
  read: references/phase_b_review_loop.md
  Per round:
    1. Skill(agent-review-panel) with track's brief + scoping files
    2. Skill(plan-review-integrator) with review output + brief
    3. Dispatch fix subagents for must-fix items; re-query BQ if needed
    4. check_exit: verdict + P0 count + trust score + P1-resolved count
    5. If fail: next round OR Stage-1 retune (if budget + needs_stage_1_rerun)

Phase D — Consolidation
  read: references/phase_c_consolidation.md
  - Dispatch fresh Opus 4.7 1M subagent (consolidator) with both finals + review synthesis
  - Produces consolidation.md + workflow_learnings.md
  - One final Skill(agent-review-panel) pass on consolidation (stricter exit)

Phase E — HTML rendering
  read: references/phase_c_consolidation.md
  - render_html.py (deterministic, pure function from Markdown)
  - 5 HTMLs + index.html, reusing project CSS

Phase F — Morning handoff
  read: references/phase_c_consolidation.md
  - write_morning_summary.py (7-section template from overnight-review-client-delivery)
  - open_pr.py with DO NOT MERGE banner

Phase G — claudeception
  Skill(claudeception) to capture any new learnings back into this skill or siblings
```

## Anti-patterns

- **Single track.** If you only run B or only C, you lose the hedging benefit. If
  compute is genuinely constrained and you must cut to one, choose C (auditable is
  better than creative for client-facing work) but log the trade-off explicitly.
- **Shared branch between parallel tracks.** Per `overnight-review-client-delivery`'s
  branch-hygiene lesson: unique branch names per agent. Tracks B and C commit to the
  same branch is fine because they write to disjoint directories (`track_b/` vs
  `track_c/`); parallel Claude *sessions* do not.
- **Novelty gate at the track level.** Track B will rationalize its own findings.
  Put the novelty gate on the **reviewer persona** (`scientific-critical-thinker`),
  not in the track's prompt. Trust-but-verify; the panel is the "verify."
- **Global SHAP top-20.** One feature can be rank-1 in Cohort A and rank-50 globally
  — the global rank is the wrong gate. Always cohort-conditional.
- **Letting the consolidator re-score.** The consolidator merges, doesn't re-review.
  Its review happens in the dedicated consolidation-review pass. If the consolidator
  starts re-ranking findings, the tracks' review loops become meaningless.
- **Hard MUST/NEVER in track prompts.** Opus 4.7 reasons well from WHY. The prompts
  in `phase_a_tracks.md` explain every constraint's rationale. Rigid rules without
  rationale get worked around; explained constraints get respected.
- **Executing without a scoping `known_knowns_by_cohort.jsonl`.** The novelty gate is
  the single biggest defense against "LLM narrates known facts as surprising." Skip
  it and you'll end up with a brief full of SHAP top-20 restatements.
- **Writing SQL before Phase 0.0 (schema reality check).** Production tables have a
  habit of being wide where plans assumed long (or vice versa). Row counts match; the
  first `CREATE VIEW` materializes against a non-existent column list; Task 3 blocks
  mid-run. `INFORMATION_SCHEMA.COLUMNS` queries take 5 seconds each and prevent ~2 hr
  of cascading rework across downstream tasks. Always run 0.0 before 0.3.

## Verification

After the run completes, verify success:

1. **PR is open with DO NOT MERGE banner.** `gh pr view <N> --json state` = `OPEN`.
2. **Supreme Judge returned `Approve` on the consolidation** — check
   `review_panel_report.md` from the consolidation review.
3. **consolidation.html renders cleanly** — open it in a browser, no broken charts.
4. **Cost is under cap** — check `state/budget.jsonl` for final cumulative TB + hrs.
5. **Every number in consolidation traces to a BQ query** in `queries/`. Grep one or
   two numbers manually.
6. **workflow_learnings.md exists** — even if the run was flawless, this captures
   lessons for v2.
7. **morning_summary.md § 1 flags any P0 fixes or capped loops** prominently.
8. **No unresolved P1s silently in the consolidation** — check Caveats footer.

If any of 1–5 fails, treat the brief as non-deliverable; fix and re-run the relevant
phase. 6–8 are strong preferences, not hard gates.

## When to skip phases

- **Skip Phase 0's stitched score view** if your data has only one model version in
  scope. The view is for multi-version reconciliation.
- **Skip the v-over-v SHAP compute** if only one model exists or SHAP isn't part of
  the cohort known-knowns (e.g., rule-based segmentation).
- **Skip Phase G claudeception** if no new pattern emerged — it's a courtesy to
  future sessions, not a blocker.

## When to add phases

- **Add a Phase 0.5 client glossary** if the domain has heavy jargon the review
  panel won't recognize. Include term definitions so `client-trust-evaluator` can
  distinguish "technically true but client-confusing" from "client-ready."
- **Add a deep-research Phase B.3 agent batch** for complex domains where
  methodology-vs-literature comparison matters (admissions propensity, clinical
  trials, etc.). Reuse `overnight-review-client-delivery`'s Phase B.3 pattern.

## See also

- **`overnight-review-client-delivery`** — sibling skill, use for polishing existing
  deliverables. Shares phase structure, locked-file escape hatch, branch hygiene.
- **`agent-review-panel`** — REQUIRED dependency. 16-phase review protocol with
  Supreme Judge arbitration + HTML dashboard.
- **`plan-review-integrator`** — REQUIRED dependency. Applies review findings to a
  plan/brief with rollback on coherence break.
- **`claudeception`** — post-run knowledge capture into this skill's next version.
- **`planning-with-files`** — the file-first discipline that makes successor handoff
  possible.
- **`writing-plans`** — use at the start of any run to produce the executable plan
  from a design doc.
- **`agent-review-panel` GitHub**: https://github.com/wan-huiyan/agent-review-panel

## References

The `references/` directory contains the detailed phase instructions. Read them
when you're executing that specific phase; don't load all of them upfront.

- `references/phase_0_preparation.md` — scoping, stitched views, known-knowns build
- `references/phase_a_tracks.md` — Track B prompt + Track C pipeline
- `references/phase_b_review_loop.md` — panel orchestration + exit criteria
- `references/phase_c_consolidation.md` — consolidation + HTML + morning handoff
- `references/cohort_novelty_gate.md` — deep dive on the novelty mechanism
- `references/adaptive_tuning.md` — yield classes + parameter tuning table

## Assets

- `assets/config_yaml_template.yaml` — scoping/config.yaml starter
- `assets/canonical_numbers_template.md` — canonical-numbers markdown
- `assets/morning_summary_template.md` — 7-section morning summary
- `assets/pr_body_template.md` — PR body with DO NOT MERGE banner

## Scripts

Shared deterministic utilities. Adapt to the project's stack (BQ vs Snowflake,
matplotlib vs plotly, etc.).

- `scripts/bq_budget.py` — cumulative TB wrapper with soft cap + JSONL log
- `scripts/render_html.py` — deterministic Markdown → HTML with embedded charts

## Origin

Extracted from a university-admissions propensity project overnight run on 2026-04-17.
Patterns that survived the first production run are canonical here.

## Version history

- **v1.0.2** (2026-04-17) — Added "Model policy" section making the Opus-throughout
  override explicit. The first production run had an implementer subagent dispatched
  with Sonnet because `subagent-driven-development`'s default heuristic classified the
  task as mechanical. Design intent was Opus throughout; the override was implicit in
  design-doc prose but not in the plan doc, so the fresh session correctly followed the
  sub-skill's default. Fix: document the override in the plan's header and in this
  skill's SKILL.md; bake `model="opus"` into the orchestrator's dispatch helper so
  it's not a per-call decision.
- **v1.0.1** (2026-04-17) — Added Phase 0.0 (schema reality check) based on a real blocker
  from the first production run: predictions table was wide-format (per-term score columns),
  plan assumed long-format. Row counts matched so the mismatch wasn't caught in Phase 0
  until Task 3 materialized a stitched view against non-existent columns. Phase 0.0 now
  mandates `INFORMATION_SCHEMA.COLUMNS` verification of every canonical table before any
  downstream SQL is written, with a `scoping/schemas.md` artefact documenting wide-vs-long
  shape, per-term pattern, primary keys, and term-independent columns.
- **v1.0.0** (2026-04-17) — Initial release.
