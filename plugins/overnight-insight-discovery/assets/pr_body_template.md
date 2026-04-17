# <project>: ah-ha insight brief — <YYYY-MM-DD> (v1 B-vs-C pilot)

> ⚠️ **DO NOT MERGE** without eyeballing `docs/overnight/<YYYY-MM-DD>/html/consolidation.html`.

## Summary

Two parallel tracks (B = LLM-autonomous, C = hybrid deterministic + LLM narration)
generated insight briefs for <target_term_label> at <client>. Both were reviewed
by `agent-review-panel` over <n> rounds and consolidated into a single
client-facing brief.

## Primary deliverable

- [Client-ready brief (Markdown)](../blob/<branch>/docs/overnight/<YYYY-MM-DD>/consolidation.md)
- [Client-ready brief (HTML)](../blob/<branch>/docs/overnight/<YYYY-MM-DD>/html/consolidation.html)

## Traceability artefacts

- [Landing page](../blob/<branch>/docs/overnight/<YYYY-MM-DD>/html/index.html)
- [Track B brief](../blob/<branch>/docs/overnight/<YYYY-MM-DD>/track_b/brief.md)
- [Track C brief](../blob/<branch>/docs/overnight/<YYYY-MM-DD>/track_c/brief_c_final.md)
- [Review panel final HTML](../blob/<branch>/docs/overnight/<YYYY-MM-DD>/html/review_panel_final.html)
- [Workflow learnings](../blob/<branch>/docs/overnight/<YYYY-MM-DD>/workflow_learnings.md)
- [Morning summary](../blob/<branch>/docs/overnight/<YYYY-MM-DD>/morning_summary.md)

## Cost tally

- BQ scanned: <X.XX> TB (cap: 5.0 TB)
- Wall-clock: <X.X> hr (cap: 8.0 hr)
- Tokens: <N> (~$<X.XX> USD)

## Findings in consolidation

**B-flavor (funnel leaks):**
- [ ] <finding 1 headline>
- [ ] <finding 2 headline>

**C-flavor (surprise patterns):**
- [ ] <finding 3 headline>
- [ ] <finding 4 headline>

Origin tags:
- <n> confirmed by both tracks `[B+C]`
- <n> from Track B only `[B-only]`
- <n> from Track C only `[C-only]`

## Unresolved P1 findings

Check each before sign-off:

- [ ] <P1 finding 1> — <severity rationale>
- [ ] <P1 finding 2> — <severity rationale>

(None if clean run.)

## Locked-file edits applied post-consolidation

<None, or list with commits + rationales. Per overnight-review-client-delivery
§ Locked-file escape hatch: every post-review edit to a locked file is
documented here.>

## Verification steps for the reviewer

1. Read `consolidation.html` end-to-end.
2. Run the 4 sanity-check queries in `morning_summary.md § 6` — verify numbers match.
3. Confirm no PII in chart screenshots.
4. Check review panel did NOT return unanimous Round 1 approval (soft warning — panel may have been sycophantic).
5. If satisfied, approve + merge.

## Known caveats to surface to client

- <caveat 1 from Caveats footer>
- <caveat 2>

## Next steps

See `workflow_learnings.md` for v2 recommendations. Tentative list:
- <item>
- <item>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
