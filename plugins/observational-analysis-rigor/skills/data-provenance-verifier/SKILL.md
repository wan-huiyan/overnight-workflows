---
name: data-provenance-verifier
description: |
  Verify that external data files in a repository are genuine (not fabricated or hallucinated
  by an AI agent). Use this skill whenever you encounter data files from external sources
  (CSV, JSON, Excel) that were committed by an automated process or a previous AI session.
  Also use when the user asks to "check data", "verify data sources", "audit data files",
  "check if this data is real", or when you notice data files without clear provenance
  documentation. Trigger proactively when inheriting a codebase with data/ directories
  containing external datasets (weather, search trends, economic indices, etc.) — these
  are the highest-risk files for AI fabrication. Also trigger when the user asks to add
  a .provenance.md file or document where data came from.
---

# Data Provenance Verifier

AI coding agents sometimes generate plausible-looking data files instead of fetching
real data — especially when the real source requires browser interaction, API keys,
or authentication. The fabricated data looks right (correct column names, reasonable
value ranges, seasonal patterns) but is numerically wrong. This is dangerous because
experiments run on fabricated data produce specific quantitative claims that appear
credible but are based on fiction.

This skill catches fabricated data before it contaminates analysis results.

## When This Applies

- A repository contains CSV/JSON data files from external sources (weather APIs,
  Google Trends, economic databases, public datasets)
- Data was committed by an AI agent or automated process
- No provenance documentation exists for data files
- The user asks to verify, audit, or document data sources
- You're picking up a project from a previous session and notice data files

## The Three-Layer Audit

### Layer 1: Provenance Documentation Check

Scan for data files and check if each has provenance documentation:

```bash
# Find all data files
find . -name "*.csv" -o -name "*.json" -o -name "*.xlsx" | grep -v node_modules | grep -v .git

# For each, check for companion provenance file
# Expected: data/file.csv -> data/file.provenance.md OR header comment in file
```

For each data file, check:
1. Does a `.provenance.md` companion file exist?
2. Does the CSV have a header comment documenting the source?
3. Does git blame show it was committed by an AI agent vs a human?
   (Check commit messages for "Claude", "AI", "automated", "Co-Authored-By: Claude"
   — AI work is often committed under a human's name via PR merge)

Flag files with no provenance as "unverified."

### Layer 2: Source Verification (spot-check)

For each unverified file, attempt to verify against the claimed source:

**Weather data (Open-Meteo, NOAA, Met Office):**
```python
# Fetch 10 days from the real API and compare
import urllib.request, json, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
url = f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lng}&daily=temperature_2m_mean,precipitation_sum&start_date={start}&end_date={end}"
# Compare: real values should match within ±0.5 for temperature, ±1.0 for precipitation
```

**Google Trends data:**
- Weekly anchor values should be integers (Google Trends returns whole numbers)
- The max value in any date range should be exactly 100 (Google normalizes to 100)
- If values are non-integer on weekly boundaries, the data was interpolated from
  something — check if the anchor points match real Google Trends exports
- Best verification: use browser automation to download from trends.google.com
  and compare the weekly values

**Economic/financial data (FRED, ONS, BoE):**
- Fetch a sample from the official API and compare
- Monthly data should have exact date alignment (1st of month for most sources)
- Values should match to the published decimal precision

**General heuristics for spotting fabricated data:**
- Suspiciously smooth patterns (real data has noise)
- Round numbers where the source provides decimals (or vice versa)
- Values that are close but not exact (mean absolute error 1-3 points = likely fabricated)
- No fetching script in the codebase but data file exists
- The library needed to fetch the data (e.g., pytrends) isn't installed
- Data extends beyond what the source provides (e.g., daily Google Trends for >90 days
  when the API only gives weekly for longer ranges)

### Layer 3: Documentation Remediation

For each verified file, create or update provenance documentation:

```markdown
# Provenance: {filename}

- **Source:** {URL or API endpoint}
- **Download date:** {ISO date}
- **Method:** {API call / browser export / manual download / unknown}
- **Processing:** {any transformations applied — interpolation, aggregation, filtering}
- **Verification:** {date verified, method used, match quality}

## History
- **v1 ({date}):** {status — REAL / FABRICATED / UNVERIFIED}
```

For files confirmed as fabricated:
1. Back up the fabricated file as `{filename}.FABRICATED.{ext}`
2. Fetch real data from the source (use browser automation if needed)
3. Replace the fabricated file with real data
4. Document the replacement in the provenance file

## Output

Produce a summary report:

```
=== Data Provenance Audit ===

VERIFIED (real data):
  data/weather_daily.csv — Open-Meteo API, matched within ±0.4

FABRICATED (replaced):
  data/search_trends.csv — MAE ~2 points vs real Google Trends, replaced with browser export

UNVERIFIED (no source to check against):
  data/custom_features.csv — appears to be derived/computed, not external

MISSING PROVENANCE:
  data/holiday_calendar.csv — no .provenance.md, source unknown

Actions taken: 1 file replaced, 2 provenance files created, 1 flagged for review
```

## Key Principle

Real data has traceable provenance. If you can't trace how a data file got into
the repository, treat it as suspect until verified. The cost of checking is minutes;
the cost of building analysis on fabricated data is weeks of wasted work and
potentially wrong business decisions.
