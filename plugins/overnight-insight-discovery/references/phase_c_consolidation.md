# Phases D, E, F — Consolidation, HTML, morning handoff

After both tracks exit their review loops (approved or capped), three short phases
produce the final deliverables.

## Phase D — Consolidation (~45 min)

### D.1 Dispatch the consolidator

A fresh Opus 4.7 1M subagent. NOT the orchestrator — fresh context. Reads both
final briefs + review synthesis + scoping, writes the merged document.

Inputs:
- `track_b/brief_b_final.md` + `track_b/review/round_N/report.md` + `verdict.json`
- `track_c/brief_c_final.md` + `track_c/review/round_N/report.md` + `verdict.json`
- Full traceability from both `integration_log.jsonl`
- `scoping/*` (config, canonical_numbers, known_knowns_by_cohort, analysis_principles)

Prompt (compressed):

> Produce `consolidation.md` — the client-ready brief — by synthesising the
> strongest findings across both tracks. Cover 2 B-flavor + 2 C-flavor findings.
> For each:
> - Flag which track(s) found it: `[B+C: confirmed by both]` / `[B-only]` / `[C-only]`
> - Report both tracks' numbers if they differ — divergence is itself a signal,
>   don't paper over
> - Cite the strongest evidence from whichever track had it
> - Carry every unresolved P1 from both review loops into a "Caveats" footer
>
> Also produce `workflow_learnings.md`:
> - B vs C side-by-side: what did each find that the other didn't?
> - Count of findings surviving novelty + review per track
> - Review-round count per track (which converged faster?)
> - Token + BQ cost per track
> - Concrete v2 recommendations

Output files: `consolidation.md`, `workflow_learnings.md`.

### D.2 Consolidation review pass

One more `Skill(agent-review-panel)` pass on `consolidation.md` only (not
brief_b/brief_c). Full 6-persona panel, single round. Stricter exit:

- Supreme Judge must return `Approve` (not `Approve with revisions`)

If the judge asks for revisions: apply them surgically via
`Skill(plan-review-integrator)`, re-review once, then ship. No further looping —
wall-clock protection.

## Phase E — HTML rendering (~30 min)

Deterministic — no LLM re-interpretation. `scripts/render_html.py` reads the
committed Markdown files and writes HTML.

Required files in `html/`:

| File | Purpose | Source |
|---|---|---|
| `consolidation.html` | Client-ready brief | `consolidation.md` |
| `brief_b.html` | Track B traceability | `track_b/brief_b_final.md` |
| `brief_c.html` | Track C traceability | `track_c/brief_c_final.md` |
| `review_panel_final.html` | Score progression per track + round + traceability | Auto-generated from review_panel_report.html files + integration_logs |
| `workflow_learnings.html` | B vs C comparison | `workflow_learnings.md` |
| `index.html` | Landing page linking all 5 | Auto-generated |

### Styling

Reuse the project's existing CSS (dashboard or similar) by copying it into
`html/style.css`. Don't invent new styles — brand consistency matters when the
client opens the HTML.

### Charts

Embed as `<img src="../charts/...">` with relative paths. PNGs were produced in
Phase A by both tracks; they're already committed. No inline JS, no CDN
dependencies — the `html/` directory should be zip-emailable as-is.

### Idempotency

Re-running `render_html.py` on unchanged Markdown must produce byte-identical
HTML. Use this during debugging to avoid spurious diffs.

## Phase F — Morning handoff (~15 min)

### F.1 `morning_summary.md`

Follow the 7-section template from `overnight-review-client-delivery`:

```markdown
# Overnight Run Morning Summary — <date>

**Branch:** <branch>
**PR:** <link>
**Total cost:** <BQ TB> / <hr wall-clock> / <tokens>
**Critical first thing to read:** <§ number>

## 1. The one thing that needs your attention NOW

[If any track capped at MAX_ROUNDS, or a Stage-1 retune was used, or any
locked-file edit was applied post-consolidation, FLAG IT HERE with diff +
rationale.]

## 2. What got done overnight

[Commits per phase, track counts, review rounds completed]

## 3. Phase B review panel findings (severity-ranked)

[Consolidated list across both tracks + consolidation review]

## 4. Outstanding items (ordered by priority)

1. Must-do before client delivery
2. Should-do
3. Could-do
4. Blocked

## 5. Cost tally

BQ: <actual> TB / <cap> TB
Wall-clock: <actual> hr / <cap> hr
Tokens: <actual> (<USD>)

## 6. Sanity-check queries

[5–10 drop-in BQ queries reproducing the consolidation's headline numbers —
paste-and-verify before signing off]

## 7. Signs that something went wrong

[What to look for: gaps in commit history, unexpected branch state, failed
tests, capped loops, successor-handoff limits hit]
```

Pull cost data from `state/budget.jsonl`. Pull review rounds from the review
directories. Pull locked-file edits from `git log --grep="locked"` or similar.

### F.2 Open PR

```bash
gh pr create \
  --title "<project>: ah-ha insight brief — <date> (v1 B-vs-C pilot)" \
  --base main \
  --head <branch> \
  --body "$(cat pr_body.md)" \
  --reviewer <user-only>
```

PR body must prominently include:
- ⚠️ **DO NOT MERGE** without eyeballing `html/consolidation.html`
- Link to the landing page (`html/index.html`)
- List of unresolved P1s as review checkboxes
- Cost tally (matches morning summary § 5)

See `assets/pr_body_template.md` for the full template.

Do not request reviewers beyond the user. Client-facing briefs need human eyes
before any team-wide notification.

### F.3 (Optional) Notify yourself

If the project has a Feishu/Slack/email notifier, send a summary. But keep it
off-by-default for v1 — surprise pings wake people up. Morning summary is the
single touch point.

## Phase G — claudeception (post-handoff)

After PR opens, run `Skill(claudeception)` to capture any new patterns from
this run into an updated version of this skill or a sibling. Common updates:
- New yield class for the adaptive tuning loop
- New panel persona that proved decisive
- New feature-family grouping that caught near-duplicate rediscovery
- Correction to an anti-pattern

Commit any skill changes under the user's `~/.claude/skills/` directory.

## Common pitfalls

- **Letting the consolidator re-rank findings**. The consolidator merges; it
  doesn't re-review. If it starts re-scoring, the tracks' review loops lose
  meaning.
- **Adding new facts in HTML rendering**. `render_html.py` is deterministic. If
  you want to clarify something, edit the Markdown and re-render.
- **Merging the PR automatically**. Don't. The DO NOT MERGE banner exists
  because the client-facing document needs a human read-through first.
- **Skipping the sanity-check queries in § 6**. These are what catch "looks
  right but actually wrong" bugs. 5 minutes of verification beats a bad client
  delivery.
