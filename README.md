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
| [**overnight-multi-issue-implementation**](plugins/overnight-multi-issue-implementation/) | You have either a cluster of 6–15 related GitHub issues or a large mixed live queue and want the executable work implemented, reviewed, and integrated by morning. The issue path supports stacked PRs; the large-queue reference adds exhaustive source coverage, split stale/owner/autonomous classifications, granular authority, file ownership, durable recovery, immutable base/head review, and serial integration. |

## Companion safety patterns

Cross-cutting skills that strengthen any overnight workflow. Installable independently or as part of the bundle.

| Plugin | When to use |
|---|---|
| [**large-redesign-parallel-branch-collision-audit**](plugins/large-redesign-parallel-branch-collision-audit/) | Pre-flight audit BEFORE starting a multi-PR redesign that rewrites shared files. Catches the failure mode where a long-running parallel feature branch (client-variant, staging, whitelabel) has unmerged commits touching the same files the redesign is about to rewrite — so they end up stranded with head-on conflicts that can't be cleanly cherry-picked. Adjacent to but distinct from the tracker-id audit in `overnight-multi-issue-implementation`. |
| [**subagent-review-tier-calibration-for-overnight-pr-chains**](plugins/subagent-review-tier-calibration-for-overnight-pr-chains/) | Calibrate review intensity per-PR (Tier 1 two-stage / Tier 2 combined single-agent / Tier 3 bash-only verification) in long overnight chains (10+ PRs). Specializes `superpowers:subagent-driven-development`'s review step with a decision rubric + concrete bash-verification recipe for low-risk visual-restyle PRs. |
| [**overnight-review-panel-blocked-reviewer-reads-as-clean**](plugins/overnight-review-panel-blocked-reviewer-reads-as-clean/) | Harden the review panel against a silent tool gap: code-review subagents often have **no `Bash`**, so when told to `gh pr diff`/checkout a PR they return a BLOCKED report (or review `main`, which predates the PR) — and in an unattended run a BLOCKED reviewer reads as a CLEAN one. Fix: pre-generate per-base diffs + materialize worktrees + hand explicit paths, and treat BLOCKED as not-clean. (Overnight specialization of the general `code-reviewer-subagent-no-bash-blocked-on-pr-diff`.) |
| [**schedule-poll-orchestrator-pattern**](plugins/schedule-poll-orchestrator-pattern/) | Fire-ASAP orchestration for multi-track overnight runs dispatched via scheduled triggers (RemoteTrigger / CronCreate). Replaces a fixed `t+Nh` consolidation timer with a self-rescheduling poll loop that consolidates the moment all parallel tracks report `phase: complete`, and lets a scheduled successor survive a 12–20h session end. Distinct from in-session `successor-handoff`. |

