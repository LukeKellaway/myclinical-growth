#!/usr/bin/env python3
"""
fetch_observatory.py
====================
Downloads the raw datasets the UK National Data Observatory needs into a working
directory, ready for process_v2.py. Designed to run in GitHub Actions (GitHub's
runners can reach england.nhs.uk / nomisweb / fingertips, which most sandboxes
cannot).

It is driven by scripts/download_manifest.json — the record of the original
26 March pull, which lists every source URL and its target file. For most
sources the manifest URL is stable and re-fetching it returns the latest data:
  - ONS via the Nomis API (every URL has date=latest)
  - Fingertips via its API (returns the full series)
  - DEFRA / annual gov files (change only once a year)

The exception is NHS England's monthly performance files (RTT, DM01, A&E,
Cancer): their URLs have the month baked in, so we resolve the current link from
the publication page and fall back to the manifest URL if resolution fails.

Output dir: $OBS_DATA_DIR (default: a temp dir). Mirrors the UK_National_Data
layout process_v2.py expects, e.g. Performance_Waiting_Times/RTT_latest.zip.

Exit code is always 0: a partial fetch still lets process_v2.py build whatever
it can, and the workflow's own guard decides whether the result is worth
publishing.
"""

import os
import re
import sys
import json
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "download_manifest.json"
OUT_DIR = Path(os.environ.get("OBS_DATA_DIR") or tempfile.mkdtemp(prefix="obsdata_"))

UA = "Mozilla/5.0 (compatible; MyClinicalObservatoryBot/1.0; +https://growth.myclinical.co.uk)"

# The exact files process_v2.py reads (relative to the data root), besides the
# whole Fingertips_Extracts folder which is pulled wholesale.
NEEDED = {
    "Performance_Waiting_Times/RTT_latest.zip",
    "Performance_Waiting_Times/DM01_latest.zip",
    "Performance_Waiting_Times/AE_latest.csv",
    "Performance_Waiting_Times/Cancer_Waits_latest.csv",
    "Workforce/Sickness_Absence_latest.csv",
    "Demographics/ONS_Population_MYE_LA.csv",
    "Demographics/ONS_Births_LA.csv",
    "Demographics/ONS_Deaths_LA.csv",
    "Demographics/ONS_Population_Ethnicity_LA.csv",
    "Economy_Employment/ONS_Employment_APS_LA.csv",
    "Economy_Employment/ONS_Claimant_Count_LA.csv",
    "Economy_Employment/ONS_Median_Earnings_ASHE_LA.csv",
    "Economy_Employment/ONS_GVA_Per_Head_LA.csv",
    "Economy_Employment/ONS_Business_Counts_LA.csv",
    "Economy_Employment/DWP_Benefits_LA.csv",
    "Environment/DEFRA_Air_Quality_PM25.csv",
    "Environment/DEFRA_Air_Quality_NO2.csv",
}

results = {"ok": [], "fail": []}


def rel_path(file_field: str) -> str:
    """Manifest 'file' is an absolute Mac path; keep the part after the data root."""
    marker = "UK_National_Data/"
    return file_field.split(marker, 1)[-1] if marker in file_field else Path(file_field).name


def http_get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download(url: str, dest_rel: str) -> bool:
    dest = OUT_DIR / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = http_get(url)
        if not data or len(data) < 64:
            raise ValueError(f"suspiciously small ({len(data)} bytes)")
        dest.write_bytes(data)
        print(f"  OK   {dest_rel}  ({len(data)//1024} KB)")
        results["ok"].append(dest_rel)
        return True
    except Exception as e:  # noqa: BLE001 — best-effort, keep going
        print(f"  FAIL {dest_rel}  <- {url[:90]}  ({e})")
        results["fail"].append(dest_rel)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# NHS England monthly performance — resolve the current file link from the page.
# These resolvers are the fragile part (page structure can change). Each falls
# back to the manifest's last-known URL, so a resolver miss = last-known data,
# never a crash. Keep them isolated so a fix is a one-function change.
# ─────────────────────────────────────────────────────────────────────────────
def find_links(page_url: str, pattern: str):
    """Return absolute hrefs on page_url whose href matches pattern (ci)."""
    try:
        html = http_get(page_url, timeout=90).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(f"  (page fetch failed {page_url[:70]}: {e})")
        return []
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)
    rx = re.compile(pattern, re.I)
    out = []
    for h in hrefs:
        if rx.search(h):
            if h.startswith("//"):
                h = "https:" + h
            elif h.startswith("/"):
                h = "https://www.england.nhs.uk" + h
            out.append(h)
    return out


def resolve_nhs_england(key: str):
    """Return a best-guess current URL for an NHS England performance dataset."""
    WORK = "https://www.england.nhs.uk/statistics/statistical-work-areas/"
    if key == "RTT":
        # work-area page -> latest year sub-page -> "Full CSV data file" zip
        years = find_links(WORK + "rtt-waiting-times/", r"rtt-data-20\d\d-\d\d")
        for yr in sorted(set(years), reverse=True):
            zips = find_links(yr, r"Full[-_ ]?CSV[-_ ]?data[-_ ]?file.*\.zip")
            if zips:
                return zips[0]
        return None
    if key == "DM01":
        zips = find_links(WORK + "diagnostics-waiting-times-and-activity/",
                          r"(full[-_ ]?extract|monthly[-_ ]?diagnostics).*\.zip")
        return zips[0] if zips else None
    if key == "AE":
        csvs = find_links(WORK + "ae-waiting-times-and-activity/",
                          r"20\d\d-CSV.*\.csv")
        return csvs[0] if csvs else None
    if key == "Cancer_Waits":
        csvs = find_links(WORK + "cancer-waiting-times/",
                          r"(monthly|combined).*\.csv")
        return csvs[0] if csvs else None
    return None


def main():
    print(f"fetch_observatory -> {OUT_DIR}")
    manifest = json.load(open(MANIFEST))["datasets"]

    # 1) Manifest-driven re-fetch for the stable sources we need.
    nhs_keys = {"RTT", "DM01", "AE", "Cancer_Waits"}
    for cat, entries in manifest.items():
        if not isinstance(entries, dict):
            continue
        is_fingertips = cat == "Fingertips_Extracts"
        for key, info in entries.items():
            if not isinstance(info, dict) or "url" not in info or "file" not in info:
                continue
            rel = rel_path(info["file"])
            if not is_fingertips and rel not in NEEDED:
                continue  # skip datasets the observatory doesn't read

            url = info["url"]
            # NHS England performance: try to resolve the current month's link.
            if cat == "Performance_Waiting_Times" and key in nhs_keys:
                fresh = resolve_nhs_england(key)
                if fresh:
                    print(f"  resolved latest {key}: {fresh[:90]}")
                    url = fresh
                else:
                    print(f"  using last-known URL for {key} (resolver found nothing)")
            download(url, rel)

    print("\n=== fetch summary ===")
    print(f"  downloaded: {len(results['ok'])}")
    print(f"  failed:     {len(results['fail'])}")
    if results["fail"]:
        print("  failures:", ", ".join(results["fail"][:15]))
    print(f"OBS_DATA_DIR={OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
