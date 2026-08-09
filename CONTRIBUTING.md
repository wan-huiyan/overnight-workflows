# Contributing — publishing hygiene

These skills are often distilled from real client engagements. Before anything is pushed, a
**leak gate** checks for client / PII identifiers so engagement-specific details never ship to
this public repo. A second gate keeps every skill **description** inside the cap Claude Code
applies to its always-resident skill listing.

## What runs automatically

**CI** (`.github/workflows/ci.yml`) runs seven checks on every PR and push:

1. `.github/scripts/validate_plugins.py --self-test` — marketplace/plugin
   structure plus the payload/version release ledger and its negative controls.
2. `scripts/leak_scan.sh` — low-false-positive generic patterns: Salesforce custom fields
   (`__c` / `__r`), API keys / tokens, and real email addresses. A hit fails the check.
3. `scripts/check_skill_descriptions.py` — the **skill-description cap gate**.
4. `scripts/check_large_queue_guidance.py --self-test` — durable state,
   recovery, exact routing contract, and installed-package negative controls.
5. `scripts/validate_panel_inputs.py --self-test` — immutable diff and installed
   evidence-snapshot omission/drift controls.
6. `scripts/test_publish_codex_install.py` — focused publisher unit tests.
7. `scripts/publish_codex_install.py self-test` — deterministic fake-exchange
   publication, rollback, concurrency, drift, and capability controls.

## The skill-description cap gate

Claude Code injects every model-invocable skill's `name` + `description` into context on
**every turn**, capped per skill at **1,536 chars** (`skillListingMaxDescChars`). Past the cap
the harness keeps `description[:1535]` and appends an ellipsis — it cuts **mid-word**, with no
warning. A description is trigger text, so every `use when the user says "…"` phrase living
past char 1535 is already dead: the skill cannot fire on it and nothing reports the loss.

```bash
python3 scripts/check_skill_descriptions.py . --no-color --triggers
```

Exit 0 = clean, 1 = a description is over the cap **or** broken by a line wrap, 2 = bad path.
`--triggers` lists the quoted phrases that fall past the cut. When trimming: cut prose and
cross-references (they belong in the SKILL.md body, which lazy-loads only when the skill fires),
never trigger vocabulary or a NOT-for list; and land ~30-50 chars under the cap so the next edit
does not re-break it.

**Never wrap mid-token.** A `description: >` / `description: |` scalar joins its lines with a
SPACE, so `foo-\n  bar` is injected as `foo- bar` — a corrupted skill name or domain term, at an
unchanged character count that no length check can see. `textwrap.wrap()` breaks on hyphens **by
default**; pass `break_on_hyphens=False`. The gate reports these under `BROKEN BY LINE-WRAP`.

**Under the cap is not the same as visible.** The cap is only the per-skill limit. A second
limit, `skillListingBudgetFraction` (1% of the context window), caps the listing as a whole; when
the total exceeds it the harness collapses whichever entries no longer fit down to bare names,
ranked by usage rather than by length. Getting a description under the cap guarantees it is no
longer *truncated* — not that it is *injected*. Check the `Listing budget` section of the gate
output, and check it against the whole install, not just this repo:

```bash
python3 scripts/check_skill_descriptions.py . --no-color --context 1000000
```

### Before/after a trim: diff the trigger surface, don't just count words

```bash
python3 scripts/check_skill_descriptions.py --compare main:path/to/SKILL.md path/to/SKILL.md
python3 scripts/score_trigger_coverage.py --old-ref main
```

`--compare` flags `DROPPED` / `NARROWED` / `REWORDED` triggers. `NARROWED` is the one to read by
hand: turning `trigger on X — and separately, watch for Y` into `trigger on X WHOSE Y` keeps the
identical word set, so every word-overlap score is blind to it, while the trigger now only fires
for users who already diagnosed Y. `score_trigger_coverage.py` is the coverage harness and
`scripts/eval/description-trigger-suite.json` its committed suite — if a PR quotes coverage
numbers, they must come from a committed harness a reviewer can re-run. Word overlap is
diagnostic only. The nonzero overnight route gate is the exact prompt-class-to-route contract
inside `check_large_queue_guidance.py`, backed by the real Codex loader inventory probe when
Codex 0.147.0 is locally available.

The gate script is vendored from `wan-huiyan/context-police`; fix it upstream and re-vendor
rather than editing the copy here.

## One-time local setup (recommended)

Enable the committed pre-push hook so both gates run **before** anything leaves your machine:

```bash
git config core.hooksPath .githooks
cp .leakterms.example .leakterms      # then add YOUR real client / brand / project names
```

`.leakterms` is gitignored — it holds the names only you know are sensitive (client brands,
dataset / project ids, your username), one `grep -E` regex per line. **Never commit it.** The
generic CI patterns plus your local `.leakterms` together catch the *enumerable* leaks; a first
public publish still deserves a human / LLM semantic read for client-shaped names a fixed
pattern can't enumerate.

## If the gate fires

Sanitize the flagged content (replace the identifier with a neutral placeholder), or — for a
genuine false positive — narrow the pattern or add an exclusion in `scripts/leak_scan.sh`.
