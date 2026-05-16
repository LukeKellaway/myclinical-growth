#!/usr/bin/env python3
"""
Render a preview daily-brief HTML from current curated data — used to
test the email design before the automated daily flow is live. Skips
the 'last 24h' filter so we get a full digest of everything we have.
"""
import sys, json, datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_digest import render

opps = json.loads((ROOT / "data" / "opportunities.json").read_text())["opportunities"]
grants = json.loads((ROOT / "data" / "grants.json").read_text())["grants"]
html = render(opps, grants)

out = ROOT / "preview_digest.html"
out.write_text(html)
print(f"Wrote: {out}")
print(f"Items: {len(opps)} opportunities, {len(grants)} grants")
print(f"Size: {len(html):,} chars")
