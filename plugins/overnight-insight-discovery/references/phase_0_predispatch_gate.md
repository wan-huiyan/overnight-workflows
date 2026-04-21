# Phase 0.X — Pre-dispatch confirmation gate (v1.7.0)

## Problem this solves

Long-running overnight dispatches have a failure mode where
confirmation-requiring items surface *mid-run*, blocking progress until the
user wakes up and answers. Classic examples:

- **Skill-mechanism constraints that deviate from the plan.** E.g. the
  `schedule` skill's 1-hour cron minimum conflicts with a 30-minute polling
  spec inherited from the predecessor session. Orchestrator discovers this
  while trying to create the trigger, then stalls.
- **Blast-radius items that deserve a conscious green-light.** 5 parallel
  remote Opus agents × 4–6 h × potential 5 TB BQ scan = real money + durable
  PRs + branch state. "Pre-authorized in the handoff" is not the same as
  "pre-authorized with today's BQ pricing, today's cloud quota, today's
  patience for 12–20 h wall-clock."
- **Branch-checkout questions for remote agents.** Most bootstrap artefacts
  live on a non-`main` branch the remote agent won't see unless the PR is
  merged OR each prompt includes an explicit `git fetch && checkout`.
- **Phase 0 probe results that change planned behaviour.** Cross-year overlap
  = 0 → tracks silently degrade to DESCRIPTIVE_ONLY. Did the user intend
  that? If they expected longitudinal cohort analysis, silence is worse than
  asking.

Without a batched gate, each of these surfaces individually during dispatch,
and every one is a micro-stall. With the gate, the orchestrator asks once,
at the end of Phase 0, and either proceeds silently (if confirmed as-is) or
applies redirect fixes before firing.

## When to run the gate

**Always**, right after Phase 0 probes + scoping write-out, **before any
trigger creation or Agent dispatch.** It's a ~5 min step that sits between
"Phase 0 complete" and "Phase A launch."

Skip only if:

- The run is fully autonomous from an already-authenticated CI pipeline
  (no human is expected to be available at any point), AND
- The scoping config explicitly sets `predispatch_gate.mode: skip_and_log`.
  In that mode, the orchestrator records the would-be-asked items to
  `morning_summary.md §0` for post-hoc review instead of blocking.

## What the gate surfaces

One `AskUserQuestion` call (or equivalent). Each item is one question with
structured options + a free-text escape. Items to include **only if
applicable to this specific run** — no zero-substance "just checking"
questions.

### Item 1 — Dispatch-mechanism deviations

If any dispatch-mechanism constraint conflicts with the plan as written,
surface the delta + the proposed resolution.

> Example: "The `schedule` skill minimum cron is 1 hour; your plan specs
> 30-min polling. Options: (a) accept 1-hour polling; (b) switch to
> event-driven dispatch (each track fires the orchestrator on completion);
> (c) keep the 30-min interval via local `/loop` — requires laptop stay
> open."

Default option in the ask is the safest (usually option b, event-driven, if
applicable — fewer surprises, sub-minute latency).

### Item 2 — Blast-radius confirmation

Compute a single-screen summary:

```
# Dispatch blast radius
- Remote agents:        N × Opus-1M, M-hour wallclock each
- Claude compute est.:  $LOW–$HIGH
- BQ scan cap:          Y TB soft / Z TB hard
- Durable artefacts:    N branches, M-1 commits per track, 1 final PR
- Worst-case wallclock: K hours (includes t+12h safety)
```

Show this to the user. They either say "proceed" or "abort" or redirect
with a scope change (e.g., "drop Track D, cuts compute ~25%"). Do NOT skip
this — "pre-authorized in the handoff" is a lower bar than current-moment
authorization.

### Item 3 — Branch-checkout question

If Phase 0 artefacts live on a non-`main` branch, ask:

> Options: (a) merge the anchor PR to `main` now so remote agents see the
> bootstrap; (b) bake `git fetch origin <branch>; git checkout <branch>`
> into each remote track prompt; (c) force-push the branch to a
> shared-agent branch and pin triggers to that ref.

