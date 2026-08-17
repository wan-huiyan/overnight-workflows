# Overnight Workflows

Sister [Claude Code](https://claude.com/claude-code) plugins for running **autonomous overnight work sessions** that land a polished deliverable, an insight brief, or a stack of reviewed PRs on your desk by morning, with multi-agent review panels baked in to catch factual errors before they reach the client.

[![license](https://img.shields.io/github/license/wan-huiyan/overnight-workflows)](LICENSE)
[![last commit](https://img.shields.io/github/last-commit/wan-huiyan/overnight-workflows)](https://github.com/wan-huiyan/overnight-workflows/commits)
[![Claude Code](https://img.shields.io/badge/Claude_Code-plugin-orange)](https://claude.com/claude-code)

## Workflow plugins

| Plugin | When to use |
|---|---|
| [**overnight-review-client-delivery**](plugins/overnight-review-client-delivery/) | You already have a client deliverable (slide deck, report, HTML, memo) that needs polishing + quality-gating before a morning hand-off. Runs Phase A (content work) + Phase B (8-agent review panel in parallel) + Phase C (morning synthesis). |
| [**overnight-insight-discovery**](plugins/overnight-insight-discovery/) | You want to *generate* a client-facing insight brief from scratch — surfacing funnel leaks and surprise patterns from data. Runs two parallel tracks (B = LLM-autonomous creative exploration + C = hybrid deterministic-with-narration), consolidates, and reviews. Gated by BOTH a **novelty** check (not a known feature restated) and an **analytical validity** check (`references/observational_analysis_rigor.md` — composition / leak / anchor-timing / marker-vs-lever) so a surprising-but-wrong finding can't ship. |
| [**overnight-multi-issue-implementation**](plugins/overnight-multi-issue-implementation/) | You have a cluster of 6–15 related GitHub issues (typically a P1 review-panel finding set) and want them implemented + reviewed + opened as stacked PRs by morning. Runs Phase A (PR1 tasks via subagent-driven-development) + Phase B (PR2 tasks stacked on PR1) + Phase C (PR-level code review + morning hand-off). Also covers the **plan-driven variant** (independent PRs from a written plan rather than stacked PRs from issues), **Phase 0** (stale-backlog triage + owner-ruling application, for issue clusters that aren't pre-validated), and the multi-session discipline a long run needs: never truncating a findings payload, claiming intent on a shared in-repo board so a parallel agent session doesn't duplicate your work, amending a running orchestration through a file on disk rather than the script, and an explicit line on what the run may and may not decide on its own. |

## Companion safety patterns

Cross-cutting skills that strengthen any overnight workflow. Installable independently or as part of the bundle.

| Plugin | When to use |
|---|---|
| [**large-redesign-parallel-branch-collision-audit**](plugins/large-redesign-parallel-branch-collision-audit/) | Pre-flight audit BEFORE starting a multi-PR redesign that rewrites shared files. Catches the failure mode where a long-running parallel feature branch (client-variant, staging, whitelabel) has unmerged commits touching the same files the redesign is about to rewrite — so they end up stranded with head-on conflicts that can't be cleanly cherry-picked. Adjacent to but distinct from the tracker-id audit in `overnight-multi-issue-implementation`. |
| [**subagent-review-tier-calibration-for-overnight-pr-chains**](plugins/subagent-review-tier-calibration-for-overnight-pr-chains/) | Calibrate review intensity per-PR (Tier 1 two-stage / Tier 2 combined single-agent / Tier 3 bash-only verification) in long overnight chains (10+ PRs). Specializes `superpowers:subagent-driven-development`'s review step with a decision rubric + concrete bash-verification recipe for low-risk visual-restyle PRs. |
| [**overnight-review-panel-blocked-reviewer-reads-as-clean**](plugins/overnight-review-panel-blocked-reviewer-reads-as-clean/) | Harden the review panel against a silent tool gap: code-review subagents often have **no `Bash`**, so when told to `gh pr diff`/checkout a PR they return a BLOCKED report (or review `main`, which predates the PR) — and in an unattended run a BLOCKED reviewer reads as a CLEAN one. Fix: pre-generate per-base diffs + materialize worktrees + hand explicit paths, and treat BLOCKED as not-clean. (Overnight specialization of the general `code-reviewer-subagent-no-bash-blocked-on-pr-diff`.) |
| [**schedule-poll-orchestrator-pattern**](plugins/schedule-poll-orchestrator-pattern/) | Fire-ASAP orchestration for multi-track overnight runs dispatched via scheduled triggers (RemoteTrigger / CronCreate). Replaces a fixed `t+Nh` consolidation timer with a self-rescheduling poll loop that consolidates the moment all parallel tracks report `phase: complete`, and lets a scheduled successor survive a 12–20h session end. Distinct from in-session `successor-handoff`. Also covers the loop's **standing prompt going stale** — it is written once and re-read on every wake, asserting in the present tense whatever was true when it was written. |
| [**plant-the-failure-before-you-trust-the-check**](plugins/plant-the-failure-before-you-trust-the-check/) | A check that has only ever passed has never been shown to work. Before trusting a guard, a test, an agent's "finding closed", or a cost fix, make the failure it exists to catch actually happen and watch the check go **red** — and plant it in the **production path**, not the fixture. Covers verifying a closed finding by running the negative case instead of grepping the source for the mechanism, measuring every factor of a cost before declaring it fixed, and putting a canary on the surface the leak actually uses. |
| [**cross-lane-audit-quote-verbatim-and-pin-the-commit**](plugins/cross-lane-audit-quote-verbatim-and-pin-the-commit/) | Two independent lanes auditing each other, with the other lane's wording carried **verbatim** rather than paraphrased, and every commit under audit pinned with `git update-ref refs/audit/<name> <sha>` so an amend cannot move it out from under the audit. Catches the drift where something specified precisely gets carried forward approximately — which no self-check finds, because the lane's own checks were built from its own restatement. |

The workflow plugins share the same phase structure, locked-file escape hatch, branch hygiene, and file-first discipline — use them as a set, in pairs, or individually. The companion safety patterns layer on top of any of the workflow plugins (or on standalone `subagent-driven-development` runs). For the orchestrator-takeover boundary when a track's subagent is blocked waiting on external state (CI / Cloud Build / a `gcloud` poll), see [`subagent-external-wait-orchestrator-takeover`](https://github.com/wan-huiyan/agent-traffic-control) in `agent-traffic-control`.

## Analysis toolkit

A standalone methodology bundle — not an overnight workflow, but the analytical backbone the insight-discovery workflow leans on. Installable independently.

| Plugin | When to use |
|---|---|
| [**observational-analysis-rigor**](plugins/observational-analysis-rigor/) | The validity gate for any finding from **observational** data (no randomization). A flagship 9-step protocol skill + 31 focused deep-dive skills covering leak-free point-in-time cohorts, composition/Simpson decomposition, event-anchor timing inversion, marker-vs-lever discipline, held-out-set hygiene, coverage-limited-join bias, provenance/re-derivation, and de-stale delivery to every rendered surface. Catches the *surprising-but-wrong* finding — a composition artifact, a leak, an anchor-timing inversion, or an intent marker sold as a lever. Backs `overnight-insight-discovery`'s analytical validity gate; usable in any analysis. |

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

**But verify every reviewer actually saw what it reviewed.** Many review/search subagents (`feature-dev:code-reviewer`, `voltagent-*`, `Explore`) ship **without a `Bash` tool**, so a reviewer told to `gh pr diff`/checkout a PR returns a **BLOCKED** report — or silently reviews the current checkout (often `main`, which predates the work) instead. In an unattended overnight run, **a BLOCKED reviewer reads as a CLEAN one**, and the bug it never looked at ships by morning. Pre-generate per-base diffs to files + materialize PR branches as worktrees + hand each reviewer explicit paths, and in the morning synthesis treat **BLOCKED as not-clean** (re-dispatch before counting the vote). See [`overnight-review-panel-blocked-reviewer-reads-as-clean`](plugins/overnight-review-panel-blocked-reviewer-reads-as-clean/).

> **Standing convention — review every non-trivial PR with the panel before merge.** Distinct from the *deliverable* panel above (Phase B audits a doc/deck): before squash-merging any **non-trivial code PR**, run the [`roundtable:agent-review-panel`](https://github.com/wan-huiyan/agent-review-panel) skill with **all panel agents set to `model: opus`** (the skill's enforced default) instead of (or in addition to) a single code-reviewer agent — multiple independent opus reviewers catch what one reviewer misses, gating client-facing / substantive changes. Fold/triage findings, fix, re-run if needed, THEN squash-merge. **Trivial / docs-only PRs may skip the full panel** (same non-trivial threshold). This `roundtable:`-invoked panel is the same `agent-review-panel` dependency named in §1 + [Dependencies](#dependencies). (Origin: the admissions propensity project, 2026-06-02.)

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

# Install one or more plugins
/plugin install overnight-review-client-delivery@wan-huiyan-overnight-workflows
/plugin install overnight-insight-discovery@wan-huiyan-overnight-workflows
/plugin install overnight-multi-issue-implementation@wan-huiyan-overnight-workflows
```

Or clone directly:

```bash
git clone https://github.com/wan-huiyan/overnight-workflows.git
cp -R overnight-workflows/plugins/overnight-insight-discovery ~/.claude/skills/
cp -R overnight-workflows/plugins/overnight-review-client-delivery ~/.claude/skills/
cp -R overnight-workflows/plugins/overnight-multi-issue-implementation ~/.claude/skills/
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

**Implement an issue cluster overnight** (issues → stacked PRs):

> "Run overnight-multi-issue-implementation on issues #437–#442 in the-project-repo. Two-PR shape: hardening (#438–#441) + knowledge-gap (#437, #442). Brainstorm + plan first, then subagent-driven execution, code-review subagent before merge. I'm asleep — wake up to merged PRs and follow-up issues filed."

All three plugins will ask for scoping details (target date, known-knowns table, canonical numbers, panel personas, issue cluster, PR shape) before kicking off. Morning output: a PR with the deliverable, a morning summary flagging anything that needs your attention first, and a review-panel HTML dashboard.

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
overnight-insight-discovery        →  generates the brief from scratch
                                   ↓
overnight-review-client-delivery   →  polishes a known-good brief into client-shape

overnight-multi-issue-implementation  →  ships a cluster of issues to stacked PRs
                                       (independent track — engineering, not deliverables)
```

For new insight work, start with `overnight-insight-discovery`. For existing deliverables that just need polish + QA, go straight to `overnight-review-client-delivery`. For an engineering issue cluster (typically a P1 review-panel finding set), use `overnight-multi-issue-implementation`. The first two chain naturally; the third runs as an independent track.

## Related repos

For **synchronous** end-to-end audit of a live data dashboard (one ~30–45 min round of parallel cluster agents — different shape from the autonomous overnight runs in this repo), see [`wan-huiyan/dashboard-audit-toolkit`](https://github.com/wan-huiyan/dashboard-audit-toolkit). It bundles four sister skills: a parallel-cluster-agents methodology spine, the most common fix-shape produced by the audit, single-metric depth audit, and the GitHub squash-merge gotcha when shipping the fixes.

For a **per-instance ML explainability** fix-shape — SHAP waterfall charts in production dashboards where a post-hoc calibrator (isotonic regression, Platt scaling) sits between the raw model and the displayed score — see [`wan-huiyan/shap-waterfall-calibrator-skill`](https://github.com/wan-huiyan/shap-waterfall-calibrator-skill). The "rescale to fit the chip" anti-pattern, the negative-scale-guard workaround that trades one bug for another, and the correct fix (apply the calibrator point-by-point to the cumulative-probability path so every bar lives in calibrated space).

## Origin

All three plugins encode patterns from real overnight runs. `overnight-review-client-delivery` was validated on a causal-impact project; `overnight-insight-discovery` was extracted from a university-admissions propensity project; `overnight-multi-issue-implementation` was extracted from a 12-task chatbox-hardening + knowledge-gap session on the same admissions propensity project (2026-05-08, issues #437–#442 → 2 stacked PRs merged by morning + 5 follow-ups filed). The patterns are generalized for any project that needs autonomous overnight work with quality gates.

## Version history

- **2026-08-17** — **Two new companion plugins, one new analysis deep-dive, two extended skills.** Bundle `VERSION` → **1.5.0** (it moves because plugins were added — the same reason it moved for v1.1.0; it is the bundle's own counter, not a mirror of any plugin). New: [`plant-the-failure-before-you-trust-the-check`](plugins/plant-the-failure-before-you-trust-the-check/) **v1.0.0** and [`cross-lane-audit-quote-verbatim-and-pin-the-commit`](plugins/cross-lane-audit-quote-verbatim-and-pin-the-commit/) **v1.0.0**. `observational-analysis-rigor` → **v1.4.0** (32 skills; adds `held-out-cut-tuned-on-its-own-errors-manufactures-the-lift`, and its flagship protocol skill → v1.2.0 for the step-5 index entry plus one new red-flags row). `overnight-multi-issue-implementation` → **v1.6.0**, `schedule-poll-orchestrator-pattern` → **v1.1.0**. Every figure below is from one run in which **two lanes audited each other's work**, and that pairing is itself the first lesson: **the cross-read produced 26 findings that neither lane's own checks had raised**, all of them the same shape — the other lane specified something precisely and it was carried forward approximately (a blanket clock where an **upper bound** had been granted; an open path prefix where the rule named **exact files**; "before **commit**" where they wrote "before **edit**"). No self-check finds these, because each lane's checks were built from its own restatement and agree with it perfectly; the fix is to carry the other lane's sentence verbatim and keep your interpretation beside it rather than instead of it. The same skill carries the pinning rule: `git update-ref refs/audit/<name> <sha>` before dispatching an audit, because an agent **amended the audited commit twice** during the run and the findings survived only because the commit was pinned — a rewritten commit vanishes from the branch while every branch-based check keeps reporting normally. **The other new plugin is about checks that cannot come out negative.** A guard was written, tested, and green; the code was then changed so it **stopped asking GitHub for the field** the guard checks, and the test **stayed green**, because the fixture already contained that field — in production the field would have been empty and the guard would have protected nothing while looking correct. So: plant the failure first, and plant it in the **production path**, since every artifact-side mutation left that test green. The same rule closes a second hole: an agent reported **eleven audit findings closed**, the source was grepped, every mechanism was present, and "all eleven closed" was relayed upward — then a parallel lane **executed** them, and a delete row with the bucket changed to `wrong-bucket` **passed**, a swapped-but-valid destination digest **passed**, and reducing all cross-verification to a single row **passed**. **Eight of the eleven were still open.** The presence of a mechanism is not evidence that it works, which is a narrowing of `task-framing-claims-need-subagent-grep-verify` rather than a contradiction of it: grep is the right tool for "is this claim about the code true", and the wrong one for "does this guard stop the thing it names". The plugin also carries the wrong-axis rule — a continuous-integration cost problem diagnosed **three times and wrong three times**, twice fixing cost-per-run when the problem was volume and once assuming drafts were the volume when drafts were **5.2%** of it, so `cost = runs × minutes × rate` and all three get measured before one gets fixed — and the canary rule, from a clean scan whose token sat in the **filename** while the canary searched the **body**. **The new analysis deep-dive is about the held-out set.** A round reported balanced accuracy **0.8019**, apparently over target; the cut had been chosen after inspecting which held-out items the model got wrong, it cost **9 of 39 true positives**, and chosen honestly the same signal was worth **0.7721**. Both numbers are real and only the *order of events* separates them, so an adversarial verifier has to hunt this by name rather than review the methodology in general. Its second half: a held-out set is consumable and the looks are spent silently — this run read the same set **three times** — so count the looks, state the count next to every number, and make a revived idea clear training-half validation before it may spend another one, because the training half endorses what the held-out half refuses (**+0.034** became **−0.041**). **Two lessons went into the existing overnight workflow rather than becoming sibling skills.** `overnight-multi-issue-implementation` gains **"Ask what each component does when it REFUSES"** — a promoter regenerated its input data before opening a pull request, a bad input made that regeneration throw *inside* the run, the exception was caught and the caught path returned REFUSED: no pull request, nothing red, nothing to look at, and because the promoter was the single exit every round passed through, **84 finished items** sat stalled behind one silent refusal — and **"a null result IS a complete result"**, sitting deliberately next to the existing "an un-run unit is not an inconclusive result" so the two are read together (never-ran versus ran-and-found-nothing). That line was in every brief on this run and it paid twice: one agent retired a whole family of approaches with the measurement to prove it, another measured its own assignment as not worth doing and said so. `schedule-poll-orchestrator-pattern` gains **"re-verify the standing prompt's own claims on every wake"**: the poll protocol was already idempotent and so is the prompt, which is the defect — written once at dispatch, it asserts in the present tense whatever was true then, and on this run it was wrong three times, telling agents a deploy was unauthorised **after the owner had authorised it** and not to raise a cap the owner **had asked to have raised**. A restriction goes stale in the permissive direction as often as the restrictive one, and a loop still refusing something the owner has since approved looks exactly like a loop that is behaving.
- **2026-08-10** — `overnight-review-panel-blocked-reviewer-reads-as-clean` → **v1.1.0** (SKILL + both manifests; the SKILL frontmatter had been left at 1.0.0 when the manifests went to 1.0.1 in the 2026-06 bump-the-three-unshipped-plugins fix, so this also closes that drift; bundle `VERSION` stays 1.4.0 — it is the bundle's own counter, not a mirror of this plugin's version): **the pre-generated review diff was cut with the wrong range operator.** Step 2 of the Solution told the orchestrator to run `git diff <base>..<pr-branch>` with **two dots**, which compares tip to tip — so everything the base branch gained after the PR branched renders as a **deletion the PR appears to be making**. A reviewer handed that file reports phantom findings against code the PR never touched, and in an unattended run those findings are what the morning summary is built from. GitHub's own "Files changed" tab computes the **merge-base (three-dot)** diff, and a pre-generated diff has to match what the PR actually proposes. Verified against GitHub on a real PR: GitHub reported **5 files / 219 insertions / 16 deletions**, `git diff --stat <base>...<head>` reproduced it exactly, and the two-dot form returned a different file set with deletions that do not exist. **The fix ships two guards, not just the extra dot** — because three dots need a fork point, and across **unrelated histories** `git diff A...B` exits **128 with empty output**. That hands the reviewer a zero-byte file it reads as "nothing to review": a blocked reviewer reporting clean, which is the precise bug this plugin is named for. So the recipe now gates on `git merge-base` before the diff and `test -s` after it, and the Verification section requires every diff file to be non-empty and to match the PR's Files-changed counts before dispatch. A new note keeps this from reading as a contradiction of its sibling: `overnight-multi-issue-implementation`'s stale-base audit deliberately uses **two** dots, because it asks what your branch destroys relative to `main` *as it stands now* — a different question, where the merge base would hide exactly the files `main` gained after you branched. Pick the form from the question, not from habit.
- **2026-08-07** — `overnight-multi-issue-implementation` → **v1.4.0** (SKILL + both manifests; bundle `VERSION` already reads 1.4.0 from the 2026-08-06 release below and is unchanged — it is the bundle's own counter, not a mirror of this plugin's): adds **"Pre-flight: stale-base audit — what your OWN branch deletes"**, the inward mirror of the parallel-branch collision audit it now sits beside. That audit looks outward at branches that might conflict with you; this one looks at the branch you are about to merge. **The evidence is one incident.** A pull request merged from a branch created before several other pull requests landed and never rebased; its conflict resolution took its own side across the whole tree — **59 files, 1,891 insertions, 5,081 deletions** — reverting **11 files and 15 tracker entries belonging to three other sessions**, plus a function two surviving files still imported. Nothing failed: no conflict, green PR, schema validator passed, site still rendered. All three sessions had finished a wrap-up that morning and their work *was* on `main`, for between 6 and 90 minutes; the fastest discovery took 16 minutes 36 seconds and was an accident. The merged content was legitimate and had to stand, so `git revert` was the wrong tool — it would have destroyed everything merged after it. **What the section adds beyond "rebase before merge":** a pre-merge recipe that reads the DELETIONS, plus a total-deletions threshold set low enough to force a read — and it uses **`git diff origin/main..HEAD` with two dots, not three**, which is a correction the drafting turned up rather than a restatement. The three-dot form everyone reaches for diffs from the *merge base*, so on a branch that never took `main`'s newer commits a file added to `main` after the branch point is absent from both sides and reports as **no change at all**; verified on a two-commit synthetic repo where three-dot prints nothing and two-dot prints the file. Rebase first — the order is load-bearing — and note that a *plain* merge of a stale branch is harmless (git keeps what only `main` has); the damage needs the branch's tree to win wholesale, via a merge of `main` into the branch resolved to its own side, `-X ours`, or a squash of the branch tree. Also an audit-after recipe built on the finding most checklists miss — **`git cat-file -e` is the weak check**, because a file can be present with its contents rolled back and no existence check, id check or validator will say a word. Audit instead for a marker your change ADDED. The worked example is measured rather than argued: a page of seven interactive widgets whose option lists are single-quoted HTML attributes, three of them holding an apostrophe behind a one-character `&#39;`. Roll that escape back on the fourth widget and the file exists, the HTML is valid, all seven widgets are in the markup, every committed check passes — and four of the seven are silently dead, because the truncated attribute throws inside the one `forEach` that builds them all. Loading the real page with each escape rolled back in turn gives three working, or **none** if the first widget is the one broken. Recovery is a splice-forward (`git show <sha>:<path>`), never a revert, with the three things that bite during one: restore what the restored file imports, re-check state before each restore because parallel sessions are repairing at the same time, and re-measure any figure a restored file carries rather than reconciling two versions by eye.
- **2026-08-06** — `overnight-insight-discovery` → **v1.2.0** and `schedule-poll-orchestrator-pattern` → **v1.0.1** (both manifests each + bundle `VERSION` → 1.4.0): fixes the single-root skill-path bug in two places. A skill installed as a **plugin** lives at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, not at `~/.claude/skills/<name>/`, so anything that reaches a skill through the `~/.claude/skills/` root alone misses on a plugin install. **The one with teeth**: `overnight-insight-discovery`'s Phase 0.Y toolchain pre-flight decided whether the skill was installed with a single `test -f ~/.claude/skills/overnight-insight-discovery/SKILL.md`. On a plugin install that test fails, and the failure path is a tap-out with `[ENV_BLOCKER]` reporting no skill tree — a failed lookup reported as an install-state finding. It now probes all three roots (`$CLAUDE_PLUGIN_ROOT`, `~/.claude/skills/`, then the plugin cache), ranks cache hits on the **version** segment alone rather than whole-path `sort -V` (the marketplace segment sorts first, so `aaa-mkt/2.5.0` would otherwise lose to `zzz-mkt/1.0.0`), uses `find` instead of a glob (zsh's `nomatch` fails a non-matching glob before `2>/dev/null` applies), and prints "not found — tried <the three paths>" rather than anything that reads as "not installed". **The cosmetic ones**: three dead see-also links in `schedule-poll-orchestrator-pattern` pointed at `~/.claude/skills/<name>/SKILL.md` files a reader on a plugin install cannot open — now plain skill names with a GitHub URL where the source repo is known — and one `~/.claude/skills/`-rooted self-reference in `overnight-insight-discovery`'s v1.3.2 changelog entry. **Deliberately unchanged**: every `~/.claude/skills/**` mention in § "Autonomous-safe skill edits" and its Phase G summary. Those are the path patterns that fire a sensitive-file permission prompt in Claude Code, which is a fact about the prompt system and not about where this skill is installed; broadening them would break the contract they encode. `CLAUDE_PLUGIN_ROOT` alone is not the fix — it is frequently unset in the shell a step actually runs in, and it points at the running plugin's own root, so it can never reach a sibling plugin.
- **2026-08-06** — `overnight-multi-issue-implementation` → **v1.3.1** (SKILL + both manifests + bundle `VERSION`): drops an unsourceable figure from the entry below. The "never truncate a findings payload" lesson described the lost finding as sitting in a "553-line pre-registration". That was true of the document the reviewer read; five blocking findings were then fixed on the branch before it merged, roughly doubling it, and it now stands at about 1,100 lines — a figure with no vintage attached, in a document the skill's own readers cannot re-derive it from. The length was decoration; "a pre-registration that had no power statement anywhere" carries the whole point, so the count is gone rather than dated. The same audit **retracted a rate**: "about one in four" is no longer claimed, because the host repo reopened its own count (a sixth instance surfaced, and two of the five were documents written from scratch). It now reads "common enough to budget a round for, not a measured rate". Everything else checked out against the source — the six-findings/five-arrived split, the ±0.03-versus-0.17 margin behind "about five times finer", and the 269 → 314 test counts are all recorded in the run's own handoffs. This is the skill's own "a fix ships a fresh instance of the defect it repairs" rule firing on the release that introduced it.
- **2026-08-06** — `overnight-multi-issue-implementation` → **v1.3.0** (SKILL + both manifests + bundle `VERSION`, which had drifted a patch behind): seven lessons from a single overnight run of 17 items — 20 merged PRs, 4 orchestrated workflows, 91 subagents, ~11 hours. **Never `.slice()` a findings payload** (three reviewers returned six critical findings; the merging agent received five, and every visible signal still said the gate had worked — make the actor count what it received against what it answered, and keep the run journal as the recovery path). **Coordinating with sessions you do not control**: claim your intent and your file list on a shared in-repo board before you start, append rather than replace, and don't take the claim down while your PRs are open. **Amend a running orchestration through a file on disk, not the script** — editing the script changes every agent prompt and a resume then re-runs completed work instead of replaying it from cache. A **third kind of collision** neither pre-flight audit can see (the same piece of work under two names, in a live session's uncommitted tree). **Baseline numbers in your own brief go stale mid-run** — measure, never quote. **What an autonomous run may and may not decide** (an assumption is a default, not a ruling; for pre-registered questions disclose rather than compute; an un-run unit is "no result", not "inconclusive"; production changes only in the reverting direction and only on unanimous authorisation from the run's own reviewers, with anything that is not a revert still waiting for a person; merging is a separate grant from changing production). And a **verbatim line for every reviewer prompt** — "Check whether this change ships a fresh instance of the defect it repairs." — which caught five of the twenty merged PRs, every one by re-deriving a number rather than by reading the diff.
- **2026-07-17** — `overnight-multi-issue-implementation` → **v1.2.0** (SKILL + manifests, fixing a manifest-version drift): adds **Phase 0 — backlog triage + owner-ruling application** for unvalidated issue clusters (triage biased against dismissal with adversarial verification of dismissals only; owner cut-line ratification via an interactive review page; rulings baked as greppable issue comments before any build; decision-session / build-session split with a wave-ordered kickoff prompt; follow-up ruling rounds handled additively; successor-before-close sequencing). Also documents that the close-keyword issue trap fires from **docs-only planning PR bodies** ("then close #N" in a kickoff-prompt addendum closes the live tracker on merge). Extracted from a real large-backlog triage-and-rulings run. Cross-links the new [`interactive-feedback-report`](https://github.com/wan-huiyan/interactive-feedback-report) skill.
- **2026-06-02** — Standing convention added: review every **non-trivial PR** with the `roundtable:agent-review-panel` skill (all agents `model: opus`) before squash-merge; trivial/docs-only PRs may skip the full panel. Reconciled with the per-PR tier rubric (the panel is the heavyweight tier; single-reviewer tiers remain for low-risk PRs). `overnight-multi-issue-implementation` SKILL → v1.1.1, `subagent-review-tier-calibration-for-overnight-pr-chains` SKILL → v1.0.1.
- **v1.1.0** (2026-05-08) — Adds `overnight-multi-issue-implementation` for the engineering-side overnight pattern (issues → stacked PRs). README updated to reflect three plugins; install + compose sections expanded.
- **v1.0.0** (2026-04-17) — Initial release bundling two plugins. `overnight-review-client-delivery` was previously a standalone skill; this bundle adds the insight-discovery sibling and unifies the shared patterns (locked-file escape hatch, branch hygiene, file-first successor handoff, archive-and-regenerate).

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
