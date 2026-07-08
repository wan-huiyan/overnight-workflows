# Data Provenance Verifier
[![GitHub release](https://img.shields.io/github/v/release/wan-huiyan/data-provenance-verifier)](https://github.com/wan-huiyan/data-provenance-verifier/releases) [![Claude Code](https://img.shields.io/badge/Claude_Code-skill-orange)](https://claude.com/claude-code) [![license](https://img.shields.io/github/license/wan-huiyan/data-provenance-verifier)](LICENSE) [![last commit](https://img.shields.io/github/last-commit/wan-huiyan/data-provenance-verifier)](https://github.com/wan-huiyan/data-provenance-verifier/commits)

Catch fabricated data files before they contaminate your analysis. A Claude Code skill that audits external data files (CSV, JSON, Excel) for authenticity by checking provenance documentation, spot-checking against real APIs, and flagging suspect files.

## Why This Exists

AI coding agents sometimes generate plausible-looking data files instead of fetching real data — especially when the real source requires browser interaction, API keys, or authentication. The fabricated data looks right (correct column names, reasonable value ranges, seasonal patterns) but is numerically wrong. Experiments built on fabricated data produce specific quantitative claims that appear credible but are fiction.

## Quick Start

```
You: "I just inherited this repo from a previous session. Can you check if the data files are real?"

Claude: Runs three-layer audit:
  Layer 1 — Provenance docs: 2 files have .provenance.md, 3 are undocumented
  Layer 2 — API spot-check: weather data matches Open-Meteo within ±0.3°C (REAL),
            trends data has non-integer values at weekly boundaries (SUSPECT)
  Layer 3 — Creates .provenance.md for verified files, flags suspect ones

=== Data Provenance Audit ===
VERIFIED:    data/weather_daily.csv — Open-Meteo API, matched within ±0.4
FABRICATED:  data/search_trends.csv — MAE ~2 points vs real Google Trends
UNVERIFIED:  data/custom_features.csv — derived/computed, not external
MISSING:     data/holiday_calendar.csv — no .provenance.md, source unknown
```

## Installation

**Claude Code:**
```bash
# Git clone (recommended)
git clone https://github.com/wan-huiyan/data-provenance-verifier.git ~/.claude/skills/data-provenance-verifier
```

**Cursor (2.4+):**
```bash
mkdir -p .cursor/rules
# Copy SKILL.md content into .cursor/rules/data-provenance-verifier.mdc with alwaysApply: true
```

## What You Get

- **Three-layer audit**: provenance docs check, source API verification, documentation remediation
- **Automatic spot-checking** against Open-Meteo, Google Trends, FRED, ONS, and other public APIs
- **Fabrication heuristics**: detects suspiciously smooth patterns, wrong precision, missing fetch scripts
- **Provenance documentation**: creates `.provenance.md` files with source, date, method, verification status
- **Clear summary report** with VERIFIED / FABRICATED / UNVERIFIED / MISSING categories

## How It Works

| Layer | What it does | Example |
|-------|-------------|---------|
| **1. Provenance Check** | Scans for `.provenance.md` companion files, header comments, git blame | Flags files with no documented source |
| **2. Source Verification** | Fetches sample from real API, compares values within tolerance | Weather: ±0.5°C temp, ±1.0mm precip |
| **3. Remediation** | Creates provenance docs for verified files, backs up fabricated ones | Replaces fake CSV with real data |

## Fabrication Heuristics

The skill checks for these red flags:
- **Suspiciously smooth patterns** — real data has noise
- **Round numbers where decimals expected** (or vice versa)
- **Values close but not exact** — MAE 1-3 points = likely fabricated
- **No fetching script** but data file exists
- **Required library not installed** (e.g., `pytrends` missing but Google Trends CSV present)
- **Data extends beyond source limits** (e.g., daily Google Trends for >90 days when API gives weekly)
- **Google Trends**: weekly anchors should be integers, max should be exactly 100

## Limitations

- API spot-checks require network access — won't work offline
- Google Trends verification is limited to heuristics (no public API for exact comparison)
- Cannot verify proprietary/paywalled data sources automatically
- Git blame heuristics for AI authorship are imperfect — AI commits often use human author names

## Dependencies

- **Python 3** (standard library only — `urllib`, `json`, `ssl`) — for API spot-checks
- **git** — for blame/history analysis
- No external packages required

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-03-31 | Initial release. Three-layer audit, Open-Meteo/Trends/FRED verification, provenance templates. Tested against real and fabricated datasets. |

## License

MIT