Option (a) is cleanest when the PR is review-approved; option (b) preserves
the DO-NOT-MERGE-until-morning pattern but adds 2 lines per prompt.

### Item 4 — Probe-result surprises

Any Phase 0.0 probe that flipped a planned panel from included → excluded
(or vice versa), or degraded a track from FULL → DESCRIPTIVE, gets
explicitly named here. Ask: "Does this match your intent?"

Examples worth surfacing:

- Cross-year identity overlap probe returned 0 — tracks B5/C4 auto-degrade
  to DESCRIPTIVE_ONLY.
- SW 202X panel passed the `min_n_500` gate and entered scope — user may
  or may not want that weight.
- An engagement signal expected in the v10 schema is absent — Track E
  column is entirely empty for one signal family.

### Item 5 — Skill/remote-sync status

If the local skill was bumped this session (e.g., v1.6.0 → v1.7.0), remind
the user that the remote plugin repo may still be at the previous version
and the remote agents (if they pull from remote) will use the old recipe.
Ask: "Sync remote skill now (y/n)?"

## What the gate does NOT do

- It does not re-ask scope decisions that were durably recorded in the
  predecessor handoff (e.g. "Q3 k-cap = 12"). Those are already authorized.
- It does not surface every Phase 0 number — just the surprises and
  deviations. Routine probe results go in `phase_0_probe_results.md`,
  not the gate.
- It does not re-litigate design choices from the plan doc (e.g. B-vs-C
  parallel, BH-FDR MHT). Those are skill-level defaults + plan-level
  decisions already made.

## Implementation shape

Inside the orchestrator's Phase 0 closeout:

```python
items = []

# 1. Dispatch-mechanism deviations
if plan.polling_interval_minutes < schedule_skill.min_cron_minutes:
    items.append(ask_item(
        question=f"Plan specs {plan.polling_interval_minutes}-min polling "
                 f"but schedule skill minimum is {schedule_skill.min_cron_minutes}-min. Choose:",
        options=["1hr polling", "event-driven", "local /loop"]))

# 2. Blast radius
items.append(ask_item(
    question=render_blast_radius_summary(plan, probes),
    options=["proceed", "abort", "redirect"]))

# 3. Branch checkout
if current_branch != "main" and bootstrap_on_branch:
    items.append(ask_item(
        question="Remote agents default to main; Phase 0 artefacts are on "
                f"`{current_branch}`. Choose:",
        options=["merge PR first", "bake checkout into prompts", "force-push"]))

# 4. Probe surprises
for surprise in probes.flipped_defaults:
    items.append(ask_item(surprise.describe(), options=["intended", "redirect"]))

# 5. Skill remote-sync reminder
if local_skill.version > remote_plugin.version:
    items.append(ask_item(
        question=f"Local skill at {local_skill.version}, remote at {remote_plugin.version}. Sync now?",
        options=["yes, sync now", "no, S99 uses local recipe"]))

if not items:
    log("predispatch gate: no items to surface, proceeding silently")
else:
    responses = ask_user_question_batched(items)  # ONE batched call
    apply_redirects(responses)
```

## Failure modes the gate prevents

- **Mid-run stall on authentication dialog.** Skill mandates mid-run
  `AskUserQuestion` → autonomous run blocks → user wakes to a stuck prompt.
- **Silent degradation to wrong scope.** Probe result flips a panel from
  full → descriptive; tracks start; morning brief reports descriptive-only
  and user thinks something broke.
- **Wrong branch checkout.** Remote agents run against `main`, find no
  track prompts, hang / tap out immediately.
- **Remote/local skill drift.** Tracks run the old recipe and surface
  findings that the new recipe would have caught/rejected.

## Origin

Introduced in v1.7.0 after S99 dispatch prep hit two of these exactly:
(1) schedule-skill 1-hour min vs 30-min poll spec — surfaced mid-P5,
required user decision to resolve; (2) branch-checkout question for remote
agents — surfaced ad-hoc because no gate checked for it.

User feedback: "update the overnight workflow skill so next time we don't
need user intervention mid run, if we have something to confirm, ask at
the beginning of the session." This doc is the response.
