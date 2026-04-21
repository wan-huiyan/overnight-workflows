# Phase 0.Y — Remote-env toolchain pre-flight (v1.7.1)

## Problem this solves

When dispatching an overnight run as Claude Code remote-agent triggers (via
`RemoteTrigger`, the `schedule` skill, or any CCR-backed environment), the
environment's toolchain is NOT guaranteed by its name. The default environment
commonly called `claude-code-default` (`env_01MBkzjVoKrUNooaYpQq9vvz` at time
of writing) is a **bare** Claude Code container:

- No `bq` CLI
- No `gcloud` SDK / ADC flow (user's personal ADC does not propagate)
- No Python data stack (pandas / scipy / statsmodels / xgboost / matplotlib)
- No plugin/skill tree — `~/.claude/skills/overnight-insight-discovery/` does
  not exist, so Skill-tool invocations fail silently
- Tools you *think* are auto-included by "being an autonomous Claude Code
  agent" are in fact gated by the trigger's `allowed_tools` list. If you
  don't list `RemoteTrigger` explicitly, the dispatched agent cannot call it
  — even when the prompt instructs it to fire the orchestrator at terminal
  phase.

## What happens without the gate

S100 (2026-04-21) dispatched 4 track triggers + 1 orchestrator trigger into
the default CCR env without verifying toolchain. All 4 tracks tapped out
within ~10 min with identical root cause: no `bq`, no `gcloud`, no skill
tree. Each track committed a skeleton `tap_outs.md` + empty brief to `main`,
then exited. The orchestrator's t+12h safety cron would have fired against
empty inputs. The user killed the run, re-dispatched locally using
`Agent(run_in_background=true)` subagents, and lost ~20 min of wall-clock +
4× burned Opus session-block credits.

**Cost of skipping this gate:** one bad dispatch ≈ 20 min user time + 4×
failed runs' compute. Cost of running the gate: one `get` API call per
trigger + a one-time 30-second toolchain check.

## Gate implementation (mandatory for any CCR-dispatched overnight run)

Run this before invoking `RemoteTrigger action:"create"` for any track
trigger. Anchor it as **Phase 0.Y**, immediately after `phase_0_predispatch_
gate.md` (Phase 0.X) and before Phase A dispatch.

### Step 1 — Verify the target environment has required tooling

If the environment supports a pre-flight probe trigger: fire one disposable
trigger against the env with a minimal prompt that runs the toolchain check
and writes `env_preflight.json` back to the repo. Wait for completion. Read
the result. Gate on PASS.

If no such probe trigger is practical (e.g. you're dispatching into a brand
new env and don't want to pay the round-trip): at minimum, surface the env
ID + expected toolchain to the user in the Phase 0.X batched gate and ask
them to confirm.

### Step 2 — Toolchain manifest (must all PASS)

```bash
# Cloud tooling
gcloud --version
bq --version
gcloud auth application-default print-access-token >/dev/null 2>&1

# Python stack (versions depend on run — check your scoping/config.yaml)
python3.11 -c "import pandas, scipy, statsmodels, xgboost, matplotlib, seaborn, pyarrow; print('ok')"

# Project-specific auth — service account, NOT user's personal ADC
test -f "$GOOGLE_APPLICATION_CREDENTIALS" && echo "SA key present"

# Skill tree
test -f ~/.claude/skills/overnight-insight-discovery/SKILL.md

# gh CLI + git
gh --version
git --version

# Write result to repo for traceability
echo '{"preflight": "PASS", "env_id": "'"$CCR_ENV_ID"'", "timestamp": "'"$(date -u +%FT%TZ)"'"}' \
  > "$WORK_DIR/state/env_preflight.json"
```

Any failure → the trigger agent writes `env_preflight.json` with `"PASS":
false` + missing-deps list, commits, and tap-outs immediately with
`[ENV_BLOCKER]` tag. The orchestrator on wake detects `preflight: false` in
all tracks and surfaces the env-ID + missing-deps for the morning user
rather than proceeding to consolidation against empty briefs.

### Step 3 — Verify `allowed_tools` completeness

The trigger body's `session_context.allowed_tools` list MUST include:

- `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep` (base I/O)
- `RemoteTrigger` — only if the trigger's prompt calls it (e.g. event-driven
  orchestrator wake-up at terminal phase). **Absence here is silent
  failure**: the prompt will *instruct* the agent to invoke RemoteTrigger,
  but the tool is simply not in the registry, so the agent writes a note
  "RemoteTrigger not surfaced" and proceeds without firing the orchestrator.
- Any MCP servers your skill ref expects (e.g. `mcp__claude-in-chrome__*`
  for agent-browser workflows, `mcp__ccd_directory__*` for registry lookups)
- `WebFetch`, `WebSearch` if the run does external literature verification

Checklist in the Phase 0.X batched ask: "Confirmed `allowed_tools` includes
RemoteTrigger for track triggers + orchestrator trigger? [Y/N]"

### Step 4 — Auth model

User's personal ADC (`gcloud auth application-default print-access-token`
from their laptop) does NOT flow into a CCR container. Two workable patterns:

1. **Service account key mounted at a fixed path**, exposed via
   `GOOGLE_APPLICATION_CREDENTIALS` env var set by the CCR env config. The
   SA needs at minimum `roles/bigquery.jobUser` + `roles/bigquery.dataViewer`
   on the scoped datasets.
2. **Workload Identity / short-lived token** — preferred long-term but
   requires CCR + GCP to trust each other. Out of scope until CCR supports
   it natively.

Surface the auth model in Phase 0.X: "Env `<id>` uses SA `<email>` with
perms `<list>`. Confirm this matches the BQ scope of your run? [Y/N]"

## When to SKIP this gate

- **Local dispatch via `Agent(run_in_background=true)`**: the toolchain is
  inherited from the main session and has already been verified by Phase
  0.-1 (credential liveness) + Phase 0.0 (schema probes). Phase 0.Y is
  CCR-specific.
- **Re-firing a previously-verified trigger on the same env within 7 days**:
  the env doesn't change between runs. Cache the `env_preflight.json` from
  the first successful dispatch.

## Origin

S100 (2026-04-21) v5 overnight ah-ha — lost one dispatch cycle to bare
`claude-code-default` env. User killed the run at 15:16 UTC, re-dispatched
locally. Root-cause documented at `docs/handoffs/session_100_*` in the
`barryu_application_propensity` project. Full CCR-env provisioning design
(image manifest, SA setup, preflight script) is being authored under a
separate Option B task → v1.8.0 proposal lands at
`docs/overnight/2026-04-21/skill_updates/ccr_env_requirements.md`.
