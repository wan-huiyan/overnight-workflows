# Phase B — Review loop (per track)

Each track's brief goes through up to 3 review rounds. Every round is a two-step:
`agent-review-panel` scores → `plan-review-integrator` applies findings.

## ⚠️ NON-NEGOTIABLE — do not skip this phase

Added in **v1.2.0** after the v1.0.1 production run skipped Phase B entirely
and shipped findings that a retroactive panel immediately flagged (P0
contradiction in consolidation, overstated lead number by ~30%, novelty-gate
fails on all initial track candidates).

**The review panel is the orchestrator's first-class step, not an optional
quality gate.** If Phase A produces briefs, Phase B MUST run on each of them
before any consolidation. The only acceptable reasons to skip:

- All tracks BLOCKED and produced no briefs → no panel needed (nothing to
  review). Log this explicitly in `morning_summary.md §1`.
- Running into MAX_ROUNDS=3 with unresolved P1s → ship with the "capped"
  marker per the locked-file protocol. **Capping is a valid exit, but
  skipping entirely is not.**

Anti-patterns caught in v1.0.1 that the panel would have caught in-flight:

1. **Top/bottom internal contradictions** — each self-review writes one
   framing in good faith; only a cross-doc cross-persona read surfaces the
   clash. `data-analyst` + `client-trust-evaluator` catching both lines of
   the same section is how this gets caught.
2. **Overstated effect sizes** (e.g. "4.4×" shipping as headline when a
   proper cross-cohort significance test yields "3.17× with wide CI") —
   `data-scientist` catches the missing test; `qa-expert` catches the
   missing reproducibility artefact.
3. **Un-cited numbers** — the brief cites `n=3,668 In-Process` but that
   total never appeared in `canonical_numbers.md`. `data-analyst` catches
   this on traceability pass.
4. **Scratch-table TTL** — brief depends on `ml_scratch.overnight_*` view
   that may expire within 30 days of a client rerun. `compliance-auditor`
   catches this on provenance check.

**Orchestrator implementation:** after Phase A completes for each track,
the orchestrator MUST invoke `Skill(agent-review-panel)` with the
6-persona seeded list (see §Panel composition below). Do not let a
subagent decide whether the panel is worth running. Do not defer the
panel to "after consolidation" — by that point the contradictions and
overstatements have already been baked into the client-facing output.

### Iterate until approved or capped

Round 1 panel verdict ≠ ship signal. If panel returns "Needs revisions":

1. `plan-review-integrator` applies must-fix P0s and P1s surgically.
2. Re-dispatch panel for round 2.
3. Repeat up to MAX_ROUNDS=3.
4. If still not approved at round 3, ship with prominent "capped — awaiting
   human signoff" banner at the top of the client-facing brief AND
   `morning_summary.md §1`.

The review-and-iterate loop is where the workflow earns its keep. Track C's
self-review alone caught some issues in v1.0.1; Track B's self-review caught
others. Neither caught the cross-doc contradiction. A panel would have. An
iterated panel would have also caught the overstated 4.4×. **v2 and beyond
must not skip this loop.**

## SQL re-execution gate (pre-panel, v1.4.0)

**NEW CONTRACT:** Before the panel sees any finding, every finding must pass
the SQL re-execution gate (see `sql_reexecution_gate.md`). The gate re-runs
each finding's supporting SQL and compares claimed vs returned values;
mismatches auto-retract the finding. This runs **once per track** between
Phase A completion and Round 1 of the review loop. Also applies
Benjamini-Hochberg multiple-hypothesis correction over the full surviving
finding set.

Motivation: S91's Finding 1 shipped with n=351 in the brief but a
verification query returned n=62 — the panel never caught it because the
panel doesn't re-run SQL, it reads claims as data. Gate fixes this
systemically rather than relying on the panel to catch every number.

## Panel composition

6 seeded personas + Supreme Judge (Opus). Seed explicitly rather than letting
the panel's auto-persona detection pick; the content type (analytical claims,
not code) is different enough from the default router's assumptions that
hand-picking matters.

| Persona | What it checks | Hard-fail trigger |
|---|---|---|
| data-scientist | Methodology, stat calibration, causal-vs-correlational overreach | CI width / sample size mismatches |
| data-analyst | Every number traces to a BQ query; cross-refs `canonical_numbers.md` | Any unverifiable number |
| scientific-critical-thinker | Novelty gate enforcement **+ cross-finding contradiction hunting (v1.4.0)** | Claim whose (feature family, cohort, direction) is in the known-knowns top-20 **OR** two surviving findings that cannot both be true |
| client-trust-evaluator | Would the client stop on this or say "we already knew"? | Domain-obvious, non-actionable, or vacuous |
| compliance-auditor | Data provenance, PII exposure in charts, canonical sources cited | Visitor-level PII, un-cited claims |
| qa-expert | BQ query reproducibility, chart script determinism, `CURRENT_DATE()` lint | Missing query files, non-deterministic chart scripts |

