"""Deterministic Markdown → HTML rendering for overnight-run deliverables.

Pure function: same Markdown in → byte-identical HTML out. No LLM in the loop,
no network calls, no CDN dependencies. Idempotent.

Outputs: 5 HTMLs + index.html under <run_dir>/html/:
    - consolidation.html  (client-facing)
    - brief_b.html        (Track B traceability)
    - brief_c.html        (Track C traceability)
    - review_panel_final.html  (aggregated review panel HTML)
    - workflow_learnings.html
    - index.html          (landing page linking all five)

Charts referenced from Markdown as `![](path/to/chart.png)` are copied relative
to the HTML so `<img src>` works when the folder is zipped and emailed.

Usage:
    python render_html.py <run_dir>
"""
from __future__ import annotations
import shutil
import sys
from pathlib import Path

try:
    import markdown2
except ImportError:
    print("Install: pip install markdown2", file=sys.stderr)
    sys.exit(1)

MARKDOWN_EXTRAS = [
    "tables",
    "fenced-code-blocks",
    "header-ids",
    "strike",
    "task_list",
    "footnotes",
    "code-friendly",
]

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="stylesheet" href="{css_relpath}">
</head>
<body>
<article class="overnight-brief">
{body}
</article>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Overnight Run — {run_id}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<article class="overnight-brief">
<h1>Overnight Ah-Ha Insight Run — {run_id}</h1>
<p><strong>Primary deliverable (client-facing):</strong></p>
<ul>
<li><a href="consolidation.html">consolidation.html</a></li>
</ul>
<p><strong>Traceability:</strong></p>
<ul>
<li><a href="brief_b.html">Track B brief</a></li>
<li><a href="brief_c.html">Track C brief</a></li>
<li><a href="review_panel_final.html">Review panel final</a></li>
<li><a href="workflow_learnings.html">Workflow learnings (internal)</a></li>
</ul>
<p><em>Rendered deterministically from Markdown by render_html.py.</em></p>
</article>
</body>
</html>
"""

# (source_markdown_path, output_html_filename, title)
RENDER_MANIFEST = [
    ("consolidation.md",            "consolidation.html",       "Consolidation — Client Brief"),
    ("track_b/brief.md",            "brief_b.html",             "Track B — Traceability"),
    ("track_c/brief_c_final.md",    "brief_c.html",             "Track C — Traceability"),
    ("workflow_learnings.md",       "workflow_learnings.html",  "Workflow Learnings"),
]


def render_one(md_path: Path, out_path: Path, title: str, css_relpath: str = "style.css") -> None:
    """Convert one Markdown file to HTML using the template."""
    md_src = md_path.read_text(encoding="utf-8")
    body = markdown2.markdown(md_src, extras=MARKDOWN_EXTRAS)
    html = HTML_TEMPLATE.format(title=title, body=body, css_relpath=css_relpath)
    out_path.write_text(html, encoding="utf-8")
    print(f"Rendered {out_path.relative_to(out_path.parent.parent)}")


def copy_dashboard_css(run_dir: Path, html_dir: Path) -> None:
    """Copy project-level CSS into html/ for brand consistency.

    Looks in several likely locations; falls back to a minimal inline stylesheet
    if none found."""
    candidates = [
        Path("cr_client_dashboard/static/css/style.css"),  # Flask dashboards
        Path("static/css/style.css"),
        Path("web/static/style.css"),
        Path("assets/style.css"),
    ]
    for cand in candidates:
        if cand.exists():
            shutil.copy(cand, html_dir / "style.css")
            print(f"Copied CSS from {cand}")
            return
    # Fallback: minimal inline stylesheet
    (html_dir / "style.css").write_text(_MINIMAL_CSS, encoding="utf-8")
    print("No project CSS found; wrote minimal fallback stylesheet.")


def copy_charts(run_dir: Path, html_dir: Path) -> None:
    """Copy charts from track_b/charts/ and track_c/charts/ to html/charts/.

    This lets <img src="../charts/..."> resolve correctly when the html/ folder
    is zipped for emailing."""
    charts_dst = html_dir / "charts"
    charts_dst.mkdir(exist_ok=True)
    for track in ("track_b", "track_c"):
        src = run_dir / track / "charts"
        if src.exists():
            for png in src.glob("*.png"):
                shutil.copy(png, charts_dst / f"{track}_{png.name}")
    print(f"Charts copied to {charts_dst.relative_to(run_dir)}")


def render_review_panel_final(run_dir: Path, html_dir: Path) -> None:
    """Aggregate per-track review-panel HTML into a single index page.

    This links out to each per-round report.html under track_X/review/round_N/
    and summarizes the score progression."""
    links = []
    for track in ("track_b", "track_c"):
        review_dir = run_dir / track / "review"
        if not review_dir.exists():
            continue
        for round_dir in sorted(review_dir.glob("round_*")):
            report = round_dir / "report.html"
            if report.exists():
                dest = html_dir / f"{track}_{round_dir.name}_report.html"
                shutil.copy(report, dest)
                links.append(f'<li><a href="{dest.name}">{track} {round_dir.name}</a></li>')
    # Also consolidation review
    consolidation_review = run_dir / "consolidation_review"
    if (consolidation_review / "report.html").exists():
        shutil.copy(consolidation_review / "report.html", html_dir / "consolidation_review.html")
        links.append('<li><a href="consolidation_review.html">Consolidation review (final)</a></li>')

    body = f"""<h1>Review Panel — All Rounds</h1>
