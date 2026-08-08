# Large live-queue orchestration

Use this variant when the source is a live index, handoff directory, tracker, or
backlog with roughly 15 or more mixed items. It is for queues where some rows
are already done, some contain only an owner decision, some have an autonomous
slice, and several will edit the same central record.

This is not the two-stacked-PR shape. Build an exhaustive classification first,
then derive a serial integration sequence from current evidence.

## Contents

1. [Pin the source](#1-pin-the-source)
2. [Prove complete queue coverage](#2-prove-complete-queue-coverage)
3. [Separate authority from readiness](#3-separate-authority-from-readiness)
4. [Build dependencies, budgets, and waves](#4-build-dependencies-budgets-and-waves)
5. [Assign file ownership](#5-assign-file-ownership)
6. [Use a durable state model](#6-use-a-durable-state-model)
7. [Dispatch and recover safely](#7-dispatch-and-recover-safely)
8. [Review complete artifacts at an exact commit](#8-review-complete-artifacts-at-an-exact-commit)
9. [Land changes serially](#9-land-changes-serially)
10. [Close the run honestly](#10-close-the-run-honestly)
11. [Preflight the plan adversarially](#11-preflight-the-plan-adversarially)

## 1. Pin the source

0. Record authority for network access and every later external or shared-state
   action. If fetch is not authorized, use only the available local evidence,
   mark freshness `UNCHECKED`, and do not dispatch or merge work that depends on
   a current target.
1. When network access is authorized, fetch the real remote target before
   reading task files.
2. Record the exact commit used for classification.
3. Read through `git show <sha>:<path>` or a detached, read-only worktree.
4. Record current open pull requests, active sessions, shared-board claims, and
   physical worktrees separately. A branch list is not a session inventory.
5. Keep the source commit and the execution commit distinct. A correct plan at
   the pinned source can still be stale when execution begins.

When fetch is authorized, re-fetch before dispatch and before every merge. If
it is not, stop before those actions. Reclassify any row whose premise,
dependency, owner decision, file claim, or open pull request changed.

## 2. Prove complete queue coverage

Count the source rows mechanically. Do not accept a plan's sentence saying it
read everything as evidence that it did.

Create exactly one parent classification record per source row. A mixed row
uses a `slices` array inside that parent; it does not create duplicate parent
records. Record at least:

| Field | Meaning |
|---|---|
| `source_occurrence_id` | Stable row identity; distinguish repeated links with a durable row key or `(source_path, occurrence_index)` |
| `workstream` | Product or operational area |
| `classification_state` | One of the exact queue-classification states below |
| `autonomous_slice` | Work an unattended agent may complete |
| `stop_before` | First owner- or authority-only action |
| `owner_gate` | Judgement, account, signature, payment, or recruitment needed |
| `authority_gate` | Commit, push, PR, merge, deploy, network, paid-call, or external-repo permission |
| `size` | Small, medium, large, or a measured time estimate |
| `dependencies` | Stable IDs, not descriptive guesses |
| `write_set` | Exact paths or declared path families |
| `review_tier` | Repository-defined risk tier |
| `evidence` | Current code, decision, tracker, PR, or prompt location |
| `slices` | Optional subrecords when parts have different state, authority, dependency, or stop points |

Use these exact classification states:

- `EXECUTABLE`: the whole remaining item can run with current authority.
- `CONDITIONAL`: a bounded autonomous slice exists; state the stopping point.
- `OWNER_BLOCKED`: the next action needs the owner.
- `AUTHORITY_BLOCKED`: technically ready, but permission is absent.
- `STALE_DONE`: current evidence proves the requested outcome already shipped.
- `SUPERSEDED`: a newer prompt, ruling, or implementation replaces it.
- `EXCLUDED`: outside the run's declared scope; state whether it is unchecked.
- `UNCERTAIN`: evidence conflicts; investigate or leave blocked rather than guess.

Split rows into child slices when their parts have different states. A prompt
can have completed analysis, an autonomous generation step, and a final owner
grading step. Calling the whole row done drops work; calling the whole row
blocked wastes the autonomous part. Each child slice has its own state,
authority, dependencies, write set, budget, and stopping point.

After classification, assert both:

```text
source_row_count == parent_classification_count
Counter(source_occurrence_ids) == Counter(parent.source_occurrence_id for parent in classifications)
```

Do not compare plain filename sets: two rows may link the same prompt for
different reasons. Give each occurrence a durable identity and compare the
ordered occurrences or a multiset. As a negative control, duplicate one prompt
path in a synthetic two-row input, omit or reuse one parent occurrence, and
confirm the coverage check fails before trusting the harness.

Then independently re-check every `STALE_DONE` and `SUPERSEDED` verdict. A false
dismissal silently removes work; a false `EXECUTABLE` usually gets caught when an
implementer opens the code.

### Prompts are evidence, not final truth

Open every linked prompt because its constraints and stopping rules matter.
Then check those claims against current code, committed artifacts, standing
decisions, tracker state, and merged pull requests. A detailed prompt can still
contain a retired constant or threshold. Prefer the newest authoritative source,
and record the disagreement instead of silently choosing one.

Re-search every path, constant, count, and line reference immediately before a
writer starts. These details can change within the same night.

## 3. Separate authority from readiness

Record grants independently. At minimum distinguish:

- local branch and commit;
- push;
- open or update a pull request;
- merge;
- deploy or change production configuration;
- paid API calls;
- external network or data-provider calls;
- generation jobs that consume scarce capacity;
- writes to another repository;
- filing or closing issues;
- owner-only judgement, grading, signature, payment, or recruitment.

Do not infer one grant from another. Without merge permission, an item may
reach `task_state: READY` with `reason_code: AUTHORITY`. That does not prevent
authoring or review if those actions were authorized.

Every assumption belongs in the pull-request body under an `ASSUMPTION`
heading. An assumption cannot override a standing decision, a pre-registration,
or a missing external-action grant.

## 4. Build dependencies, budgets, and waves

Draw dependencies before scheduling. Use stable IDs and distinguish:

- **authoring dependency**: the writer cannot start;
- **review dependency**: work can be prepared but not approved;
- **merge dependency**: work can reach a reviewed commit but not land;
- **owner dependency**: the autonomous stopping point has been reached.

For every executable item record:

- working budget;
- latest safe start;
- review deadline;
- merge deadline;
- stop rule;
- fallback if its dependency or collision does not clear.

If the latest start passes, record `task_state: SKIPPED` and
`reason_code: TIME`. Do not start work that cannot finish its required review
and integration before the run ends. Reserve capacity for review, fixes, merge
conflicts, and the morning handoff.

Build waves from dependencies and file claims, not topic labels. Tasks may run
in parallel only when their non-integrator write sets are disjoint and the
controller has capacity to monitor them.

## 5. Assign file ownership

Create a contention matrix before dispatch. For each item list:

- worker-written paths;
- read-only inputs;
- generated outputs;
- integrator-written shared files;
- paths already claimed by another session or pull request.

Give central trackers, indexes, manifests, shared generated files, and release
notes to one integrator. Workers return structured wrap-up data and never edit
those paths. If every item ultimately changes one shared file, merges are
strictly serial even when worker authoring is parallel.

Inventory is not cleanup. Do not prune, delete, move, or reuse another session's
worktree while sessions are active. Record suspected debris for a later audit.

## 6. Use a durable state model

Keep four dimensions separate:

1. `task_state`: `QUEUED | RUNNING | REVIEW | READY | BLOCKED | FAILED | MERGED | SKIPPED`
2. `reason_code`: for example `OWNER`, `AUTHORITY`, `EXISTING_PR`, `COLLISION`,
   `REVIEW`, `AGENT_FAILURE`, `TIME`, or `NONE`
3. `verification`: `PASS | FAIL | UNCHECKED | NOT_APPLICABLE`
4. `run_disposition`: `ACTIVE | TERMINAL`

Do not use any of those four fields for controller health or file ownership.
A durable runner also keeps three operational record types separate. They may
share one append-only operational journal or use separate JSONL files, but task
transitions only join to them by ID and never copy their mutable state.

Every operational record carries `run_id` and `repository_id`.
`repository_id` is the stable canonical remote identity such as
`github.com/owner/repository`, or `local:<canonical-absolute-root>` when no
remote exists. This prevents one run or repository from clearing another's
records when their task or reservation IDs happen to match.

1. **Controller liveness** says whether a named controller session or process is
   currently supervising the run. Its records carry `run_id`, `repository_id`,
   `controller_id`, `state` (`RUNNING` or `STOPPED`), `heartbeat_at`,
   `stopped_at`, `stop_reason`, `tool_session_id`, `pid`, and `host`. A stopped
   controller is not a live session even when branches or pull requests from
   its run remain open.

```json
{
  "time": "ISO-8601",
  "sequence": 17,
  "schema_version": 1,
  "record_type": "controller_liveness",
  "run_id": "overnight-2026-08-08",
  "repository_id": "github.com/example/project",
  "controller_id": "controller-1",
  "state": "RUNNING",
  "heartbeat_at": "ISO-8601",
  "stopped_at": null,
  "stop_reason": null,
  "tool_session_id": "tool-session-or-null",
  "pid": 12345,
  "host": "runner-host"
}
```

2. **Execution lease** gives one attempt temporary permission to run a command
   or agent. Its records carry `run_id`, `repository_id`, `lease_id`,
   `attempt_id`, `lease_owner`, `state` (`ACTIVE` or `ENDED`),
   `started_at`, `heartbeat_at`, `lease_expires_at`, `takeover_condition`,
   `ended_at`, `end_reason`, `tool_session_id`, `pid`, `command`, `worktree`,
   and `branch`. Passing `lease_expires_at` starts the recorded inspection; it
   does not itself prove the old process ended or change the latest state.

```json
{
  "time": "ISO-8601",
  "sequence": 18,
  "schema_version": 1,
  "record_type": "execution_lease",
  "run_id": "overnight-2026-08-08",
  "repository_id": "github.com/example/project",
  "lease_id": "lease-task-12-attempt-1",
  "attempt_id": "task-12-attempt-1",
  "lease_owner": "agent-7",
  "state": "ACTIVE",
  "started_at": "ISO-8601",
  "heartbeat_at": "ISO-8601",
  "lease_expires_at": "ISO-8601",
  "takeover_condition": "inspect tool session, PID, log, worktree, diff, and commits",
  "ended_at": null,
  "end_reason": null,
  "tool_session_id": "tool-session-or-null",
  "pid": 12346,
  "command": "exact-command-or-null",
  "worktree": "/absolute/path",
  "branch": "unique-branch"
}
```

Execution leases use no transfer state. To hand an attempt to another owner,
append an `ENDED` transition for the old lease after the required inspection,
then create a different lease ID whose latest record is `ACTIVE`. Join later
task transitions to the new lease ID. This leaves one unambiguous owner instead
of making a transferred lease look simultaneously ended and active.

3. **Path reservation** records that an active branch, pull request, or named
   owner still claims exact paths. It is a collision record, not a heartbeat or
   execution lease. It may remain active after both the controller and its last
   execution lease stop.

Store path-reservation transitions durably, separately from any ephemeral
running-session board. Each standalone transition repeats at least `run_id`,
`repository_id`, `reservation_id`, `exact_paths`, `owner`, `state`,
`expires_at`, `takeover_condition`, `released_at`, and `release_reason`:

```json
{
  "time": "ISO-8601",
  "sequence": 19,
  "schema_version": 1,
  "record_type": "path_reservation",
  "run_id": "overnight-2026-08-08",
  "repository_id": "github.com/example/project",
  "reservation_id": "reservation-pr-934-data-js",
  "exact_paths": ["docs/site/assets/data.js"],
  "owner": {
    "kind": "pull_request",
    "id": "934",
    "branch": "example-branch"
  },
  "state": "ACTIVE",
  "created_at": "ISO-8601",
  "expires_at": null,
  "takeover_condition": "PR merged or closed, then branch and path diff inspected",
  "released_at": null,
  "release_reason": null
}
```

Use `ACTIVE | RELEASED | TRANSFERRED` for reservation state. A nullable expiry
and a mandatory non-empty `takeover_condition` are separate fields on every
active reservation. `expires_at` may be null, meaning the claim does not age out
automatically; that never permits omitting the takeover condition. Reaching a
non-null expiry starts that recorded inspection and takeover procedure; it does
not silently free the path. Every entry in `exact_paths` is normalized and
relative to the root of the repository identified by `repository_id`, never an
absolute path or a path containing `..`.
Release only after merge plus content verification, documented abandonment or
supersession, or a named transfer. On release, append a transition with the
same run, repository, `reservation_id`, exact paths, owner, a non-null
`released_at`, and a specific `release_reason`. Stopping a controller clears
controller liveness. It does not by itself end an execution lease whose agent
or process is still alive, and it never releases an active branch or
pull-request path reservation.

`TRANSFERRED` ends the old reservation. Its terminal transition carries a
mandatory `replacement_reservation_id`, non-null `released_at`, and transfer
reason. The replacement ID must differ from the old ID, belong to the same run
and repository, cover the same exact paths, and have an `ACTIVE` highest-valid
record before the old transition is accepted. For a partial transfer, release
the old reservation and create separately named active reservations for the
new path sets; do not leave one transferred record partly active.

For each `(run_id, repository_id, record_type, record ID)`, the latest appended
operational record is authoritative. Give records a monotonically increasing
`sequence`; the highest valid sequence for that key is latest, and wall-clock
timestamps never override it. Task records cannot override that state. If a
referenced controller, lease, or reservation record is missing, unreadable,
duplicates a sequence, or moves backward in sequence, mark the relevant safety
check `UNCHECKED` and stop rather than inferring that the controller is dead,
the lease is free, or the path is unclaimed.

Do not create combined spellings such as `BLOCKED_REVIEW` in one place and
`READY_MERGE_BLOCKED` in another. Keep the base state stable and put the detail
in `reason_code` and `next_action`.

`classification_state` answers whether a source occurrence can start;
`task_state` answers where an attempted slice is in its execution lifecycle.
Reserve `task_state: READY` for an authored and reviewed result awaiting its
next authorized action. The normal path is
`QUEUED -> RUNNING -> REVIEW -> READY -> MERGED`. `BLOCKED` may return to
`QUEUED` or `RUNNING` after its reason clears. A bounded retry may move
`FAILED -> QUEUED` with a new attempt ID; after the retry cap, `FAILED` is final
for this run. `MERGED` and `SKIPPED` are final for this run. A reviewed
`READY` item with `reason_code: AUTHORITY`, or a `BLOCKED` item with an explicit
handoff, may be a terminal **run disposition** while remaining unfinished work.
Record that distinction in `next_action`; do not rewrite historical journal
records to adopt a newer schema.

Append transitions to a durable JSONL journal. Each record should carry:

```json
{
  "time": "ISO-8601",
  "sequence": 24,
  "schema_version": 1,
  "record_type": "task_transition",
  "run_id": "overnight-2026-08-08",
  "repository_id": "github.com/example/project",
  "source_occurrence_id": "stable-row-occurrence-id",
  "classification_state": "EXECUTABLE",
  "task_id": "stable-id",
  "attempt_id": "stable-attempt-id",
  "controller_id": "controller-1",
  "lease_id": "lease-task-12-attempt-1",
  "reservation_ids": ["reservation-pr-934-data-js"],
  "task_state": "REVIEW",
  "reason_code": "NONE",
  "verification": "UNCHECKED",
  "run_disposition": "ACTIVE",
  "source_sha": "classification-source-sha",
  "execution_base_sha": "worktree-base-sha",
  "worktree": "/absolute/path",
  "branch": "unique-branch",
  "head_sha": "full-sha",
  "retry_count": 0,
  "log_path": "/durable/path/or-null",
  "output_paths": [],
  "checks": [],
  "review_artifacts": [],
  "next_action": "dispatch tier-1 panel"
}
```

`controller_id`, `lease_id`, and `reservation_ids` are joins to the latest
authoritative operational records above. They may be null or empty before
assignment. Do not snapshot controller state, tool session, process ID,
command, heartbeat, lease expiry, reservation owner, or reservation state into
a task transition; those copies go stale and create two competing answers.

Give the append-only journal one controller/integrator writer, or use atomic
append with locking if the platform requires multiple writers. Store the
journal, logs, and review reports outside disposable worker worktrees, while
recording any committed copy separately. Put heartbeats and bounded leases in
their operational records. On expiry, inspect the journal, process state,
worktree, branch, diff, and commits before takeover. Cap retries; repeated
retries over unknown partial state create more ambiguity.

## 7. Dispatch and recover safely

- Give every writer a real worktree pinned to an explicit commit and a unique
  branch. Verify the directory and branch after creation.
- Put the allowed write set, stopping point, assumptions rule, validation, and
  return schema in the dispatch.
- Have agents write large results and reviews to files. Chat summaries are not
  the recovery record.
- Inspect commits and test output rather than trusting a completion message.
- Re-read current remote and collision state before starting a dependent item.

A yielded, timed-out, or apparently silent tool call may still be running.
Before retrying a long command, inspect the tool session, PID, log, worktree,
and output file. Resume or poll the existing process when possible. Never start
a duplicate test, generation job, paid call, or merge because the first call
stopped printing output.

## 8. Review complete artifacts at an exact commit

Repository rules override generic review shortcuts. If a repository requires
heavy review for a rendered surface, registered statistic, security boundary,
or widely consumed constant, do not downgrade it because the diff looks small.

For every review, record these separate identities:

- `target_ref`: the destination ref observed at dispatch;
- `target_sha`: that ref's immutable tip;
- `merge_base_sha`: `git merge-base "$target_sha" "$head_sha"`;
- `head_sha` and `head_tree_oid`;
- `diff_digest_algorithm`, `diff_digest`, and the exact diff artifact path and
  command.

Require `merge_base_sha == target_sha` before final landing review. If it does
not, reconcile the branch with the current target first. Materialize one
deterministic two-tree diff and hash the file:

```bash
git diff --binary --full-index --no-color --no-ext-diff --no-textconv \
  --no-renames --diff-algorithm=myers --unified=3 \
  "$TARGET_SHA" "$HEAD_SHA" -- > "$DIFF_PATH"
shasum -a 256 "$DIFF_PATH"
```

Do not substitute a three-dot-only diff; it can hide current-target content
that a stale branch lacks. Then:

1. Materialize the full artifact or diff at an immutable commit.
2. Give the reviewer the exact paths and every identity field above.
3. Confirm the reviewer had access to every file it claims to have reviewed.
4. Store the complete report durably; never slice or summarize the findings
   before handing them to the actor.
5. Record expected, received, answered, and unresolved finding counts plus a
   digest of the complete report.
6. Treat blocked, truncated, missing, or unread reviews as `UNCHECKED`, not clean.

Integrator-owned tracker, index, release-note, and repository-required report
or presentation edits are part of the pull-request diff. Commit them before
final review and push only when authorized. Require a clean worktree, record
the identity fields and deterministic diff digest above, and review that exact
input pair. A later commit, rebase, changed target SHA, changed merge base, or
changed digest invalidates the affected verdict and requires review again.

For plans that control many merges or change result-bearing surfaces, run an
independent adversarial review of the plan before execution. Use separate source
truth, operational safety, and domain-risk perspectives, a challenge round, and
a fresh judge. Reviewers receive raw sources, not the author's conclusions.

## 9. Land changes serially

For each item, in order:

1. If network access is authorized, re-fetch the remote target and confirm it
   is healthy. Without current-target evidence, do not merge.
2. Re-check open pull requests, active claims, and the item's premise.
3. Start from a fresh worktree or safely rebase a verified clean worktree onto
   the current target. Do not use a destructive reset over unknown local work.
4. Re-run stale path, constant, count, and line-reference searches.
5. Integrate the worker commit without taking whole-file versions of shared
   records from a stale branch.
6. Apply the integrator-owned shared-record changes.
7. Run targeted checks and the repository's full required gate.
8. Compare the current diff with the declared write set, including deletions.
9. Commit the complete final diff and push only when authorized; require a
   clean worktree.
10. Review the exact `target_sha`, `merge_base_sha`, `head_sha`, head tree, and
    deterministic diff digest defined in §8; resolve every finding. Local
    immutable-commit review is valid when push is not authorized.
11. Apply the recorded merge grant. If merge is not authorized, stop in a
    reviewed ready state and say what remains.
12. Merge only while the target remains healthy.
13. Fetch the merged target and verify content that this item added, not only
    file existence or record IDs.
14. Update the journal and the separate operational records. Mark controller
    liveness `STOPPED` whenever the controller stops. End its execution lease
    only after process inspection confirms no command or agent remains. Release
    an item path reservation after merge/content verification or an explicit
    terminal outcome such as abandonment, supersession, or named transfer.
    Preserve a separately owned reservation while an active branch or pull
    request still claims the path; repeat its `reservation_id`, exact paths,
    owner, state, expiry or takeover condition, release time and reason, and
    next action rather than pretending the old controller is running.

Never merge two items simultaneously when both ultimately edit the same shared
record. A clean merge result does not prove current content survived it.

## 10. Close the run honestly

The morning handoff must report:

- the pinned starting commit and final observed remote commit;
- every source row's disposition;
- completed, skipped, blocked, failed, and unchecked items;
- observed commits, pull requests, and merges;
- verification by layer, including excluded suites;
- assumptions used;
- owner decisions with a recommendation;
- open claims, worktrees, or processes that remain;
- the safest next action.

Do not say the queue is clear while owner-gated, excluded, or unchecked work
remains. Do not turn an un-run unit into a negative or inconclusive result.

## 11. Preflight the plan adversarially

Before a real unattended run, ask independent reviewers to challenge:

1. **Source truth and coverage:** Were all rows counted? Are stale and split
   classifications supported by current evidence?
2. **Operational safety:** Are authority, dependencies, budgets, file claims,
   recovery, review artifacts, and serial landing internally consistent?
3. **Domain risk:** Are statistics, rendered surfaces, pre-registrations,
   product rules, and owner-only judgements assigned the correct gates?

Have reviewers challenge one another's findings, then give the raw reports and
challenge record to a fresh judge. Fix material findings and re-run the judge on
the final plan. A panel that reviewed a superseded commit is not evidence about
the plan that will execute.

## Anti-patterns

- Trusting a queue's `live` label as proof an item is executable.
- Reading only index summaries and not the linked source files.
- Trusting a detailed prompt over newer code or standing decisions.
- Reporting complete coverage without comparing source and classified ID sets.
- Calling a whole row owner-blocked when an autonomous slice exists.
- Running writers in parallel because their topics differ while their files overlap.
- Letting every worker edit the same tracker or index.
- Starting an item after its latest safe start.
- Retrying a still-running process because output paused.
- Counting a blocked or truncated reviewer as clean.
- Reviewing before integrator-owned edits are committed.
- Merging against a red target or without an explicit merge grant.
- Cleaning worktrees while other sessions are active.