### Contradiction-hunter mandate (v1.4.0, assigned to `scientific-critical-thinker`)

Beyond the novelty gate, this persona now carries an **explicit
contradiction-hunting** mandate: compare claims *across* surviving findings
and *across* tracks before voting. Examples of contradictions worth flagging:

- Finding A claims Intl × UG has 3.17× tenure drop; Finding C claims Intl
  cohort is homogeneous across UG / PG. Both cannot be true without
  reconciliation.
- Track B says feature X is the top driver; Track C's mechanical scan shows
  feature X ranked 8th by surprise. Panel must ask which one holds.
- A surviving finding claims the opposite direction from a retracted
  finding's claim in `state/findings/retracted/` — why did one survive?

Motivation: v1.2.0's retroactive panel caught a cross-document contradiction
both self-reviews missed. Same-model sycophancy (see
`cross_model_tiebreaker.md`) makes this failure mode systematic — one
persona explicitly tasked with hunting contradictions mitigates it even when
the cross-model judge is unavailable.

The `scientific-critical-thinker`'s Round N report MUST include a
`contradiction_scan` section — empty section with "no contradictions found"
is acceptable; missing section is a P0.

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

## Stats-verification sub-phase (v1.3.0 — added S92 P0-2)

Added after the S91 retroactive panel caught the **overstated 4.4× headline**
that a post-hoc cross-band bootstrap test revised to 3.17× with wide CI. The
`data-scientist` persona had flagged the missing cross-band significance
test in Round 1 of the retroactive panel, but the test itself wasn't run
until the orchestrator did it manually. Had the test run in-flight, Round 2
would have received the corrected number and the client-facing brief would
never have shipped with 4.4×.

**Trigger.** The `data-scientist` persona's Round N report **may emit one or
more stats-verification requests**, one per claim whose methodology is
under-specified. A request is a JSON object in `review/round_N/stats_requests.jsonl`:

```json
{
  "claim_id": "F1_intl_ug_cliff_sharpness",
  "question": "Is the Intl 4.4× ratio significantly different from the 2.2× domestic average?",
  "snippet_path": "review/round_N/stats/F1_cross_band_test.py",
  "bq_queries": ["SELECT ... FROM ml_scratch.overnight_stitched_score_v1 WHERE ..."],
  "expected_output_shape": "{ratios: {band: point, ci_low, ci_high}, diff_ci: [low, high], p_greater: float}",
  "required_for": "P1 resolution — without this, the headline number is unverified"
}
```

The snippet is a short, self-contained `.py` (or `.sql`) the panel would run
if it could. It must:
- Pin `scoring_date` / `TARGET_DATE` explicitly (no `CURRENT_DATE()`)
- Print structured output to stdout (JSON-able or the expected shape)
- Complete in < 2 min of BQ time (cap: 10 GB scanned per request)
- Never write to a table — read-only verification only

**Orchestrator action (between Round N and Round N+1).** The orchestrator
inspects `stats_requests.jsonl`. For each request:

1. Reads `snippet_path`; runs it (`python3 <snippet>` or `bq query < <snippet>`).
2. Captures stdout + exit code to `review/round_N/stats/<claim_id>.txt`.
3. If exit code ≠ 0 or runtime > 2 min, marks the request `ERRORED` and
   escalates to the Supreme Judge (don't silently drop — a failed
   verification is itself a signal).
4. Appends a `stats_verification_result` block to the integrator's input
   bundle.

**Integrator action (Round N integration).** The integrator reads the
verification result alongside the panel report:

- If verification **contradicts the brief's headline**, this is auto-promoted
  to a **P0 must-fix** — the brief's number gets overwritten with the
  verified value before the brief is passed to Round N+1.
- If verification **confirms the brief's headline**, the panel's P1 finding
  is marked `[VERIFIED]` in round-N integration log — satisfying exit
  criterion 3 one P1 at a time.
- If verification **refines the claim** (e.g., confidence interval is wider
  than the brief implies), the integrator inserts CI language + the
  verified number; no rollback.

**Round N+1 panel input.** The verified results are loaded into the round
N+1 panel's input bundle (`review/round_(N+1)/verified_stats.json`) so the
panel can see what was reconciled without re-deriving it.

**Budget guard.** Max 3 stats-verification requests per track per round
(prevents the panel from demanding an ad-hoc re-analysis of every claim).
If more are warranted, trigger a Stage-1 retune (Track C) or successor
handoff (Track B).

**Contract summary:**

```
data-scientist → stats_requests.jsonl (≤ 3 entries)
  ↓
orchestrator → runs each snippet → stats/<claim_id>.txt
  ↓
integrator → verified_stats.json → applies to brief
  ↓
round N+1 panel ← verified_stats.json as input
```

