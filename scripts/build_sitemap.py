#!/usr/bin/env python3
"""
MyClinical Growth — sitemap builder.

Writes sitemap.xml at the repo root listing every public page plus a
per-item URL for every standing opportunity, every grant, and every
portal guide. Runs in the GitHub Actions workflow right after the poll
so search engines see new tenders quickly.

Past-deadline opportunities and grants are excluded automatically so
the sitemap doesn't push stale URLs (which Google deprecates on crawl).
"""

import datetime as dt
import json
from pathlib import Path
from xml.sax.saxutils import escape

SITE = "https://growth.myclinical.co.uk"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Static pages with a sensible priority and change frequency.
STATIC = [
    ("/",              1.0, "daily"),
    ("/opportunities", 0.9, "hourly"),
    ("/grants",        0.9, "daily"),
    ("/directory",     0.7, "weekly"),
    ("/search",        0.6, "weekly"),
    ("/submit",        0.5, "monthly"),
    ("/bid-writers",   0.5, "monthly"),
    ("/capital",       0.5, "monthly"),
    ("/about",         0.5, "monthly"),
]


def _load(name):
    p = DATA / name
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _is_past(deadline):
    if not deadline:
        return False
    try:
        d = dt.date.fromisoformat(str(deadline).strip()[:10])
    except (ValueError, TypeError):
        return False
    return d < dt.date.today()


def _entries():
    today = dt.date.today().isoformat()

    for path, priority, freq in STATIC:
        yield f"<url><loc>{SITE}{path}</loc><lastmod>{today}</lastmod>" \
              f"<changefreq>{freq}</changefreq><priority>{priority:.1f}</priority></url>"

    # Per-opportunity URLs (curated + live), excluding past-deadline items.
    opps = []
    curated = _load("opportunities.json")
    if isinstance(curated, dict):
        opps += curated.get("opportunities", [])
    live = _load("opportunities-live.json")
    if isinstance(live, dict):
        opps += live.get("opportunities", [])

    # De-dup by id; prefer the later entry (live overrides curated if both).
    by_id = {}
    for o in opps:
        oid = o.get("id")
        if oid:
            by_id[oid] = o
    for o in by_id.values():
        if _is_past(o.get("deadline")):
            continue
        if (o.get("status") or "").lower() in ("closed", "completed", "awarded"):
            continue
        loc = f"{SITE}/opportunity#{escape(str(o['id']))}"
        yield f"<url><loc>{loc}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>"

    # Per-grant URLs, excluding past-deadline.
    grants_blob = _load("grants.json")
    if isinstance(grants_blob, dict):
        for g in grants_blob.get("grants", []):
            if _is_past(g.get("deadline")):
                continue
            if (g.get("status") or "").lower() in ("closed", "completed", "awarded"):
                continue
            if not g.get("id"):
                continue
            loc = f"{SITE}/opportunity#{escape(str(g['id']))}"
            yield f"<url><loc>{loc}</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>"

    # Per-portal-guide URLs.
    guides = _load("portal-guides.json")
    if isinstance(guides, dict):
        for slug in (guides.get("portals") or {}).keys():
            loc = f"{SITE}/directory#portal-{escape(str(slug))}"
            yield f"<url><loc>{loc}</loc><changefreq>monthly</changefreq><priority>0.6</priority></url>"


def main():
    items = list(_entries())
    out = ROOT / "sitemap.xml"
    body = "\n".join("  " + item for item in items)
    out.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>\n",
        encoding="utf-8",
    )
    print(f"Wrote {out} ({len(items)} URLs)")


if __name__ == "__main__":
    main()
