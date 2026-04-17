# Phase B — Review loop (per track)

Each track's brief goes through up to 3 review rounds. Every round is a two-step:
`agent-review-panel` scores → `plan-review-integrator` applies findings.

## Panel composition

6 seeded personas + Supreme Judge (Opus). Seed explicitly rather than letting
the panel's auto-persona detection pick; the content type (analytical claims,
not code) is different enough from the default router's assumptions that
hand-picking matters.

| Persona | What it checks | Hard-fail trigger |
|---|---|---|
| data-scientist | Methodology, stat calibration, causal-vs-correlational overreach | CI width / sample size mismatches |
| data-analyst | Every number traces to a BQ query; cross-refs `canonical_numbers.md` | Any unverifiable number |
| scientific-critical-thinker | Novelty gate enforcement | Claim whose (feature family, cohort, direction) is in the known-knowns top-20 |
| client-trust-evaluator | Would the client stop on this or say "we already knew"? | Domain-obvious, non-actionable, or vacuous |
| compliance-auditor | Data provenance, PII exposure in charts, canonical sources cited | Visitor-level PII, un-cited claims |
| qa-expert | BQ query reproducibility, chart script determinism, `CURRENT_DATE()` lint | Missing query files, non-deterministic chart scripts |

One-off post-consolidation: `architect-reviewer` reviewing the workflow itself,
producing `workflow_learnings.md`. Not in the per-track loop.

## Round structure

```
Round N for track_X:
  Phase 1: Skill(agent-review-panel)
    inputs:
      - brief_X_vN.md (or brief.md for round 1)
      - queries/, charts/
      - candidates.parquet (Track C only)
      - scoping/canonical_numbers.md
      - scoping/known_knowns_by_cohort.jsonl
      - analysis_principles.md
    seeded_personas: [6 above]
    deep_mode: true  # web research for domain-literature novelty
    outputs: review/round_N/{report.md, process.md, report.html}

  Phase 2: Skill(plan-review-integrator)
    inputs:
      - review/round_N/report.md
      - brief_X_vN.md
    actions:
      - Classify each finding: must-fix / bundle / defer / info (epistemic-weighted)
      - Apply must-fix edits surgically; rollback on coherence break
      - Log to review/round_N/integration_log.jsonl
      - If any must-fix requires new data, emit needs_stage_1_rerun.json
    outputs: brief_X_v(N+1).md + traceability.md

  Phase 3: Exit evaluation (see § Exit criteria below)
    - if should_exit: brief final at brief_X_v(N+1).md
    - elif round < MAX_ROUNDS AND needs_stage_1_rerun AND retune_budget > 0:
        trigger ONE adaptive Stage-1 retune (Track C only)
        regenerate brief from fresh pruned.parquet
        start Round N+1 with the regenerated brief
    - elif round < MAX_ROUNDS:
        start Round N+1 with the edited brief
    - else:
        cap at MAX_ROUNDS; write "capped — awaiting human signoff" marker
        brief still goes to consolidation, caveat prominent
```

## Exit criteria (composite — ALL must hold)

1. Supreme Judge verdict ∈ {`Approve`, `Approve with minor revisions`}
2. Zero unresolved P0 findings
3. ≥ 2 P1 findings flagged `[VERIFIED]` or `[CONSENSUS]` are resolved (tolerate
   up to 2 remaining P1s if tagged `[SINGLE-SOURCE]`)
4. `client-trust-evaluator` verdict ≥ 6/10 — the ah-ha gate, independent of
   methodological soundness

Missing any one → loop continues (or caps at MAX_ROUNDS).

## Budget guards

- MAX_ROUNDS = 3 per track (hard cap)
- Stage-1 retune budget = 1 per track (Track C only; Track B doesn't have a
  re-generation step to retune)
- Panel wall-clock ≈ 45 min/round
- BQ scans per round per track ≤ 100 GB (small claim-verification probes)

## Escape hatch — Stage-1 retune

When to use: review verdict is "claims are thin", "trivial", "flavor-imbalanced"
— i.e. the **candidate pool itself** is the problem, not the prose. The natural
instinct is to edit text; the better move is to regenerate candidates with
tighter filters.

Mechanics:
1. Integrator detects a must-fix finding that says "need more/better
   candidates" (e.g., "all findings are restatements of SHAP top-20", "only
   B-flavor findings survived").
2. Emits `needs_stage_1_rerun.json` with a suggested `stage_config.yaml` delta
   (e.g., `novelty_similarity_max: 0.7 → 0.55`; `require_off_axis_cohort: true`).
3. Loop runs one more adaptive Stage-1 pass with the new config.
4. Regenerates brief from fresh `pruned.parquet`.
5. Starts the next review round with the regenerated brief.
6. Retune budget decrements by 1. Cap at 1 per review loop — avoids
   pathological "retune forever" loops.

Not available for Track B (no deterministic regeneration step; escalate to
prose edits or successor handoff instead).

## Built-in anti-sycophancy safeguards (DO NOT OVERRIDE)

`agent-review-panel` ships with: blind final scoring, calibrated skepticism
20–60%, sycophancy detection (flags when > 50% of position changes lack new
evidence), correlated-bias warning (unanimous agreement = soft flag), judge
confidence gating.

Rely on these. If all 6 personas unanimously approve on Round 1, that's a soft
warning flag — note it in the morning summary; don't accept it silently.

## Context + file-first discipline

Parent orchestrator never loads `review_panel_process.md` (verbatim agent
transcripts, can be huge). It reads only:
- `review_panel_report.md` (structured findings)
- `verdict.json`
- `integration_log.jsonl`

Human inspection of `process.md` is a morning-time concern, not parent-context.

## Dispatch pattern

Both `Skill(agent-review-panel)` and `Skill(plan-review-integrator)` can be
invoked as sub-skills within the orchestrator session. If the orchestrator's own
context is at risk, dispatch each through a fresh subagent instead:

```
Agent(subagent_type="general-purpose", model="opus", prompt="""
  Invoke Skill(agent-review-panel) with these inputs: [...]
  Report back the path to review_panel_report.md and the Supreme Judge verdict.
""")
```

This pattern also keeps the orchestrator's tool-event count down, reducing
pressure for its own successor handoff.

## Common failure modes

- **Unanimous approval Round 1 without an ah-ha.** Flag explicitly; either the
  panel is being sycophantic or the brief genuinely lands. The
  client-trust-evaluator's score tells you which — if ≥ 7/10, trust it.
- **P0 count keeps ticking up.** Something is fundamentally wrong with the
  brief's approach. Trigger a Stage-1 retune (Track C) or successor-handoff
  Track B to a fresh subagent that reads the review findings and rewrites.
- **Integrator rolls back every must-fix edit** due to coherence break. The
  brief's narrative is brittle — retune Stage 1 rather than trying to patch.
- **Review round wall-clock > 60 min.** Usually means the panel is doing
  unnecessary literature deep-research; disable `deep_mode` for round 2+ if so.
