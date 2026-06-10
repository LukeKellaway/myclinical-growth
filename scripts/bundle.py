#!/usr/bin/env python3
"""
MyClinical Growth — data bundler.

Reads the data/*.json files and writes assets/js/data.js as a single
window.GROWTH_DATA object. This lets the static site work both when served
(Netlify) and when opened directly from disk (file://), since <script> tags
load locally but fetch() does not.

Run after scripts/poll.py in the automation workflow so the site always
reflects the latest poll.
"""

import json
from pathlib import Path
import datetime as dt

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "assets" / "js" / "data.js"


def load(name, default):
    p = DATA / name
    if not p.exists():
        return default
    return json.loads(p.read_text())


def main():
    bundle = {
        "generated": dt.datetime.utcnow().isoformat() + "Z",
        "opportunities": load("opportunities.json", {"opportunities": []}),
        "opportunitiesLive": load("opportunities-live.json", {"opportunities": []}),
        "grants": load("grants.json", {"grants": []}),
        "events": load("events.json", {"events": []}),
        "directory": load("directory.json", {"procurement": [], "grants": []}),
        "portalGuides": load("portal-guides.json", {"portals": {}}),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.GROWTH_DATA = " + json.dumps(bundle, ensure_ascii=False) + ";\n")
    o = len(bundle["opportunities"].get("opportunities", []))
    lv = len(bundle["opportunitiesLive"].get("opportunities", []))
    g = len(bundle["grants"].get("grants", []))
    ev = len(bundle["events"].get("events", []))
    pg = len((bundle.get("portalGuides", {}) or {}).get("portals", {}))
    print(f"data.js written: {o} standing + {lv} live opportunities, {g} grants, {ev} events, {pg} portal guides")


if __name__ == "__main__":
    main()