**Binarisation-agreement check (v1.3.1 — added post-v2 run).** Before
marking a stats-verification request `[VERIFIED]`, the integrator MUST
compare the verification snippet's cell-count signature against the
brief's (or probe's) cell-count signature for the same claim. Mismatch
means the snippet is measuring something different than the brief
asserts.

Concrete failure mode from the v2 run: F2 verification snippet used
`df["lo91"] = df["login_91_180d"].fillna(0).astype(int)` on a raw count
column (values 0, 1, 2, …). The subsequent filter `df["lo91"] == 1`
selected ONLY applicants with exactly one login in the 91-180d window,
not all applicants with any login activity. Cell (1,1) contained n=358
instead of the brief's n=1,437. The interaction CI came back
[−5.21, +2.15]pp straddling zero — would have wrongly downgraded F2 to
SINGLE-SOURCE if not caught.

Fix: the snippet's author (data-scientist persona) must binarise count
columns explicitly with `(x > 0).astype(int)` when the brief's cells
are boolean-thresholded, and the integrator must verify the snippet's
printed cell counts match the brief's cell counts (or verdict.json's
numbers) before accepting the verdict.

Minimum check the integrator runs:

```python
# In integrator's verify loop
brief_cells = load_brief_cell_signature()  # {(factor_tuple): n}
snippet_cells = parse_snippet_stdout_for_cells()
for key in brief_cells:
    if abs(brief_cells[key] - snippet_cells.get(key, 0)) > max(5, brief_cells[key] * 0.05):
        mark_request_BIN_MISMATCH(request_id, expected=brief_cells, got=snippet_cells)
        # Does NOT block — the snippet still runs; but the verified_stats.json
        # tags this as needing binarisation-fix, and the round N+1 panel sees the
        # discrepancy, not a false [VERIFIED].
```

The panel, not the integrator, has final authority on whether the
snippet measures the same claim — but the integrator's mechanical
check surfaces the mismatch instead of burying it.

## Exit criteria (composite — ALL must hold)

1. Supreme Judge verdict ∈ {`Approve`, `Approve with minor revisions`}
2. Zero unresolved P0 findings
3. ≥ 2 P1 findings flagged `[VERIFIED]` or `[CONSENSUS]` are resolved (tolerate
   up to 2 remaining P1s if tagged `[SINGLE-SOURCE]`). A P1 that the
   stats-verification sub-phase returned as `[VERIFIED]` counts the same as
   one resolved via panel consensus.
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

## Cross-model tie-breaker sub-phase (v1.4.0)

After the 6-persona panel returns a verdict in a given round, any finding
that passes with `pass_count ≥ 4` is routed through a cross-model tie-breaker
judge (see `cross_model_tiebreaker.md`). The tie-breaker:

- Runs after panel verdict, not as a 7th panelist (keeps it independent).
- Uses a non-Claude model family (codex / OpenAI / Gemini), probed for
  availability at Phase 0.-1.
- Degrades gracefully: if no external judge is available, findings ship with
  a `[SAME_MODEL_PANEL_ONLY]` caveat banner rather than blocking the run.
- A high-confidence `reject` from the tie-breaker forces an extra round with
  the rejection reasons promoted to P1 must-fix.

This addresses the v1.2.0 claudeception-pass finding that same-model
homogeneous panels miss cross-doc contradictions their training shares.

## Fresh subagent per review round (v1.4.0)

**CHANGED FROM v1.3:** Round N+1's panel runs in a **fresh subagent context**,
not the orchestrator's continuing context. The fresh subagent reads only:

- The round-N structured findings artifact (`review/round_N/report.md`)
- The round-N integrator log (`integration_log.jsonl`)
- The edited brief (`brief_X_v(N+1).md`)
- The stats-verification results (`verified_stats.json`)
- The retracted-findings register (`state/findings/retracted/gate_report.jsonl`)

It does NOT read round-N's persona reasoning chains / transcript. This
enforces Nightwire-style data-only input: the reviewer sees WHAT was
decided, not HOW the prior round reasoned, preventing anchoring-bias
cross-contamination between rounds.

Dispatch shape for round N+1:

```
Agent(
  subagent_type="general-purpose",
  model="opus",
  description=f"Review round {N+1} for track {track}",
  prompt=f"""
    Invoke Skill(agent-review-panel) with these inputs:
      brief: {brief_vN1_path}
      retracted_findings: state/findings/retracted/gate_report.jsonl
      verified_stats: review/round_{N}/verified_stats.json
      prior_round_report: review/round_{N}/report.md  # data, not transcript
    Do NOT read review/round_{N}/process.md (the transcript).
    Seed personas: [6 above].
    Report back verdict + path to review_panel_report.md.
  """
)
```

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
