#!/usr/bin/env python3
"""
MyClinical Growth — Tier 1 procurement poller.

Fetches new tender notices from the Find a Tender and Contracts Finder
OCDS APIs, filters them to NHS / health-sector buyers with a digital-health
signal, and writes data/opportunities-live.json.

This script only ever writes opportunities-live.json. The curated standing
frameworks in data/opportunities.json are never touched here — they are
editorially maintained.

Runs on GitHub Actions (see .github/workflows/poll.yml). No API key needed:
both OCDS APIs are open.
"""

import json
import re
import sys
import time
import datetime as dt
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LIVE_FILE = DATA / "opportunities-live.json"

FTS_API = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
CF_API = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"

# How far back to look on each run (GitHub Actions runs hourly; a wider
# window gives resilience against missed runs). Override via
# POLL_LOOKBACK_HOURS env var (e.g. 720 = 30 days for a backfill).
import os as _os
LOOKBACK_HOURS = int(_os.environ.get("POLL_LOOKBACK_HOURS", "48"))
MAX_PAGES = int(_os.environ.get("POLL_MAX_PAGES", "20"))

# --- filtering -------------------------------------------------------------

HEALTH_BUYER = re.compile(
    r"\bNHS\b|National Health Service|Integrated Care Board|\bICB\b|"
    r"Foundation Trust|NHS Trust|Health Board|Department of Health|"
    r"Health and Social Care|\bHSC\b|Care Board",
    re.I,
)

# CPV prefixes worth keeping (software, IT services, medical equipment,
# health & social work services).
CPV_PREFIXES = ("48", "72", "302", "323", "331", "33100000", "851", "853", "799")

KEYWORDS = re.compile(
    r"digital|software|\bAI\b|artificial intelligence|machine learning|"
    r"\bEPR\b|electronic patient record|electronic health record|telehealth|"
    r"telemedicine|remote monitoring|virtual ward|patient app|patient portal|"
    r"\bplatform\b|analytics|interoperab|\bSaaS\b|cloud|e-health|ehealth|"
    r"health tech|healthtech|clinical system|data platform|informatics|"
    r"digital health|wearable|diagnostic imaging|decision support",
    re.I,
)

CATEGORY_RULES = [
    ("Imaging & diagnostics", r"imaging|radiology|patholog|diagnostic"),
    ("Clinical systems & EPR", r"\bEPR\b|electronic patient record|clinical system|patient record"),
    ("Remote monitoring", r"remote monitoring|virtual ward|wearable|telehealth|telemedicine|RPM"),
    ("AI & analytics", r"\bAI\b|artificial intelligence|machine learning|analytics|predictive"),
    ("Infrastructure & cloud", r"cloud|hosting|infrastructure|network|interoperab|integration"),
    ("Patient-facing", r"patient app|patient portal|app\b|self-referral|booking"),
]


def classify(text):
    for label, pattern in CATEGORY_RULES:
        if re.search(pattern, text, re.I):
            return label
    return "Other digital health"


def is_relevant(buyer_name, title, description, cpvs):
    blob = f"{title} {description}"
    buyer_hit = bool(HEALTH_BUYER.search(buyer_name or ""))
    cpv_hit = any(str(c).startswith(CPV_PREFIXES) for c in cpvs)
    kw_hit = bool(KEYWORDS.search(blob))
    # Require a health buyer, plus either a relevant CPV code or a keyword hit.
    return buyer_hit and (cpv_hit or kw_hit)


# --- fetching --------------------------------------------------------------

def fetch_json(url, timeout=30):
    req = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "MyClinicalGrowth-Poller/1.0 (+https://growth.myclinical)",
    })
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def iso(ts):
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def parse_release(release, source, source_base):
    """Turn one OCDS release into our opportunity shape, or None if not relevant."""
    tender = release.get("tender") or {}
    parties = release.get("parties") or []
    buyer = release.get("buyer") or {}
    buyer_name = buyer.get("name") or ""
    if not buyer_name and parties:
        for p in parties:
            if "buyer" in (p.get("roles") or []):
                buyer_name = p.get("name") or buyer_name

    title = tender.get("title") or release.get("title") or ""
    description = tender.get("description") or ""
    items = tender.get("items") or []
    cpvs = []
    for it in items:
        cls = it.get("classification") or {}
        if cls.get("scheme", "").upper().startswith("CPV") or cls.get("id"):
            cpvs.append(str(cls.get("id", "")))
        for ac in it.get("additionalClassifications") or []:
            cpvs.append(str(ac.get("id", "")))

    if not is_relevant(buyer_name, title, description, cpvs):
        return None

    tender_period = tender.get("tenderPeriod") or {}
    deadline = tender_period.get("endDate", "")
    ocid = release.get("ocid", "")
    release_id = release.get("id", ocid)

    return {
        "id": f"{source_base}-{release_id}",
        "title": title.strip(),
        "source": source,
        "source_url": _notice_url(release, source),
        "buyer": buyer_name.strip(),
        "type": "Framework" if re.search(r"framework", title, re.I) else "Tender",
        "category": classify(f"{title} {description}"),
        "value": _value(tender),
        "published": release.get("date", ""),
        "deadline": deadline,
        "summary": (description.strip()[:280] + "…") if len(description) > 280 else description.strip(),
        "cpv": [c for c in cpvs if c][:8],
        "tags": _tags(title, description, deadline),
        "means": "",  # editorial note — filled by the daily review task
    }


