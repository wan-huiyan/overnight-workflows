# SQL re-execution gate (pre-panel)

**Added v1.4.0.** Every finding must have its supporting SQL re-executed and its
claimed numbers verified **before** the review panel sees it. Panel time is expensive;
wasting it on claims that don't reconcile against fresh BQ is malpractice.

This closes the S91 failure mode where Finding 1's n-count shipped as 351 in the
brief but a verification query returned 62 (5.7× overstatement), not caught until
post-hoc review. Also addresses the v1.3 4.4× → 3.17× effect-size shrinkage — that
shrinkage happened because significance testing ran *after* the panel.

Evidence basis:
- [CRITIC arXiv 2305.11738](https://arxiv.org/abs/2305.11738) — LLMs are unreliable
  self-verifiers without external tool grounding.
- [FIRE NAACL 2025](https://aclanthology.org/2025.findings-naacl.158.pdf) —
  iterative verify beats single-pass post-hoc.
- [Applied LLMs](https://applied-llms.org/) — "run the code, verify runtime state."
- [Nightwire pattern (hermes-agent#406)](https://github.com/NousResearch/hermes-agent/issues/406)
  — verifier runs fresh with data-only input.

## Contract

Every finding artifact (`state/findings/NNN.md`) MUST carry structured fields:

```yaml
finding_id: F007
claim:
  headline: "International × UG × In-Process 3.17× tenure drop"
  effect_metric: "relative_risk"
  effect_value: 3.17
  effect_ci_low: 2.10
  effect_ci_high: 4.79
  n_count: 378
  n_denominator: 12947
supporting_sql:
  path: state/queries/F007.sql
  sha256: <sha256-of-SQL-file>
  expected_shape: "1 row, columns [rr, ci_low, ci_high, n, n_denom]"
```

No `supporting_sql` block → finding is rejected at intake (not even queued for
verification).

## Gate procedure (runs between Phase A handoff and Phase B round 1)

For each finding in `state/findings/`:

1. **Re-run the SQL.** Fresh dispatch, no cache, using `scripts/bq_budget.py`
   wrapper so cost is counted. Output goes to `state/verify/<finding_id>.csv`.
2. **Compare claimed vs returned** on every field present in `claim`:
   ```
   rel_diff(x) = abs(claimed - returned) / max(abs(returned), 1e-9)
   ```
   Gate policies:
   - `n_count`, `n_denominator`: **tolerance 0.05** (5%). Integer counts
     shouldn't drift — anything > 5% is a SQL bug, a stale cache, or a
     different filter. Reject with `STAT_MISMATCH(n_count)`.
   - `effect_value`: **tolerance 0.15** (15%). Allows for tiny re-runs of
     bootstraps that re-sample differently, rejects the 4.4× → 3.17× class.
   - `ci_low`, `ci_high`: reject if sign changes (CI crossed zero in re-run
     but not in claim, or vice versa). Label `CI_SIGN_FLIP`.
3. **Write `state/verify/gate_report.jsonl`** — one line per finding with
   `{finding_id, status ∈ [PASS, STAT_MISMATCH, CI_SIGN_FLIP, SQL_ERROR], delta_map}`.
4. **Findings with any non-PASS status are auto-retracted** before the panel
   sees them. They land in `state/findings/retracted/` with the gate report
   appended. They are NOT deleted — retracted findings are visible to the
   panel's `contradiction-hunter` persona to see what the tracks tried to
   claim vs what held up.

## Multiple-hypothesis correction (item 7)

After all findings pass the per-finding gate, apply Benjamini-Hochberg over the
full surviving set of p-values per track:

```python
from statsmodels.stats.multitest import multipletests
reject, q_values, *_ = multipletests(p_values, alpha=0.10, method="fdr_bh")
```

- Use FDR α = 0.10 (insight-discovery tolerates modest FDR; the panel catches
  downstream).
- Findings with `q > 0.10` get tagged `[MHT_NONSIGNIFICANT]` but are NOT
  auto-retracted — they enter the panel with the tag so the panel can decide
  whether exploratory-value justifies inclusion.
- Bonferroni (stricter) is the right policy for prior-validation run mode.
  Controlled via `scoping/config.yaml: mht_policy ∈ {bh, bonferroni}`.

Evidence: [LLM Hacking arXiv 2509.08825](https://arxiv.org/pdf/2509.08825) —
~31% of LLM-generated hypotheses reach incorrect conclusions; free subgroup
choice is effectively p-hacking without correction.

## Dispatch shape

Run in the orchestrator between Phase A completion and Phase B round 1:

```python
gate_results = []
for finding_md in glob("state/findings/*.md"):
    claim = parse_claim_block(finding_md)
    if not claim.supporting_sql:
        gate_results.append({"finding_id": claim.id, "status": "MISSING_SQL"})
        continue
    result = run_bq(claim.supporting_sql.path)  # via bq_budget.py
    delta = compare(claim, result)
    gate_results.append({"finding_id": claim.id, **delta})

write_jsonl("state/verify/gate_report.jsonl", gate_results)
apply_bh_correction(gate_results, alpha=0.10)
move_retracted_findings()
```

## What the panel sees

The panel reads from `state/findings/` (survivors only) plus
`state/findings/retracted/gate_report.jsonl` as read-only context. The
`contradiction-hunter` persona (see phase_b_review_loop.md) is specifically
tasked with checking that surviving findings don't contradict the retraction
reasons of their cousins.

## Failure modes this gate does NOT catch

- Selection bias in the SQL itself (wrong filter, wrong join). That's the
  data-scientist + data-analyst personas' job during the panel.
- Simpson's paradox (effect flips at population level). Deferred to v1.5.
- Label-window leakage. Track-specific check; lives in `phase_a_tracks.md`.

The gate is narrow: **does the claim number actually come out of the claim SQL?**
Everything else is downstream.
