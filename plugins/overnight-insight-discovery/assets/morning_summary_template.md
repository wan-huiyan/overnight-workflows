# Overnight Run Morning Summary — <YYYY-MM-DD>

**Run ID:** <run_id>
**Branch:** <branch>
**PR:** <pr-url>
**Critical first thing to read:** <§ number — point at the most urgent section>

## 1. The one thing that needs your attention NOW

<Flag prominently if ANY of the following happened:
 - A track capped at MAX_ROUNDS without approval
 - A Stage-1 retune was used (either track)
 - A locked-file edit was applied post-consolidation review
 - Supreme Judge approval was "Approve with revisions" on consolidation (we require bare "Approve")
 - Budget soft-cap was hit
 - Any subagent reported BLOCKED
 Otherwise: "No urgent items — run completed within all guardrails.">

## 2. What got done overnight

**Phase 0 — Preparation:** <commit refs>
- scoping/config.yaml: <summary>
- scoping/known_knowns_by_cohort.jsonl: <n> rows across <k> cohort cells
- scoping/stitched_score_view_v1: materialized
- v<version>-SHAP-from-training: <n> rows (source: <source>)

**Phase A — Tracks (parallel):**
- Track B: <n> findings, <n> hypotheses explored, <n> successor hops, <GB> scanned
- Track C: <n> tuning iterations, yield class converged to <class>, <n> candidates survived pruning

**Phase B — Review loops:**
- Track B: <n> rounds, final verdict <verdict>, trust_score <x>/10
- Track C: <n> rounds, final verdict <verdict>, trust_score <x>/10
- Stage-1 retunes used: <0 or 1 per track>

**Phase D — Consolidation:**
- consolidation.md: <n> findings (<n_B> B-flavor + <n_C> C-flavor)
- Consolidation review verdict: <verdict>
- workflow_learnings.md: see § v2 recommendations

**Phase E — HTML:** <5 files> rendered, <MB> total

**Phase F — PR:** opened at <url>

## 3. Phase B review panel findings (severity-ranked)

### P0 unresolved
<None, or list with file:line refs>

### P1 unresolved
- [ ] <finding> — from <persona>, round <n>, track <b/c>
- [ ] <finding> — ...

### P2 (tracked for v2)
- <finding>
- <finding>

Unresolved P1s from Caveats footer in consolidation.md:
- <carried-over caveat 1>
- <carried-over caveat 2>

## 4. Outstanding items (ordered by priority)

**Must-do before client delivery:**
1. Eyeball `html/consolidation.html` (5 min)
2. Run the sanity-check queries in § 6 (10 min)
3. Confirm PR reviewers list is just you (30 sec)
4. If any P0 is open, fix before anything else

**Should-do before next run:**
- <item>
- <item>

**Could-do (logged for v2):**
- <item from workflow_learnings.md>
- <item>

**Blocked:**
- <item waiting on external input — contact & ETA>

## 5. Cost tally

| Line | Actual | Cap | % used |
|---|---|---|---|
| BQ scanned | <X.XX> TB | 5.0 TB | <XX>% |
| Wall-clock | <X.X> hr | 8.0 hr | <XX>% |
| Tokens (Opus 4.7 1M) | <N> | budget | <$X.XX> USD |

Per-track split (from `state/budget.jsonl`):
- Track B: <X.XX> TB, <X.X> hr, <N> tokens
- Track C: <X.XX> TB, <X.X> hr, <N> tokens
- Consolidation: <X.XX> TB, <X.X> hr, <N> tokens
- Review panels: <X.XX> TB, <X.X> hr, <N> tokens

## 6. Sanity-check queries

Drop-in BQ queries reproducing the consolidation's headline numbers. Paste each,
run, verify the result matches the brief within noise.

### Query 1 — <finding 1 headline number>

```sql
<verbatim SQL from the brief's evidence>
```

Expected: <number in brief>. Allowable drift: <±X%>.

### Query 2 — <finding 2 headline number>

```sql
<SQL>
```

Expected: <n>. Allowable drift: <±X%>.

(Repeat for all 4 findings in the consolidation.)

## 7. Signs that something went wrong

Grep the following; if any return non-empty, investigate before signing off:

- `git log --oneline --grep="status: capped"` — any track capped?
- `git log --oneline --grep="BLOCKED"` — any subagent blocked?
- `git log --oneline --grep="ROLLBACK"` — any integrator rollback?
- `find . -name "needs_successor.json"` — any unsent successor requests?
- Branch state: `git status` should be clean; any uncommitted work means something didn't finalize.
- `gh pr view <n> --json state,mergeable` — state should be OPEN, mergeable CLEAN (CI passing).

If any signal fires, investigate that thread before delivering to client.

---

**Sign-off checklist before PR merge:**

- [ ] § 1 has no P0
- [ ] § 6 sanity queries all match the brief
- [ ] `html/consolidation.html` reads well end-to-end
- [ ] No PII visible in charts
- [ ] Caveats footer is accurate
- [ ] Cost is under cap
- [ ] You've slept and read this at least once with fresh eyes

Only then request human review + merge the PR.