<p>Per-track per-round agent-review-panel reports.</p>
<ul>
{chr(10).join(links) if links else "<li>No reviews found.</li>"}
</ul>"""
    html = HTML_TEMPLATE.format(title="Review Panel Final", body=body, css_relpath="style.css")
    (html_dir / "review_panel_final.html").write_text(html, encoding="utf-8")
    print("Rendered review_panel_final.html")


def main(run_dir_str: str) -> int:
    run_dir = Path(run_dir_str)
    html_dir = run_dir / "html"
    html_dir.mkdir(exist_ok=True)

    copy_dashboard_css(run_dir, html_dir)
    copy_charts(run_dir, html_dir)

    for src_rel, out_name, title in RENDER_MANIFEST:
        src = run_dir / src_rel
        if src.exists():
            render_one(src, html_dir / out_name, title)
        else:
            print(f"Skipping {src_rel} (not found)")

    render_review_panel_final(run_dir, html_dir)

    # Index
    run_id = run_dir.name
    (html_dir / "index.html").write_text(
        INDEX_TEMPLATE.format(run_id=run_id), encoding="utf-8"
    )
    print("Rendered index.html")
    print(f"\nAll output in: {html_dir}")
    return 0


_MINIMAL_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
       max-width: 820px; margin: 2em auto; padding: 0 1em; color: #222; line-height: 1.55; }
h1, h2, h3 { color: #111; }
h1 { border-bottom: 2px solid #333; padding-bottom: 0.3em; }
h2 { border-bottom: 1px solid #ccc; padding-bottom: 0.2em; margin-top: 2em; }
a { color: #0066cc; text-decoration: none; }
a:hover { text-decoration: underline; }
code { background: #f4f4f4; padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.95em; }
pre { background: #f4f4f4; padding: 1em; border-radius: 5px; overflow-x: auto; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: 1em 0; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.5em 0.8em; text-align: left; }
th { background: #f0f0f0; }
blockquote { border-left: 4px solid #0066cc; margin: 1em 0; padding: 0.5em 1em;
             background: #f9f9ff; color: #555; }
img { max-width: 100%; height: auto; }
.overnight-brief { max-width: 820px; }
"""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python render_html.py <run_dir>")
        print("  e.g. python render_html.py docs/overnight/2026-04-17")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