The workflow plugins share authority, branch hygiene, review, and file-first recovery principles, but not one phase topology. In particular, a large mixed queue uses the [serial large live-queue procedure](plugins/overnight-multi-issue-implementation/references/large-live-queue-orchestration.md), not the issue cluster's PR1/PR2 graph. Use the plugins as a set, in pairs, or individually. The companion safety patterns layer on top of any of the workflow plugins (or on standalone `subagent-driven-development` runs). For the orchestrator-takeover boundary when a track's subagent is blocked waiting on external state (CI / Cloud Build / a `gcloud` poll), see [`subagent-external-wait-orchestrator-takeover`](https://github.com/wan-huiyan/agent-traffic-control) in `agent-traffic-control`.

## Analysis toolkit

A standalone methodology bundle — not an overnight workflow, but the analytical backbone the insight-discovery workflow leans on. Installable independently.

| Plugin | When to use |
|---|---|
| [**observational-analysis-rigor**](plugins/observational-analysis-rigor/) | The validity gate for any finding from **observational** data (no randomization). A flagship 9-step protocol skill + 30 focused deep-dive skills covering leak-free point-in-time cohorts, composition/Simpson decomposition, event-anchor timing inversion, marker-vs-lever discipline, coverage-limited-join bias, provenance/re-derivation, and de-stale delivery to every rendered surface. Catches the *surprising-but-wrong* finding — a composition artifact, a leak, an anchor-timing inversion, or an intent marker sold as a lever. Backs `overnight-insight-discovery`'s analytical validity gate; usable in any analysis. |

## Why use these

Overnight autonomous runs are seductive but brittle. The typical failure modes:

- **Hallucinated conclusions.** The model "finds" patterns that are restatements of known features, or narrates trivial tautologies as surprising.
- **Factual errors ship to the client.** A single reviewer (you, sleep-deprived in the morning) misses a wrong BSTS CI, a decomposition table with inverted signs, a mislabelled cohort.
- **Stale content dressed as fresh.** Author adds an "archive banner" at the top + updates the headline, leaves the body with old numbers — readers can't tell which parts are current.
- **Context-window blowup.** A 6-hour autonomous run fills the model's window; the session compresses lossy, then drifts.
- **Parallel session commit-dropping.** Two agents on the same branch silently rebase each other's commits into oblivion.

These plugins encode the hard-won patterns that fix each of these — extracted from real overnight runs that caught real P0 errors before they reached real clients.

## Core patterns

### 1. Multi-agent review panel

The workflows do not trust the author (or the track) to self-review. A panel of 4–8 specialized reviewers runs on the relevant deliverable, analysis, plan, or code change — data-scientist, data-analyst, scientific-critical-thinker, client-trust-evaluator, compliance-auditor, qa-expert. A Supreme Judge arbitrates. Dependency: [`agent-review-panel`](https://github.com/wan-huiyan/agent-review-panel).

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

**Critical gotcha:** parallel Claude sessions on the same repo can silently drop each other's commits via rebase. Use `feature/session-NN-claude-A` vs `feature/session-NN-claude-B`. Checkpoint every commit locally; when push is explicitly authorized, push after each commit. Otherwise retain the unique local branch, journal, and worktree and report the pending push. Treat `git reflog` as the safety net.

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

### Canonical Codex umbrella

The complete 44-file mapping is recorded in
[`codex/overnight-workflows/install-manifest.json`](codex/overnight-workflows/install-manifest.json).
The groups are:

| Group | Count | Installed layout |
|---|---:|---|
| Codex router and interface metadata | 2 | `SKILL.md`, `agents/openai.yaml` |
| Routed workflow documents | 7 | `references/workflows/<plugin>/WORKFLOW.md` |
| Workflow support resources | 35 | `references/workflows/<plugin>/{references,assets,scripts}/...` |

Thirty-two support resources are derived mechanically from every file under
each plugin's `references/`, `assets/`, and `scripts/` directories. The other
three are the read-only panel validator, its fixtures, and its small shared
inventory codec, installed beside the multi-issue workflow; the mutating
publisher and controller-side finalization-manifest writer are deliberately
excluded. Plugin manifests under `.claude-plugin/` are also excluded.
Per-workflow directories prevent name collisions and preserve relative
dependencies. Copy all 44 canonical sources to their exact installed paths,
compare all 44 source/install SHA-256 digests, and validate
that every local Markdown dependency resolves in both layouts.

The 7 tracked navigation stubs live at
`codex/overnight-workflows/references/workflows/<plugin>/WORKFLOW.md`. They are
repository navigation files, not install sources; validate that each resolves
to its canonical plugin `SKILL.md`. The installed copies also use
`WORKFLOW.md`, so Codex discovers only the umbrella root `SKILL.md`.

Manifest schema 3 records a SHA-256 for every canonical source plus the framed
`overnight-workflows-install-input-v1` aggregate over mapping order, source and
destination names, and file bytes. Publication evidence uses
`sha256-size-path-v1`: `sha256<TAB>bytes<TAB>relative POSIX path<LF>`, sorted by
raw UTF-8 path bytes with exactly one final LF.

`scripts/check_large_queue_guidance.py --self-test` is the nonzero state,
package, and exact prompt-class-to-route contract gate. The required local
release command is
`scripts/check_large_queue_guidance.py --self-test --release-gate /path/to/codex`.
It fails if the pinned loader is unavailable, calls Codex 0.147.0 app-server
`skills/list(forceReload=true)`, requires only the root `SKILL.md`, retrieves
each positive and negative route contract from that loaded umbrella, and proves
a nested child `SKILL.md` becomes recursively exposed. CI uses
`--ci-loader-gate`, which skips only after emitting an explicit unavailable or
version-mismatch environment classification. The committed word-overlap scorer
remains diagnostic. Implicit model route selection is **UNCHECKED** because no
pinned-model evaluation was authorized for this release.

### Safe canonical publication

Use `scripts/publish_codex_install.py` to prepare an immutable-commit candidate,
reserve a package-wide writer lock, and publish only under a current external
reader-quiescence attestation. The publisher validates the recorded claim; it
does not discover readers or prove the unknowable state of every process. The
schema-2 JSON record has these exact fields (undeclared fields fail):

```text
schema_version: 2
record_type: external_reader_quiescence_attestation
operation_id, authorized_by
publisher_validation_scope: publisher-validates-recorded-external-claim-not-unknowable-world-truth
maintenance_window: {id, starts_at, ends_at}
known_reader_inventory: {
  scope: all-known-codex-skill-readers,
  method, evidence_reference, inventory_complete: true,
  known_reader_count: nonnegative integer,
  known_active_reader_count: 0,
  unknown_reader_policy: STOP_IF_UNKNOWN,
  unknown_reader_status: NONE_OBSERVED,
  checked_at, expires_at
}
controller: {id, state: ACTIVE, owner: {host, pid, process_start_identity}}
```

All text identities are normalized and control-free; `pid` is positive and
reader counts are nonnegative. Timestamps use exact UTC
`YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. The check and maintenance window must nest in
order, must each be no longer than 15 minutes, and use an exclusive
`expires_at`. Reserve validates the record twice. The publisher then re-reads,
re-hashes, and revalidates it as the final action before every atomic exchange.
A changed, stale, incomplete, active-reader, or unknown-reader claim blocks the
exchange.

Publication stages outside skill-discovery roots, retains the prior generation,
and requires Darwin whole-directory `RENAME_SWAP`; an unavailable exchange is
`UNCHECKED`, with no per-file live fallback. Both staged and live generations
run `check_large_queue_guidance.py --installed-root ... --self-test --json` and
bind every named mutation outcome. `prepare` requires
`--expected-live-source-commit`: the live tree must exactly match that commit's
installed paths and bytes, independently of the candidate paths and bytes. This
permits an exact nested `SKILL.md` to `WORKFLOW.md` migration without treating
the removed path as drift. Mutating `recover` requires both a durable
`--takeover-authorization` proving the reserved owner is `STOPPED` or
`SUPERSEDED` and a fresh `--reader-quiescence-record`. Fresh means its byte
digest differs from every earlier attestation and its `checked_at` is strictly
later. Renewal records preserve the precise binding time inside that claim's
current bounds and no later than any exchange that cites them. They are
append-linked to the exact generation, reservation, takeover authorization,
recovery action, and prior renewal. Read-only `recover --action inspect`
rejects a reader record. Receipts bind both source commits/trees, both exact
identities, the named live mutation outcome, the recovery authorization, and
the ordered renewal and per-exchange attestation histories.

`scripts/finalization_manifest.py` is the one grammar, validator, and locked
appender for controller and publisher finalization records. It requires exact
generic schema-2 rows, a single canonical header, contiguous sequences, stable
finalization/writer identities, strict UTC timestamps, exact record-specific
fields, and one final LF. Unknown types, undeclared fields, duplicate
identities, partial rows, links, and changed inodes fail closed. The controller
must use its `init`, `append`, and `seal-prefix` commands instead of building
JSONL directly. New source-review rows bind an arbitrary, exact repository-role
map instead of project names. A source review may also declare one exact
`required_pre_dispatch_validation` with its artifact kind, absolute path,
SHA-256, top-level status field, and required `PASS` value. Before the dispatch
seal, an `artifact_registered` row must bind the same artifact and review with
state `PASS`; the writer rereads the JSON and rejects absence, byte drift,
invalidation, non-PASS status, or a different review ID. Generic-v2 source rows
that omit the optional field retain their explicit ungated compatibility
meaning; omission must not bypass a repository-required gate. The publisher
imports the same parser and appender. The prior project-specific schema-1
grammar remains available only to reproduce archived manifests; it cannot be
appended or sealed.

Generic-v2 `raw_input_registered` rows are restricted to the
`postpublication-installed-snapshot` boundary. A
`prepublication-source-and-staged-snapshot` dispatch must use
`source_review_input_registered`, so it cannot select the generic raw-input row
to evade a declared source-validation artifact. The validation artifact JSON
is decoded through the strict decoder before its review ID and configured PASS
field are checked.

Nine modules that decode JSON someone else produced route it through one
sanctioned decoder each, and each refuses four ways `json.loads` returns a
confident answer another conforming parser would not: a repeated key, the Python
constants `NaN` / `Infinity` / `-Infinity`, a number too large for a float such
as `1e400` (ordinary JSON number syntax, which Python resolves to the same
infinity and re-encodes as the literal `Infinity` the decoder refuses), and an
unpaired surrogate escape such as `\ud800` (Python keeps the lone code point,
Go's encoding/json substitutes U+FFFD, so one document decodes to two different
strings). A surrogate pair is ordinary non-BMP text and stays accepted.

Nine, not every module: one further trust boundary was found and deliberately
not repaired. `plugins/overnight-insight-discovery/scripts/bq_budget.py` reads a
shared spend ledger other processes write and gates a real spend decision on it,
so it is a boundary — but its ledger row names a released `source_commit`, and
repairing it changes a released plugin's payload bytes and forces a version bump
through `plugin.json`, the marketplace manifest, the SKILL banner and the README.
That is a publishing act, left for the owner. It is recorded, with that reasoning,
in `PLAIN_JSON_DECODE_DEFERRED` in `scripts/test_finalization_manifest.py`, and a
test requires the entry to name a file that still decodes JSON, so the deferral
cannot go quiet.

A generic-v2 source review can seal `judgment` only when its lifecycle joins up.
Challenge responses must come from exactly the roles that reported, one per
registered reviewer, and the judge's role and artifact must be distinct from
every report and challenge. Every reported finding ID must resolve exactly once
to an accepted, merged, or rejected disposition; merge targets must themselves
be accepted, the accepted count must fall inside the declared deduplicated
range, and the judge's structured receipt must carry that resolution. Every
report, challenge, judge rationale, structured receipt, and controller payload
must carry a create-once publication receipt minted by
`publish-review-artifact`, and the seal rereads each public name to confirm it
still resolves to the inode the publisher created. Only the literal
`SOURCE_ACCEPTED` state and `SOURCE_GUIDANCE_ACCEPTED` status are accepted; the
obsolete `JUDGED`/`REVIEWED` aliases are readable only in the schema-1 grammar.
Rows written without a finding inventory or a publication receipt stay readable
and appendable, so an in-flight manifest is never bricked, but they can never
seal `judgment`. The receipt proves the publication method, the continuity of
the published inode, and the current bytes; it is not a signature and does not
authenticate the publisher, which remains the launcher's job.

Every rule in the paragraph above is re-run over the sealed prefix each time the
manifest is read, so a rule added later would refuse evidence that was validly
sealed earlier — and the seal, its snapshot and its receipt are all create-once,
so there would be no way to repair it. Each phase seal therefore records the
contract it was minted under, in `review_contract`, and re-validation applies
that contract's rules: a seal written before the finding-resolution and
publication joins existed keeps validating under the rules that gated it.
Minting a new seal always applies the current contract, so nothing weakens going
forward. The marker cannot be deleted to buy the older rules, because the
create-once prefix receipt carries the same value and the two are compared; a
seal naming any other contract string is refused outright rather than treated as
an older one. The one exception, stated plainly: a review whose `judgment` seal
predates the contract has its judge receipt checked against that era's eleven
fields rather than the sixteen, because a create-once receipt cannot grow the
five resolution fields. Every other reservation check runs in both eras.

A prepared generation that a `source_review_input_registered` row names —
joined by both its `prepared_generation_id` and its `prepare_receipt_sha256` —
cannot be reserved until that same review accepted it. Reservation requires the
literal `state: "SOURCE_ACCEPTED"` and `source_guidance_status:
"SOURCE_GUIDANCE_ACCEPTED"` row, its exact `ACCEPT` structured judge receipt,
and the registered `judgment` prefix seal for the same review and raw-input
inventory. A dispatch-only prefix reserves nothing. The reviewed
`installed_source` head must equal the prepare receipt's own source commit, so
one review's judgment seal cannot authorize another candidate. Matching one
join field and not the other is refused rather than skipped, and the rule is
enforced by the manifest itself, so it re-applies on every later read.

`finalize` writes terminal validation evidence but deliberately retains the
package-wide `package.lock` through panel review. While that reservation is
active, exactly one inventory chain must run in order: `dispatch`, then
`judgment`, then `acceptance`. Each schema-2 receipt records the exact live
`sha256-size-path-v1` inventory and binds the preceding receipt plus the prior
and current bounded finalization-manifest prefixes. Before each inventory
phase, `seal-prefix` writes create-once prefix and receipt files, then appends a
`manifest_prefix_registered` row that binds the review ID and sealed raw-input
inventory digest. The publisher requires that registration to be the current
last row and revalidates its files and bytes. Skipped, reordered, duplicate, or
cross-review artifacts fail. An exact same-path replay of an already committed
phase returns its existing receipt; a crash retry may reuse only the same phase
and exact output path recorded by the durable pending intent. Only an explicit
`accept` command with the complete chain's acceptance receipt, reviewer
identity, reason, and unchanged acceptance prefix appends panel acceptance and
releases the reservation.

```bash
python3 scripts/finalization_manifest.py init --manifest /abs/finalization.jsonl --finalization-id FINALIZATION --writer-controller-id CONTROLLER
python3 scripts/finalization_manifest.py seal-prefix --manifest /abs/finalization.jsonl --writer-controller-id CONTROLLER --review-id REVIEW --phase dispatch --raw-input-inventory-sha256 RAW_SHA256 --prefix-output /abs/evidence/dispatch-prefix.jsonl --receipt-output /abs/evidence/dispatch-prefix.json
python3 scripts/finalization_manifest.py publish-review-artifact --review-root /abs/evidence --relative-output reports/report-1.md --expected-sha256 ARTIFACT_SHA256 --expected-byte-count BYTES --receipt-output /abs/evidence/receipts/report-1.json < /abs/draft/report-1.md
python3 scripts/finalization_manifest.py judgment-input-identity --manifest /abs/finalization.jsonl
python3 scripts/publish_codex_install.py inventory --operation OP --state-root /abs/state --lock /abs/state/package.lock --phase dispatch --output /abs/evidence/dispatch-live.json
# Repeat seal-prefix and inventory with distinct outputs for judgment, then acceptance.
python3 scripts/publish_codex_install.py inventory --operation OP --state-root /abs/state --lock /abs/state/package.lock --phase judgment --output /abs/evidence/judgment-live.json
python3 scripts/publish_codex_install.py inventory --operation OP --state-root /abs/state --lock /abs/state/package.lock --phase acceptance --output /abs/evidence/acceptance-live.json
python3 scripts/publish_codex_install.py accept --operation OP --state-root /abs/state --lock /abs/state/package.lock --finalization-manifest /abs/finalization.jsonl --acceptance-inventory-receipt /abs/evidence/acceptance-live.json --accepted-by REVIEWER --acceptance-reason "panel accepted"
```

Do not publish while other Codex sessions may be reading the package. The test
suite uses a fake exchange adapter for failure injection. On Darwin it also
uses real `RENAME_SWAP` only between temporary directories, proving that a
reader holding an opened old directory descriptor sees the complete old
generation while a new path reader sees the complete new one. It never
performs a live installation; unknown readers still require recorded
quiescence or maintenance authorization. This is a safe temporary-directory
control, not a claim about concurrent readers of a live installation. Run:

```bash
python3 -m unittest scripts/test_finalization_manifest.py -v
python3 -m unittest scripts/test_publish_codex_install.py -v
python3 scripts/publish_codex_install.py self-test
```

Panel dispatches use generic schema-3 `panel_input` records with review boundary
`prepublication-source-and-staged-snapshot`. The `repository_roles` object is
an exact bijection—a one-to-one mapping covering every `repositories` key—from
semantic roles to repository keys and must include `installed_source`, which
identifies the one reviewed repository that
matches the installed snapshot's source repository, commit, and tree.
Controllers derive finalization `repository_heads` mechanically from that
validated map: each role keeps its exact repository key and the corresponding
repository's `head_sha`. New producers must not emit legacy schema-2 panel
rows. Every repository input
includes `target_ref`, its full `target_ref_sha_at_dispatch`, the literal
deterministic `git diff` argv array, immutable target/merge-base/head/tree IDs,
and the saved diff digest. The installed evidence snapshot includes the exact
inventory digest/count/bytes, generation ID equal to that digest, source
commit/tree, and install manifest digest. The `prepare` join must reproduce the
exact receipt and state bytes, their operation/source/manifest/generation
identities, state `PREPARED`, and mutation outcome
`NO_LIVE_MUTATION_PREPARED`; staged validation must be the immutable-source
checker invocation with zero exit, empty stderr, and all named outcomes
`PASS`. Scope is exact: source guidance is `NOT_REVIEWED`, the live
installation is `UNCHANGED_PREDECESSOR`, later publication, review, and
acceptance stages are `NOT_RUN`, and reader/model checks are `UNCHECKED`. A
record cannot claim live publication in this boundary.
The canonical `scripts/validate_panel_inputs.py` and the exact copy bundled
under the installed multi-issue workflow reproduce those joins and reject
omissions or drift. Resolve the installed copy from the active umbrella
`SKILL.md` loader path; do not guess among discovery caches. Run it with
`--self-test --manifest panel.jsonl` before dispatch and acceptance. The bundle
includes only that read-only validator, its fixtures, and the inventory codec,
not `publish_codex_install.py`.

## Dependencies

The workflow plugins use these dependencies where their procedures call for them:

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

Each plugin asks for the scoping details relevant to its workflow before starting. Morning delivery follows the recorded authority grants: retain reviewed local commits when pull-request authority is absent; open or update a pull request only when that grant exists; and merge only under a separate explicit merge grant, including a recorded merge-on-green grant whose named gates all passed.

## What you get by morning

- The finished deliverable (Markdown + HTML, client-ready)
- A `morning_summary.md` that flags the ONE thing to look at first (P0 fixes to locked files, capped loops, unresolved P1s)
- A review-panel HTML dashboard with per-round scores and persona-by-persona verdicts
- A reviewed local commit when pull-request authority is absent. When pull-request authority exists but merge authority does not, a PR to `main` carries a **DO NOT MERGE** banner. With an explicit merge-on-green grant, the workflow may merge only after every named green and review gate passes, then reports the observed merge.
- A `workflow_learnings.md` capturing concrete recommendations for the next run
- Full traceability: every commit per phase, every BQ query, every finding that DIDN'T make the brief (and why)

## Limitations

- These plugins are structured for **8 hour overnight windows**. Sub-hour sessions are overkill; multi-day projects need further decomposition.
- They assume a BigQuery-style data warehouse with read access + at least one scratch dataset. Snowflake / Redshift / Postgres will work but the budget wrapper and SHAP compute scripts need adaptation.
- No plugin may execute trades, move money, push to production, or run migrations without explicit human authorization.
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

- **2026-08-10** — `overnight-multi-issue-implementation` → **v1.5.4**:
  removes a timing false negative in the authenticated prepublication check.
  The production-event mutation child now has a named 60-second budget, its
  panel-validator parent has 120 seconds and a 60-second margin, and the outer
  staged-checker replay has 300 seconds. That outer budget leaves another 60
  seconds for checker controls and at least 120 seconds of headroom.
  Deterministic controls capture all three subprocess budgets, reject old or
  reduced-budget mutants, preserve direct and held-file-descriptor launches,
  and surface a child's JSON `FAIL`, error list, or timeout in the outer
  diagnostics. The 44-file package remains unpublished and bundle `VERSION`
  remains **1.5.0**.
- **2026-08-09** — `overnight-multi-issue-implementation` → **v1.5.3**:
  generic raw-input rows are now postpublication-only, so prepublication
  source/staged dispatch cannot bypass its source-input validation gate. The
  registered PASS receipt rejects duplicate JSON keys, including conflicting
  review IDs or status values. Positive postpublication and frozen legacy-v1
  behavior remain covered. The 44-file package remains unpublished and bundle
  `VERSION` remains **1.5.0**.
- **2026-08-09** — `overnight-multi-issue-implementation` → **v1.5.2**:
  a source-review input can require one exact pre-dispatch validation artifact.
  The generic-v2 dispatch seal now joins that declaration to a same-review
  `artifact_registered` PASS row, rereads the JSON receipt, and fails on a
  missing, changed, invalidated, non-PASS, or cross-review artifact. Existing
  generic-v2 reviews that declare no gate keep their prior behavior, and frozen
  legacy-v1 evidence remains read-only. The source-only 44-file package is not
  published by this change; bundle `VERSION` remains **1.5.0**.
- **2026-08-09** — `overnight-multi-issue-implementation` → **v1.5.1**:
  new review producers use generic schema-3 panel inputs with an exact
  one-to-one repository-role map and mechanically derived generic-v2
  finalization heads. Archived schema-2 panel receipts may retain a distinct
  original producer path only when their digest and available original bytes
  equal the sealed copy. Frozen legacy-v1 finalization prefixes and receipts
  remain reproducible read inputs, while all new append and seal operations
  remain v2-only. Bundle `VERSION` stays **1.5.0** because that already-unreleased
  bundle counter is independent of this plugin patch.
- **2026-08-09** — `overnight-review-client-delivery` → **v1.0.3** and
  `schedule-poll-orchestrator-pattern` → **v1.0.4**: the final-byte gate now
  versions its durable contract and binds the package root plus every directory,
  including empty directories, so create/delete membership changes invalidate
  approval. Schedule-poll now versions and binds its full run configuration,
  status path, journal path, and one journal-derived lock; alternate status
  files, control-path aliases, duplicate claims, and incomplete initialization
  recovery fail closed. The shared finalization writer now creates generic
  schema-2 manifests with arbitrary repository role/key identities while
  retaining the project-specific schema-1 reader only for archived evidence.
  The panel validator similarly writes/validates generic schema-3 role maps,
  removes the staged-result process cache, and strengthens receipt and event
  controls. The 44-file source package remains source-only and is not published
  by these changes. The release ledger pins the preceding payloads to commit
  `e99eefd4` and binds these two new patch payloads.
- **2026-08-09** — `overnight-review-client-delivery` → **v1.0.2** and
  `schedule-poll-orchestrator-pattern` → **v1.0.3**: routing is explicitly
  non-authorizing, and commit, push, pull-request, merge, deploy, network,
  paid-call, and other external-write grants remain separate at the action
  point. Client delivery now requires an independent review of the frozen
  final bytes after all edits; any later byte change invalidates that approval.
  Schedule-poll replaces its prose sketch with a locked, atomic, idempotent
  state-machine helper, stable trigger and operation IDs, crash repair, a hard
  ceiling, and a separately authorized pull-request claim. A single executable
  finalization-manifest grammar/appender now serves controller and publisher,
  with create-once phase-prefix receipts registered before each publisher
  inventory. The panel-input validator accepts only the exact prepublication
  source-and-staged boundary and reproduces its prepare/state/checker joins and
  scope. The Codex umbrella closure is now **44 files**, including the
  schedule-poll and client-delivery gate helpers while continuing to exclude
  mutating publication tools; focused finalization, final-byte, action-authority,
  and schedule mutation suites are CI gates. The release
  ledger pins the preceding candidate payloads to commit `40361a7f` and binds
  both new patch payloads. Bundle `VERSION` remains **1.5.0** because the bundle
  is still unreleased. These repository changes do not publish a live package.
- **2026-08-09** — `overnight-insight-discovery` plugin → **v1.2.1** and its
  independent workflow-content counter → **v1.9.1**: includes the required
  cross-model tie-breaker template and makes Phase F pull-request creation
  conditional on a separately recorded grant, keeps only `name` and
  `description` in loader frontmatter, and adds a top contents list. The other
  newly navigable plugin payloads are patch-released as
  `large-redesign-parallel-branch-collision-audit` **1.0.1**,
  `overnight-review-client-delivery` **1.0.1**,
  `overnight-review-panel-blocked-reviewer-reads-as-clean` **1.0.2**,
  `schedule-poll-orchestrator-pattern` **1.0.2**, and
  `subagent-review-tier-calibration-for-overnight-pr-chains` **1.0.1**.
  `overnight-multi-issue-implementation` remains at its already-unreleased
  **1.5.0**. The release ledger reproduces every prior payload from fixed
  published commit `3df43c37` and binds every current payload/version. The
  ledger, plugin manifests, and this release note are the canonical release
  history; runtime skill bodies keep their navigation and, where useful, one
  concise current-version line instead of duplicating historical changelogs.
  The routed Codex package keeps the seven
  canonical plugin `SKILL.md` entrypoints but installs them as ordinary
  `WORKFLOW.md` references, leaving only the umbrella model-visible. Bundle
  `VERSION` remains **1.5.0** because that bundle release is still unreleased.
  The unreleased bundle repairs also add strict state/journal fixtures, a real
  Codex 0.147.0 loader inventory probe, exact routing cases, immutable panel
  input/snapshot validation, and an atomic generation publisher with rollback
  and deterministic concurrency/drift/failure controls. Terminal validation
  retains the package reservation through panel acceptance; an exact
  dispatch-to-judgment-to-acceptance inventory chain and the separate
  acceptance command bind and release it. Bounded external reader attestations
  are revalidated at every atomic exchange, with append-linked renewals for
  inspected recovery. The 41-file umbrella now bundles the mandatory read-only
  panel validator and its two transitive resources beside the multi-issue
  workflow while excluding the mutating publisher.
  No live package is published by these repository changes.
- **2026-08-08** — `overnight-multi-issue-implementation` → **v1.5.0** and bundle `VERSION` → **1.5.0**: adds `references/large-live-queue-orchestration.md` for mixed indexes and backlogs whose rows may be stale, partly complete, owner-gated, or blocked by shared files. The procedure requires occurrence-aware parent-row reconciliation with child slices, separate authority grants, per-item budgets and latest starts, an explicit contention matrix, separate classification/task/reason/verification/disposition state, separate controller-liveness, execution-lease, and repository-relative exact-path-reservation records joined by ID, inspected crashed-controller takeover, durable recovery, complete review artifacts, deterministic target/merge-base/head/diff review, and serial integration. It adds a tracked canonical source, machine-checked state contract, and complete 41-file transitive install manifest for the concise Codex umbrella plus a focused CI contract gate. The umbrella preserves each workflow's directory shape, verifies local links in canonical and installed layouts, and carries the mandatory read-only panel validator without the mutating publisher. It also starts sequential tasks from a fresh target worktree instead of broadly resetting an unknown tree; honors a recorded merge-on-green grant while stopping when merge authority is absent; inspects a yielded process before retrying it; preserves the old discovery suite and adds large-queue trigger cases; and removes agent, workflow, and wall-time totals the source handoff did not verify.
- **2026-08-07** — `overnight-multi-issue-implementation` → **v1.4.0** (SKILL + both manifests; bundle `VERSION` already reads 1.4.0 from the 2026-08-06 release below and is unchanged — it is the bundle's own counter, not a mirror of this plugin's): adds **"Pre-flight: stale-base audit — what your OWN branch deletes"**, the inward mirror of the parallel-branch collision audit it now sits beside. That audit looks outward at branches that might conflict with you; this one looks at the branch you are about to merge. **The evidence is one incident.** A pull request merged from a branch created before several other pull requests landed and never rebased; its conflict resolution took its own side across the whole tree — **59 files, 1,891 insertions, 5,081 deletions** — reverting **11 files and 15 tracker entries belonging to three other sessions**, plus a function two surviving files still imported. Nothing failed: no conflict, green PR, schema validator passed, site still rendered. All three sessions had finished a wrap-up that morning and their work *was* on `main`, for between 6 and 90 minutes; the fastest discovery took 16 minutes 36 seconds and was an accident. The merged content was legitimate and had to stand, so `git revert` was the wrong tool — it would have destroyed everything merged after it. **What the section adds beyond "rebase before merge":** a pre-merge recipe that reads the DELETIONS, plus a total-deletions threshold set low enough to force a read — and it uses **`git diff origin/main..HEAD` with two dots, not three**, which is a correction the drafting turned up rather than a restatement. The three-dot form everyone reaches for diffs from the *merge base*, so on a branch that never took `main`'s newer commits a file added to `main` after the branch point is absent from both sides and reports as **no change at all**; verified on a two-commit synthetic repo where three-dot prints nothing and two-dot prints the file. Rebase first — the order is load-bearing — and note that a *plain* merge of a stale branch is harmless (git keeps what only `main` has); the damage needs the branch's tree to win wholesale, via a merge of `main` into the branch resolved to its own side, `-X ours`, or a squash of the branch tree. Also an audit-after recipe built on the finding most checklists miss — **`git cat-file -e` is the weak check**, because a file can be present with its contents rolled back and no existence check, id check or validator will say a word. Audit instead for a marker your change ADDED. The worked example is measured rather than argued: a page of seven interactive widgets whose option lists are single-quoted HTML attributes, three of them holding an apostrophe behind a one-character `&#39;`. Roll that escape back on the fourth widget and the file exists, the HTML is valid, all seven widgets are in the markup, every committed check passes — and four of the seven are silently dead, because the truncated attribute throws inside the one `forEach` that builds them all. Loading the real page with each escape rolled back in turn gives three working, or **none** if the first widget is the one broken. Recovery is a splice-forward (`git show <sha>:<path>`), never a revert, with the three things that bite during one: restore what the restored file imports, re-check state before each restore because parallel sessions are repairing at the same time, and re-measure any figure a restored file carries rather than reconciling two versions by eye.
- **2026-08-06** — `overnight-insight-discovery` → **v1.2.0** and `schedule-poll-orchestrator-pattern` → **v1.0.1** (both manifests each + bundle `VERSION` → 1.4.0): fixes the single-root skill-path bug in two places. A skill installed as a **plugin** lives at `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, not at `~/.claude/skills/<name>/`, so anything that reaches a skill through the `~/.claude/skills/` root alone misses on a plugin install. **The one with teeth**: `overnight-insight-discovery`'s Phase 0.Y toolchain pre-flight decided whether the skill was installed with a single `test -f ~/.claude/skills/overnight-insight-discovery/SKILL.md`. On a plugin install that test fails, and the failure path is a tap-out with `[ENV_BLOCKER]` reporting no skill tree — a failed lookup reported as an install-state finding. It now probes all three roots (`$CLAUDE_PLUGIN_ROOT`, `~/.claude/skills/`, then the plugin cache), ranks cache hits on the **version** segment alone rather than whole-path `sort -V` (the marketplace segment sorts first, so `aaa-mkt/2.5.0` would otherwise lose to `zzz-mkt/1.0.0`), uses `find` instead of a glob (zsh's `nomatch` fails a non-matching glob before `2>/dev/null` applies), and prints "not found — tried <the three paths>" rather than anything that reads as "not installed". **The cosmetic ones**: three dead see-also links in `schedule-poll-orchestrator-pattern` pointed at `~/.claude/skills/<name>/SKILL.md` files a reader on a plugin install cannot open — now plain skill names with a GitHub URL where the source repo is known — and one `~/.claude/skills/`-rooted self-reference in `overnight-insight-discovery`'s v1.3.2 changelog entry. **Deliberately unchanged**: every `~/.claude/skills/**` mention in § "Autonomous-safe skill edits" and its Phase G summary. Those are the path patterns that fire a sensitive-file permission prompt in Claude Code, which is a fact about the prompt system and not about where this skill is installed; broadening them would break the contract they encode. `CLAUDE_PLUGIN_ROOT` alone is not the fix — it is frequently unset in the shell a step actually runs in, and it points at the running plugin's own root, so it can never reach a sibling plugin.
- **2026-08-06** — `overnight-multi-issue-implementation` → **v1.3.1** (SKILL + both manifests + bundle `VERSION`): drops an unsourceable figure from the entry below. The "never truncate a findings payload" lesson described the lost finding as sitting in a "553-line pre-registration". That was true of the document the reviewer read; five blocking findings were then fixed on the branch before it merged, roughly doubling it, and it now stands at about 1,100 lines — a figure with no vintage attached, in a document the skill's own readers cannot re-derive it from. The length was decoration; "a pre-registration that had no power statement anywhere" carries the whole point, so the count is gone rather than dated. The same audit **retracted a rate**: "about one in four" is no longer claimed, because the host repo reopened its own count (a sixth instance surfaced, and two of the five were documents written from scratch). It now reads "common enough to budget a round for, not a measured rate". Everything else checked out against the source — the six-findings/five-arrived split, the ±0.03-versus-0.17 margin behind "about five times finer", and the 269 → 314 test counts are all recorded in the run's own handoffs. This is the skill's own "a fix ships a fresh instance of the defect it repairs" rule firing on the release that introduced it.
- **2026-08-06** — `overnight-multi-issue-implementation` → **v1.3.0** (SKILL + both manifests + bundle `VERSION`, which had drifted a patch behind): seven lessons from a single overnight run; exact agent, workflow, and wall-time totals are omitted because its surviving handoff did not verify them. **Never `.slice()` a findings payload** (three reviewers returned six critical findings; the merging agent received five, and every visible signal still said the gate had worked — make the actor count what it received against what it answered, and keep the run journal as the recovery path). **Coordinating with sessions you do not control**: claim your intent and your file list on a shared in-repo board before you start, append rather than replace, and don't take the claim down while your PRs are open. **Amend a running orchestration through a file on disk, not the script** — editing the script changes every agent prompt and a resume then re-runs completed work instead of replaying it from cache. A **third kind of collision** neither pre-flight audit can see (the same piece of work under two names, in a live session's uncommitted tree). **Baseline numbers in your own brief go stale mid-run** — measure, never quote. **What an autonomous run may and may not decide** (an assumption is a default, not a ruling; for pre-registered questions disclose rather than compute; an un-run unit is "no result", not "inconclusive"; production changes only in the reverting direction and only on unanimous authorisation from the run's own reviewers, with anything that is not a revert still waiting for a person; merging is a separate grant from changing production). And a **verbatim line for every reviewer prompt** — "Check whether this change ships a fresh instance of the defect it repairs." — which caught multiple cases, every one by re-deriving a number rather than by reading the diff.
- **2026-07-17** — `overnight-multi-issue-implementation` → **v1.2.0** (SKILL + manifests, fixing a manifest-version drift): adds **Phase 0 — backlog triage + owner-ruling application** for unvalidated issue clusters (triage biased against dismissal with adversarial verification of dismissals only; owner cut-line ratification via an interactive review page; rulings baked as greppable issue comments before any build; decision-session / build-session split with a wave-ordered kickoff prompt; follow-up ruling rounds handled additively; successor-before-close sequencing). Also documents that the close-keyword issue trap fires from **docs-only planning PR bodies** ("then close #N" in a kickoff-prompt addendum closes the live tracker on merge). Extracted from a real large-backlog triage-and-rulings run. Cross-links the new [`interactive-feedback-report`](https://github.com/wan-huiyan/interactive-feedback-report) skill.
- **2026-06-02** — Standing convention added: review every **non-trivial PR** with the `roundtable:agent-review-panel` skill (all agents `model: opus`) before squash-merge; trivial/docs-only PRs may skip the full panel. Reconciled with the per-PR tier rubric (the panel is the heavyweight tier; single-reviewer tiers remain for low-risk PRs). `overnight-multi-issue-implementation` SKILL → v1.1.1, `subagent-review-tier-calibration-for-overnight-pr-chains` SKILL → v1.0.1.
- **v1.1.0** (2026-05-08) — Adds `overnight-multi-issue-implementation` for the engineering-side overnight pattern (issues → stacked PRs). README updated to reflect three plugins; install + compose sections expanded.
- **v1.0.0** (2026-04-17) — Initial release bundling two plugins. `overnight-review-client-delivery` was previously a standalone skill; this bundle adds the insight-discovery sibling and unifies the shared patterns (locked-file escape hatch, branch hygiene, file-first successor handoff, archive-and-regenerate).

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Patches welcome. The shape of the workflow plugins is still settling — if you run them in production and learn something the skills didn't catch, please open an issue or PR against the relevant `SKILL.md` or reference doc.

Common contribution targets:
- Additional panel personas for specific domains (medical, financial, legal)
- New yield classes for the adaptive tuning loop (insight-discovery only)
- Platform-specific adaptations of `bq_budget.py` (Snowflake, Redshift, Databricks)
- Alternative HTML renderers (beyond markdown2)

---

🤖 Patterns co-developed with Claude Code. All examples in the skills use synthetic data; no client-specific numbers in this repo.
