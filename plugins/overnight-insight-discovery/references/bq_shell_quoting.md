# Gotcha — BQ query shell-quoting: stdin pattern mandatory

**Added v1.6.0 (S98, 2026-04-21).** Track B's first SQL dispatch in v3 died
on this; file-based SQL inputs are now required.

---

## The pitfall

Never pass SQL as a `--query=` string when it contains embedded single-quotes,
especially via subprocess or inline in shell scripts:

```bash
# WRONG — shell will break on the embedded quotes, or worse, silently
# produce the wrong query after bash's quote-collapsing.
bq query --use_legacy_sql=false "SELECT ... WHERE country = 'United States'"
```

Failure modes:
- Bash parse error → loud, easy to diagnose.
- Silent quote-collapse → quiet, produces wrong query, wastes a dispatch and a
  BQ scan budget. This is the v3 Track B failure mode.

## The right pattern — stdin

Always use one of the stdin forms:

```bash
# Preferred — file-first
bq query --use_legacy_sql=false --format=json < queries/NNN_name.sql

# Equivalent
cat queries/NNN.sql | bq query --use_legacy_sql=false --format=json

# Here-string — acceptable for inline SQL with a variable
bq query --use_legacy_sql=false --format=json <<< "$SQL"
```

## Phase 0 dispatch prompt mandates file-based SQL

Both tracks' Phase 0 dispatch prompts must instruct subagents to use
file-based SQL inputs exclusively:

- Write SQL to `queries/NNN_{track}_{name}.sql` first.
- Execute via stdin.
- Store result at `results/NNN_{track}_{name}.{json,tsv}`.

File-first discipline aligns naturally — every query is auditable, replayable,
and can be probe-replayed later without re-deriving from chat history.

## One-liner for morning_summary §4

> bq stdin-quoting pattern mandated to prevent silent SQL mutation.