def _value(tender):
    v = tender.get("value") or {}
    amt = v.get("amount")
    if amt:
        return f"{v.get('currency','GBP')} {amt:,.0f}"
    return ""


def _notice_url(release, source):
    for doc in release.get("tender", {}).get("documents", []) or []:
        if doc.get("url"):
            return doc["url"]
    ocid = release.get("ocid", "")
    if source == "Find a Tender":
        return f"https://www.find-tender.service.gov.uk/Notice/{ocid}"
    return f"https://www.contractsfinder.service.gov.uk/Notice/{ocid}"


def _tags(title, description, deadline):
    tags = []
    blob = f"{title} {description}"
    if re.search(r"framework", title, re.I):
        tags.append("Framework")
    else:
        tags.append("Tender")
    if re.search(r"\bAI\b|artificial intelligence|machine learning", blob, re.I):
        tags.append("AI")
    if deadline:
        try:
            d = dt.datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            # OCDS feeds are inconsistent about timezones. Force UTC if naive
            # so we can subtract from a tz-aware now() without a TypeError.
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            days = (d - dt.datetime.now(dt.timezone.utc)).days
            if 0 <= days <= 21:
                tags.append("Closes soon")
        except (ValueError, TypeError):
            pass
    return tags


def poll_find_a_tender(since):
    out = []
    url = f"{FTS_API}?stages=tender&updatedFrom={iso(since)}"
    for _ in range(MAX_PAGES):
        try:
            data = fetch_json(url)
        except (URLError, HTTPError) as e:
            print(f"[FTS] fetch failed: {e}", file=sys.stderr)
            break
        for release in data.get("releases", []):
            opp = parse_release(release, "Find a Tender", "fts")
            if opp:
                out.append(opp)
        nxt = (data.get("links") or {}).get("next")
        if not nxt or nxt == url:
            break
        url = nxt
        time.sleep(1)
    print(f"[FTS] kept {len(out)} relevant notices")
    return out


def poll_contracts_finder(since):
    out = []
    page = 1
    while page <= MAX_PAGES:
        url = (f"{CF_API}?stages=tender&size=100&page={page}"
               f"&publishedFrom={iso(since)}")
        try:
            data = fetch_json(url)
        except (URLError, HTTPError) as e:
            print(f"[CF] fetch failed: {e}", file=sys.stderr)
            break
        results = data.get("results") or data.get("releases") or []
        if not results:
            break
        for entry in results:
            release = entry.get("releasePackage", {}).get("releases", [entry])[0] \
                if isinstance(entry, dict) and "releasePackage" in entry else entry
            opp = parse_release(release, "Contracts Finder", "cf")
            if opp:
                out.append(opp)
        page += 1
        time.sleep(1)
    print(f"[CF] kept {len(out)} relevant notices")
    return out


def main():
    since = dt.datetime.utcnow() - dt.timedelta(hours=LOOKBACK_HOURS)
    print(f"Polling for notices updated since {iso(since)}")

    items = []
    items += poll_find_a_tender(since)
    items += poll_contracts_finder(since)

    # de-duplicate by id, keep most recently published
    by_id = {}
    for it in items:
        by_id[it["id"]] = it
    merged = sorted(by_id.values(), key=lambda x: x.get("published", ""), reverse=True)

    DATA.mkdir(exist_ok=True)
    payload = {
        "updated": dt.datetime.utcnow().isoformat() + "Z",
        "count": len(merged),
        "opportunities": merged,
    }
    LIVE_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Wrote {len(merged)} live opportunities to {LIVE_FILE}")


if __name__ == "__main__":
    main()
