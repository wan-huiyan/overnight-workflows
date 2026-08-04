# Contributing — publishing hygiene

These skills are often distilled from real client engagements. Before anything is pushed, a
**leak gate** checks for client / PII identifiers so engagement-specific details never ship to
this public repo. A second gate keeps every skill **description** inside the cap Claude Code
applies to its always-resident skill listing.

## What runs automatically

**CI** (`.github/workflows/ci.yml`) runs three checks on every PR and push:

1. `.github/scripts/validate_plugins.py` — marketplace + plugin structure.
2. `scripts/leak_scan.sh` — low-false-positive generic patterns: Salesforce custom fields
   (`__c` / `__r`), API keys / tokens, and real email addresses. A hit fails the check.
3. `scripts/check_skill_descriptions.py` — the **skill-description cap gate**.

## The skill-description cap gate

Claude Code injects every model-invocable skill's `name` + `description` into context on
**every turn**, capped per skill at **1,536 chars** (`skillListingMaxDescChars`). Past the cap
the harness keeps `description[:1535]` and appends an ellipsis — it cuts **mid-word**, with no
warning. A description is trigger text, so every `use when the user says "…"` phrase living
past char 1535 is already dead: the skill cannot fire on it and nothing reports the loss.

```bash
python3 scripts/check_skill_descriptions.py . --no-color --triggers
```

Exit 0 = clean, 1 = at least one description over the cap, 2 = bad path. `--triggers` lists the
quoted phrases that fall past the cut. When trimming: cut prose and cross-references (they
belong in the SKILL.md body, which lazy-loads only when the skill fires), never trigger
vocabulary or a NOT-for list; and land ~30-50 chars under the cap so the next edit does not
re-break it. Also **wrap the block scalar at ~110 cols and never mid-token** — YAML folds
`foo-\n  bar` into `foo- bar`, which silently corrupts skill names inside the description.

The script is vendored from `wan-huiyan/context-police`; fix it upstream and re-vendor rather
than editing the copy here.

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
