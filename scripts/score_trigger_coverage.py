#!/usr/bin/env python3
"""Score a description trim against the committed trigger-coverage eval suite.

WHY THIS IS COMMITTED
    A PR that trims trigger text and then quotes four-decimal coverage numbers is asking
    the reviewer to take those numbers on faith unless the harness ships with it. This is
    that harness, and `scripts/eval/description-trigger-suite.json` is that suite. Run it
    and you get the same figures the PR quotes.

    Adapted from wan-huiyan/agent-review-panel (scripts/score_trigger_coverage.py), which
    scores one skill against one eval file; this variant walks a multi-skill suite so the
    whole PR table reproduces from a single command.

WHAT IT MEASURES
    For each prompt, the fraction of its content words that appear in the description the
    model can actually SEE.

    The baseline is the crux: BEFORE is `old_description[:1535]`, NOT the full old text.
    The harness truncates at the cap, so scoring against the full oversized source compares
    the new description to text the model never read, and makes every honest trim look like
    a regression.

    Negatives are scored too. A trim can raise positive coverage just by getting wordier,
    which also pulls in prompts the skill must NOT fire on. `separation` (positive mean
    minus negative mean) is the number that cannot be gamed that way.

WHAT IT CANNOT MEASURE  (read this before trusting a green result)
    This is word overlap on a bag of words. It is structurally BLIND to trigger-condition
    restructuring: rewriting `trigger on X -- and separately, watch for Y` into
    `trigger on X WHOSE Y` keeps the identical word set and therefore scores IDENTICALLY,
    while the trigger now only fires for users who have already diagnosed Y.

    Use `check_skill_descriptions.py --compare OLD NEW` for that class of regression, and
    read the NARROWED / REWORDED rows yourself. Do not clear them with this number.

    It also says nothing about whether the description is injected at all. Under the cap
    means "not truncated", not "visible" -- see the listing-budget section of the gate.

USAGE
    python3 scripts/score_trigger_coverage.py --old-ref main
    python3 scripts/score_trigger_coverage.py --old-ref main --markdown
    python3 scripts/score_trigger_coverage.py --old-ref main --json
    python3 scripts/score_trigger_coverage.py --old-ref main --skill funnel-lever-vs-predictor-deleaked-forward-gap

    Exit 0 always; this reports, it does not gate. The gate is check_skill_descriptions.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

CAP = 1536
DEFAULT_SUITE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "eval", "description-trigger-suite.json")

# Deliberately small and explicit: a large stopword list is a free parameter that can be
# tuned until the numbers look good. Keep it to function words only, and keep it here in
# the repo so a reviewer can see exactly what was excluded.
STOP = set("""
a an the this that these those i my me you your can could do does did is are was were be
been am on in of to for with and or but if it its as at by from want need get got have has
make please before after so we our us they them then than about just really sure into out
up down over all any some no not
""".split())


def words(s: str) -> set:
    """Content words: alphabetic (hyphens kept, so `point-in-time` stays one token)."""
    return {w for w in re.findall(r"[a-z][a-z-]{2,}", s.lower())} - STOP


def read_description(spec: str) -> str:
    """Read a SKILL.md description from a path or a `git-ref:path` spec."""
    if ":" in spec and not os.path.exists(spec):
        ref, _, path = spec.partition(":")
        r = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"git show {ref}:{path} failed: {r.stderr.strip()}")
        text = r.stdout
    else:
        text = open(spec, encoding="utf-8").read()
    if not text.startswith("---"):
        sys.exit(f"no frontmatter in {spec}")
    end = re.search(r"^---\s*$", text[4:], re.M)
    if not end:
        sys.exit(f"unterminated frontmatter in {spec}")
    fm = text[4:4 + end.start()]
    m = re.search(r"^description:[ \t]*[|>][-+]?\d*[ \t]*\n((?:[ \t]+.*\n?)*)", fm, re.M)
    if not m:
        sys.exit(f"no folded/block description in {spec}")
    body = " ".join(l.strip() for l in m.group(1).splitlines() if l.strip())
    return re.sub(r"\s+", " ", body).strip()


def score_one(name: str, entry: dict, old_ref: str) -> dict:
    path = entry["path"]
    old_full = read_description(f"{old_ref}:{path}")
    new = read_description(path)
    # The whole point: compare against what was VISIBLE, not what was authored.
    old = old_full if len(old_full) <= CAP else old_full[:CAP - 1]

    ow, nw = words(old), words(new)

    def cov(prompt: str, dw: set) -> float:
        pw = words(prompt)
        return len(pw & dw) / len(pw) if pw else 0.0

    pos, neg = entry["positive"], entry["negative"]
    rows = [(p, cov(p, ow), cov(p, nw)) for p in pos]
    better = sum(1 for _, o, n in rows if n > o + 1e-9)
    worse = sum(1 for _, o, n in rows if n < o - 1e-9)
    same = len(rows) - better - worse

    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    pm_o, pm_n = mean([o for _, o, _ in rows]), mean([n for _, _, n in rows])
    nm_o, nm_n = mean([cov(p, ow) for p in neg]), mean([cov(p, nw) for p in neg])

    return {
        "skill": name, "path": path,
        "old_chars_authored": len(old_full), "old_chars_visible": len(old),
        "new_chars": len(new), "headroom": CAP - len(new),
        "positives": len(pos), "negatives": len(neg),
        "better": better, "same": same, "worse": worse,
        "positive_mean": {"before": round(pm_o, 4), "after": round(pm_n, 4)},
        "negative_mean": {"before": round(nm_o, 4), "after": round(nm_n, 4)},
        "separation": {"before": round(pm_o - nm_o, 4), "after": round(pm_n - nm_n, 4)},
        "regressions": [
            {"prompt": p, "before": round(o, 3), "after": round(n, 3),
             "dropped_words": sorted((words(p) & ow) - nw)}
            for p, o, n in rows if n < o - 1e-9
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", default=DEFAULT_SUITE, help="path to the eval suite JSON")
    ap.add_argument("--old-ref", default="main",
                    help="git ref holding the pre-trim SKILL.md files (default: main)")
    ap.add_argument("--skill", help="score only this skill")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--markdown", action="store_true", help="emit the PR table")
    a = ap.parse_args()

    suite = json.load(open(a.suite, encoding="utf-8"))["skills"]
    names = [a.skill] if a.skill else list(suite)
    for n in names:
        if n not in suite:
            sys.exit(f"{n} is not in {a.suite}")
    results = [score_one(n, suite[n], a.old_ref) for n in names]

    tot_better = sum(r["better"] for r in results)
    tot_same = sum(r["same"] for r in results)
    tot_worse = sum(r["worse"] for r in results)
    totals = {"positives": sum(r["positives"] for r in results),
              "negatives": sum(r["negatives"] for r in results),
              "better": tot_better, "same": tot_same, "worse": tot_worse}

    if a.json:
        print(json.dumps({"old_ref": a.old_ref, "cap": CAP,
                          "totals": totals, "skills": results},
                         indent=2, ensure_ascii=False))
        return 0

    if a.markdown:
        print("| skill | chars | pos cov | neg cov | separation (pos−neg) |")
        print("|---|---|---|---|---|")
        for r in results:
            print(f"| `{r['skill']}` | {r['old_chars_authored']:,} → **{r['new_chars']:,}** | "
                  f"{r['positive_mean']['before']:.3f} → {r['positive_mean']['after']:.3f} | "
                  f"{r['negative_mean']['before']:.3f} → {r['negative_mean']['after']:.3f} | "
                  f"{r['separation']['before']:+.3f} → {r['separation']['after']:+.3f} |")
        print(f"\nPrompt-level: {tot_better} better / {tot_same} same / {tot_worse} worse "
              f"({totals['positives']} positives, {totals['negatives']} negatives).")
        return 0

    for r in results:
        print(f"\n=== {r['skill']}")
        print(f"  authored {r['old_chars_authored']:,} chars, but only {r['old_chars_visible']:,} "
              f"were VISIBLE (cap {CAP})  ->  now {r['new_chars']:,} "
              f"({r['headroom']} headroom)")
        print(f"  positives ({r['positives']}):  {r['better']} better · {r['same']} same · "
              f"{r['worse']} worse")
        print(f"  positive mean coverage   {r['positive_mean']['before']:.4f} -> "
              f"{r['positive_mean']['after']:.4f}")
        print(f"  negative mean coverage   {r['negative_mean']['before']:.4f} -> "
              f"{r['negative_mean']['after']:.4f}   (lower is better)")
        print(f"  separation               {r['separation']['before']:+.4f} -> "
              f"{r['separation']['after']:+.4f}")
        for g in r["regressions"]:
            print(f"    REGRESSED {g['before']:.3f} -> {g['after']:.3f}  {g['prompt'][:70]}")
            print(f"      dropped words: {g['dropped_words']}")

    print(f"\nPrompt-level across {len(results)} skills: {tot_better} better / "
          f"{tot_same} same / {tot_worse} worse  "
          f"({totals['positives']} positives, {totals['negatives']} negatives)")
    print("\n  NOTE: word overlap cannot see trigger-condition restructuring. Run\n"
          "  check_skill_descriptions.py --compare and read the NARROWED rows too.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
