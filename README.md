# Overnight Workflows

Two sister [Claude Code](https://claude.com/claude-code) plugins for running **autonomous overnight work sessions** that land a polished deliverable on your desk by morning, with multi-agent review panels baked in to catch factual errors before they reach the client.

[![license](https://img.shields.io/github/license/wan-huiyan/overnight-workflows)](LICENSE)
[![last commit](https://img.shields.io/github/last-commit/wan-huiyan/overnight-workflows)](https://github.com/wan-huiyan/overnight-workflows/commits)
[![Claude Code](https://img.shields.io/badge/Claude_Code-plugin-orange)](https://claude.com/claude-code)

## The two plugins

| Plugin | When to use |
|---|---|
| [**overnight-review-client-delivery**](plugins/overnight-review-client-delivery/) | You already have a client deliverable (slide deck, report, HTML, memo) that needs polishing + quality-gating before a morning hand-off. Runs Phase A (content work) + Phase B (8-agent review panel in parallel) + Phase C (morning synthesis). |
| [**overnight-insight-discovery**](plugins/overnight-insight-discovery/) | You want to *generate* a client-facing insight brief from scratch — surfacing funnel leaks and surprise patterns from data. Runs two parallel tracks (B = LLM-autonomous creative exploration + C = hybrid deterministic-with-narration), consolidates, and reviews. |

They share the same phase structure, locked-file escape hatch, branch hygiene, and file-first discipline — use them as a pair, or individually.

## Why use these

Overnight autonomous runs are seductive but brittle. The typical failure modes:

- **Hallucinated conclusions.** The model "finds" patterns that are restatements of known features, or narrates trivial tautologies as surprising.
- **Factual errors ship to the client.** A single reviewer (you, sleep-deprived in the morning) misses a wrong BSTS CI, a decomposition table with inverted signs, a mislabelled cohort.
- **Stale content dressed as fresh.** Author adds an "archive banner" at the top + updates the headline, leaves the body with old numbers — readers can't tell which parts are current.
- **Context-window blowup.** A 6-hour autonomous run fills the model's window; the session compresses lossy, then drifts.
- **Parallel session commit-dropping.** Two agents on the same branch silently rebase each other's commits into oblivion.

These plugins encode the hard-won patterns that fix each of these — extracted from real overnight runs that caught real P0 errors before they reached real clients.

## Core patterns (shared across both plugins)

### 1. Multi-agent review panel

Neither plugin trusts the author (or the track) to self-review. A panel of 4–8 specialized reviewers runs on the deliverable — data-scientist, data-analyst, scientific-critical-thinker, client-trust-evaluator, compliance-auditor, qa-expert. A Supreme Judge arbitrates. Dependency: [`agent-review-panel`](https://github.com/wan-huiyan/agent-review-panel).

### 2. Locked-file escape hatch

Client-facing files are LOCKED by default. Modifying one requires **four conditions**: explicit prompt authorization, independent verification (BQ query OR second reviewer confirming), surgical-only edit, and prominent documentation in the morning summary. Without all four, flag as "REQUIRES USER DECISION."

### 3. File-first successor handoff

The parent orchestrator never loads working data — reads only small status files. Each track writes state to `state/status.json`, `state/planning_board.md`, `state/findings/*.md`. When context pressure rises, parent dispatches a fresh successor subagent that reads state files and continues. Max 3 hops per track.

### 4. Archive-and-regenerate (not banner-and-partial-update)

When refreshing stale content, never add an archive banner + update headlines in place. Archive the prior version (`name_context.html`) as a snapshot, then regenerate the active version from the current source of truth. Keeps readers oriented; passes the "would you stake your reputation on this" test.

### 5. Aggressive cost cap

`£0 Cloud Run + £0 Cloud Build + 5 TB BQ read` is the recommended envelope. Validated: entire overnight runs complete within this for most client-delivery and insight-discovery sessions. A `bq_budget.py` wrapper (shipped with `overnight-insight-discovery`) dry-runs every query, logs to JSONL, aborts on soft-cap hit.

### 6. Unique branch names per parallel agent

**Critical gotcha:** parallel Claude sessions on the same repo can silently drop each other's commits via rebase. Use `feature/session-NN-claude-A` vs `feature/session-NN-claude-B`. Push immediately after every commit. Treat `git reflog` as the safety net.

## Installation

```bash
# Add the marketplace
/plugin marketplace add wan-huiyan/overnight-workflows

# Install one or both plugins
/plugin install overnight-review-client-delivery@wan-huiyan-overnight-workflows
/plugin install overnight-insight-discovery@wan-huiyan-overnight-workflows
```

Or clone directly:

```bash
git clone https://github.com/wan-huiyan/overnight-workflows.git
cp -R overnight-workflows/plugins/overnight-insight-discovery ~/.claude/skills/
cp -R overnight-workflows/plugins/overnight-review-client-delivery ~/.claude/skills/
```

## Dependencies

Both plugins integrate tightly with:

- **[agent-review-panel](https://github.com/wan-huiyan/agent-review-panel)** — REQUIRED. 16-phase review protocol with Supreme Judge + HTML dashboard.
- **[plan-review-integrator](https://github.com/wan-huiyan/plan-review-integrator)** — Applies review findings to plans/briefs with rollback on coherence break.
- `planning-with-files` — File-first discipline that makes successor handoff possible.
- `claudeception` — Post-run knowledge capture into updated skill versions.

## Quick start

**Polish a client deliverable overnight** (existing doc/deck):

> "Run overnight-review-client-delivery on the Q4 marketing campaign report. The deliverable lives at `deliverables/campaign_impact.html`. Canonical numbers are in `scoping/expected_metrics.md`. Locked files: the three HTMLs going to the client tomorrow. Cap: £0 cloud spend, use BQ queries only."

**Discover ah-ha insights overnight** (generate from data):

> "Run overnight-insight-discovery on Q4 e-commerce data. Target: 2 funnel leaks + 2 surprise patterns for the exec brief on Monday. Fall campaign scope. Cap: 5 TB BQ, 8 hr wall-clock. Client = retail ops team."

Both plugins will ask for scoping details (target date, known-knowns table, canonical numbers, panel personas) before kicking off. Morning output: a PR with the deliverable, a morning summary flagging anything that needs your attention first, and a review-panel HTML dashboard.

## What you get by morning

- The finished deliverable (Markdown + HTML, client-ready)
- A `morning_summary.md` that flags the ONE thing to look at first (P0 fixes to locked files, capped loops, unresolved P1s)
- A review-panel HTML dashboard with per-round scores and persona-by-persona verdicts
- A PR to `main` with a **DO NOT MERGE** banner (you eyeball first)
- A `workflow_learnings.md` capturing concrete recommendations for the next run
- Full traceability: every commit per phase, every BQ query, every finding that DIDN'T make the brief (and why)

## Limitations

- These plugins are structured for **8 hour overnight windows**. Sub-hour sessions are overkill; multi-day projects need further decomposition.
- They assume a BigQuery-style data warehouse with read access + at least one scratch dataset. Snowflake / Redshift / Postgres will work but the budget wrapper and SHAP compute scripts need adaptation.
- Neither plugin can execute trades, move money, push to production, or run migrations — all side-effecting actions require explicit human authorization.
- `overnight-insight-discovery` requires a pre-populated cohort known-knowns table (~30 cells × top-20 features each). Without it, the novelty gate has nothing to enforce.

## How they compose

```
overnight-insight-discovery   →  generates the brief from scratch
                              ↓
overnight-review-client-delivery  →  polishes a known-good brief into client-shape
```

For new insight work, start with `overnight-insight-discovery`. For existing deliverables that just need polish + QA, go straight to `overnight-review-client-delivery`. For the full pipeline, chain them.

## Origin

Both plugins encode patterns from real client-delivery overnight runs. `overnight-review-client-delivery` was validated on a causal-impact project; `overnight-insight-discovery` was extracted from a university-admissions propensity project. The patterns are generalized for any project that needs autonomous overnight work with quality gates.

## Version history

- **v1.0.0** (2026-04-17) — Initial release bundling both plugins. `overnight-review-client-delivery` was previously a standalone skill; this bundle adds the insight-discovery sibling and unifies the shared patterns (locked-file escape hatch, branch hygiene, file-first successor handoff, archive-and-regenerate).

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Patches welcome. The shape of both plugins is still settling — if you run them in production and learn something the skills didn't catch, please open an issue or PR against the relevant `SKILL.md` or reference doc.

Common contribution targets:
- Additional panel personas for specific domains (medical, financial, legal)
- New yield classes for the adaptive tuning loop (insight-discovery only)
- Platform-specific adaptations of `bq_budget.py` (Snowflake, Redshift, Databricks)
- Alternative HTML renderers (beyond markdown2)

---

🤖 Patterns co-developed with Claude Code. All examples in the skills use synthetic data; no client-specific numbers in this repo.
