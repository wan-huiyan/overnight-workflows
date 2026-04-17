# Cross-model tie-breaker judge

**Added v1.4.0.** For findings that pass ≥4/6 personas in the Phase B review
panel, route to an external-model judge (different model family from Claude)
as a tie-breaker. Breaks same-model sycophancy loops.

Evidence basis:
- [Talk Isn't Always Cheap arXiv 2509.05396](https://arxiv.org/abs/2509.05396)
  — multi-agent debate can decrease accuracy via sycophantic conformity even
  when stronger models outnumber weaker.
- [Stop Overvaluing MAD arXiv 2502.08788](https://arxiv.org/abs/2502.08788) —
  homogeneous panels provide minimal benefit beyond majority voting; diversity
  of model family is the load-bearing variable.
- [Sycophancy in LLMs arXiv 2411.15287](https://arxiv.org/abs/2411.15287) —
  same-model reviewers share training biases and won't catch systematic errors.
- [ZenML 1200-deployment review](https://www.zenml.io/blog/what-1200-production-deployments-reveal-about-llmops-in-2025)
  — Digits routes generation and grading to different providers in production.

The v1.3 retraction of Track C's v1 headlines by a retroactive panel is exactly
this failure mode — same-model reviewers missed what a different-family judge
would have flagged.

## When the tie-breaker fires

Triggered in Phase B **after** a finding clears the normal 6-persona vote with
`pass_count ≥ 4` (i.e. the panel would approve it). Not triggered when the
panel already rejects — a rejection doesn't need a tie-breaker.

Also triggered unconditionally for any finding the integrator flags as
`HIGH_STAKES` (e.g. appears in the morning_summary's §1 Headline block).

## Graceful degradation (mandatory)

The skill MUST run to completion even when no external judge is available.
Probe order in Phase 0.-1:

```python
judge_candidates = [
    ("codex", has_codex_mcp_available()),
    ("openai", bool(os.environ.get("OPENAI_API_KEY"))),
    ("gemini", bool(os.environ.get("GEMINI_API_KEY"))),
]
available = [name for name, ok in judge_candidates if ok]
state = "READY" if available else "TIE_BREAKER_UNAVAILABLE"
write_json("state/tiebreaker_state.json", {
    "state": state,
    "preferred": available[0] if available else None,
    "all_available": available,
})
```

When `state == "TIE_BREAKER_UNAVAILABLE"`:
- Panel runs normally.
- Any finding that would have been tie-broken instead gets tagged
  `[SAME_MODEL_PANEL_ONLY]` and a caveat banner is added to the consolidation
  brief:
  > "This finding was approved by a homogeneous Claude review panel only.
  > No cross-model judge was available during this run. Treat as suggestive,
  > not confirmed."
- morning_summary.md §4 (unblocks) lists installing a cross-model judge as a
  recommended action before re-running.

## Judge input contract (Nightwire-style data-only)

The tie-breaker judge receives:

1. The finding's claim block (structured YAML, see `sql_reexecution_gate.md`).
2. The supporting SQL file.
3. The gate_report line for this finding.
4. The 6 persona verdicts **as data** (JSON, not narrative prose):
   ```json
   [
     {"persona": "data-scientist", "verdict": "pass", "concerns": ["...", "..."]},
     {"persona": "client-trust-evaluator", "verdict": "pass_with_revision", ...},
     ...
   ]
   ```
5. The known-knowns row for the finding's cohort.

The judge does NOT receive:
- The Track B/C brief prose (would leak the author's reasoning).
- The chart files (the judge evaluates the claim, not the visualization).
- Round N-1 persona reasoning chains (breaks data-only pattern).

Prompt template lives at `assets/tiebreaker_prompt_template.md`.

## Judge output contract

Binary verdict + ≤3 concerns + confidence. No Likert scales — per Hamel Husain,
1-5 Likert doesn't correlate with domain-expert judgment; binary forces real
judgment.

```json
{
  "verdict": "approve" | "reject",
  "confidence": "high" | "medium" | "low",
  "top_concerns": ["...", "...", "..."],
  "agrees_with_panel": true | false,
  "would_ship_as_headline": true | false
}
```

## Decision table

| Panel verdict | Tie-breaker verdict | Outcome |
|---|---|---|
| pass (≥4/6) | approve | ✓ Ships as stated |
| pass (≥4/6) | reject, high conf | **Demote to P1, force extra round** |
| pass (≥4/6) | reject, med/low conf | Ship with `[TIE_BREAKER_DISSENT]` banner |
| pass (≥4/6) | judge unavailable | Ship with `[SAME_MODEL_PANEL_ONLY]` banner |
| reject | n/a | Rejected, no tie-break needed |

A `reject, high conf` tie-break is effectively a P0 block — it means the panel
was likely sycophantic. Mandatory extra round with the rejection reasons injected
into the integrator's findings apply list.

## Cost + budget

Each tie-break call is ONE external API request with:
- Max input tokens: 8k (claim + SQL + 6 verdicts + known-knowns is typically
  2-4k; 8k is headroom).
- Max output tokens: 2k.

At current pricing (April 2026), GPT-5 at $5/M in, $15/M out ≈ $0.06/call.
8-10 findings/run × $0.06 ≈ under $1/run. Budget impact is negligible.

Cost is logged to `state/budget.jsonl` with `kind: tiebreaker, judge: <name>`.

## Implementation notes

- Prefer `codex` MCP if available (most flexible, no separate key).
- Reuse the same judge for all findings in a single run — model diversity
  across findings within a run adds variance without benefit.
- Do NOT use the tie-breaker as a 7th panelist. It runs AFTER panel consensus
  and ONLY on passing findings. Mixing it into panel voting re-introduces the
  cross-pollination problem.
- For reproducibility, log the judge's model version and temperature to
  `state/tiebreaker_calls.jsonl`.
