#!/usr/bin/env python3
# last manual trigger: 2026-05-20T11:28Z (retry send after audience subscriber added)
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

# Buyers split into two tiers based on how digital their procurement is.
#
# STRONG_CENTRAL_BUYER: dedicated digital / technology / informatics shops.
# These buyers genuinely don't publish non-digital procurement at meaningful
# volume, so a notice from one of them that survives the hard-exclude is
# auto-passed even without a CPV or keyword hit. Catches the policy-language
# "stealth releases" that don't use procurement terminology.
#
# WEAK_CENTRAL_BUYER: large national / regional commissioners that BUY a lot
# of digital but also commission ordinary clinical services (e.g. NHS England
# commissions both NHSX-style platforms AND clinical pathway services like the
# MMPSA medication service). Notices from these buyers must STILL show a CPV
# or keyword signal to be kept, so a clinical-services contract from
# NHS England no longer auto-passes the filter.
STRONG_CENTRAL_BUYER = re.compile(
    r"NHS Digital\b|\bNHSX\b|NHS Transformation Directorate|"
    r"NHS Business Services Authority|\bNHSBSA\b|"
    r"Genomics England|"
    r"NHS Shared Business Services|\bNHS\s*SBS\b|"
    r"NHS North of England Commercial Procurement Collaborative|\bNOE CPC\b|"
    r"London Procurement Partnership|\bNHS LPP\b",
    re.I,
)
WEAK_CENTRAL_BUYER = re.compile(
    r"\bNHS England\b|"
    r"Department of Health and Social Care|\bDHSC\b|"
    r"Health Education England|"
    r"NHS Arden|NHS Midlands and Lancashire|NHS South[, ]?Central[, ]?and West|"
    r"East of England NHS Collaborative",
    re.I,
)
# Back-compat alias used elsewhere in the file. Matches any central buyer of
# either tier; the is_relevant logic now decides what to do per-tier.
CENTRAL_DIGITAL_BUYER = re.compile(
    STRONG_CENTRAL_BUYER.pattern + "|" + WEAK_CENTRAL_BUYER.pattern,
    re.I,
)

# CPV prefixes split into two tiers.
# STRONG = unambiguously digital (software, IT services, telecoms, data).
#   A health buyer + any of these = relevant on its own.
# WEAK = healthcare-adjacent but covers a lot of clinical-services-only
#   contracts that have no digital component (eye testing, frailty consults,
#   transport, catering, etc.). Only relevant when paired with a digital keyword.
STRONG_DIGITAL_CPV = (
    "48",        # Software packages and information systems
    "72",        # IT services: consulting, software development, internet
    "32",        # Radio, TV, communications, telecoms equipment
    "30200",     # Computer equipment and supplies
    "33196",     # Medical aids (incl. some digital devices)
    "73",        # Research and development services (including digital R&D)
)
WEAK_HEALTHCARE_CPV = (
    "85",        # Health and social work services (covers most NHS clinical contracts)
    "331",       # Medical equipment, pharmaceuticals & personal care products (broad)
    "33100000",  # Medical equipment (broad parent)
    "799",       # Business services (admin, finance, marketing)
    "302",       # Office machinery (rarely digital)
    "323",       # Electrical apparatus (mostly hardware)
)

KEYWORDS = re.compile(
    # \bdigital matches "digital" AND "digitally" / "digitalisation"; same trick
    # on \bvirtual\b ... \bcare\b so "virtual care", "virtual ward" both hit.
    r"\bdigital|software|\bAI\b|artificial intelligence|machine learning|"
    r"\bEPR\b|electronic patient record|electronic health record|telehealth|"
    r"telemedicine|remote monitoring|virtual ward|virtual care|patient app|patient portal|"
    r"analytics|interoperab|\bSaaS\b|\bcloud\b|e-health|ehealth|"
    r"health tech|healthtech|clinical system|data platform|informatics|"
    r"digital health|wearable|\bAI as a Medical Device\b|\bAIaMD\b|"
    r"decision support|computer vision|natural language processing|"
    r"federated learning|cyber\s*security|connected device|"
    r"electronic prescrib|e-prescrib|electronic referral|e-referral|"
    r"image (?:analysis|processing|recognition)|"
    # NHS clinical-system acronyms (each is a real NHS digital product category)
    r"\bPAS\b|\bRIS\b|\bPACS\b|\bLIMS\b|\bOMS\b|\bTPS\b|"
    r"\bEDMS?\b|\bECR\b|\bMIS\b|\bCDS\b|\bCDSS\b|\bCRIS\b|"
    r"\bSCR\b|\bGPSoC?\b|\bSPINE\b|\bHSCN\b|\bN3\b|"
    r"maternity system|theatre management|bed management|e-?observation|"
    r"e-?roster|workforce system|patient flow|case management system|"
    r"referral management|outcome management system|order communications|"
    r"results reporting|clinical correspondence|secure messaging|"
    r"electronic forms?|\bIT\s+(?:system|infrastructure|services)|"
    r"\bICT\b|technology refresh|system replacement|system upgrade|"
    # 2025-26 NHS digital vocabulary that the original list missed
    r"ambient voice|ambient scribe|\bscribe\b|\bcopilot\b|"
    r"\bdictation\b|transcrib|voice recognition|speech recognition|"
    r"\bchatbot\b|conversational (?:AI|agent)|large language model|\bLLM\b|"
    r"generative AI|\bGenAI\b|foundation model|"
    r"\bRPA\b|robotic process automation|workflow automation|"
    r"\bGP Connect\b|\bBaRS\b|\be-?RS\b|Wayfinder|"
    r"\bFHIR\b|\bHL7\b|\bSNOMED\b|terminology server|"
    r"integration engine|interface engine|\bESB\b|\bAPI\b|"
    r"\bSSO\b|single sign[- ]?on|identity (?:management|provider)|smart\s*card|"
    r"dashboard|visualisation|visualization|business intelligence|\bBI\b|"
    r"data warehouse|data lake|data lakehouse|data mesh|trusted research environment|\bTRE\b|"
    r"\bDSPT\b|data security (?:and )?protection|ISO\s*27001|"
    r"\bSaMD\b|software as a medical device|"
    r"online consultation|video consultation|virtual triage|virtual consult|"
    r"web portal|mobile app|patient-facing app|tech-enabled|"
    r"shared care record|\bShCR\b|integrated care record|\bICR\b|"
    r"population health management|\bPHM\b|risk stratification|"
    r"managed (?:IT|service|hosting)|hosting platform|"
    r"network infrastructure|connectivity|telephony|unified comms",
    re.I,
)

# Buyer-name red flags. These are health buyers but the contract is clearly
# non-digital and should be dropped even if a generic CPV matches.
EXCLUDE_TITLE = re.compile(
    r"\bcatering\b|\bcleaning\b|\blaundry\b|\bwaste\b|\btransport\b|"
    r"\bcab(?:bing)?\b|\btaxi\b|\bambulance\b(?! booking| dispatch)|"
    r"\bsecurity guard|\bgardening\b|\bgrounds maintenance\b|"
    r"\buniform(?:s)?\b|\bstationery\b|\bfurniture\b|"
    r"\bconsultant services?\b(?! for (?:digital|software|data|AI))|"
    r"\beye (?:test|screen)|\bdental (?:services|care)\b|"
    r"\bphysiotherap|\bcounselling\b|\bdomicil|"
    # More clinical-services-only contracts that aren't digital health:
    r"\bwheelchair|\borthodontic|\borthotic|\bprosthetic|\bhearing aid|"
    r"\bspectacle|\bspeech (?:and language|therapy)|"
    r"\btalking therap|\bIAPT\b|\bpsychological therap|"
    r"\bnon-custodial|\bcustodial services|"
    # NOTE: removed bare "general practice", "pharmacy", "medication", "maternity services"
    # excludes — they were dropping digital medicines, pharmacy stock systems, GP IT
    # call-offs, maternity information systems. Now guarded so only the
    # clearly-clinical variants are excluded.
    r"\bAPMS\b|\bprimary medical service|"
    r"\bpharmac(?:y|euticals?)\b(?! (?:system|software|automation|stock|management|app|platform|module|portal|informatic|robot))|"
    r"\bmedication (?:review|reconciliation service|administration service)\b|"
    r"\bgeneral practice (?:provision|services|vacanc)|"
    r"\bradiotherap|\blinac|\blinear accelerator|"
    r"\boncology services|\brenal services|"
    r"\bmaternity services\b(?! system| software| platform)|\bsexual health (?:service|clinic)|"
    r"\boral surgery\b|\boral and maxillofacial\b|"
    r"\bgeneral medical services?\b|\bpractice vacanc|\bvacant practice|"
    r"\bGP locum|\blocum (?:gp|consultant|cover)|"
    r"\bcare home\b|\bsupported living\b|\bsubstance misuse",
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


# Per-run funnel stats. Reset by main(). Lets us see what the filter
# is doing on every run, instead of just "kept N".
STATS = {
    "fetched_fts": 0,
    "fetched_cf": 0,
    "nhs_buyer_match": 0,
    "hard_excluded": 0,
    "central_buyer_pass": 0,
    "strong_cpv_pass": 0,
    "keyword_pass": 0,
    "rejected_no_signal": 0,
    "kept": 0,
}
# A small bucket of borderline items (NHS buyer + no digital signal) so we
# can see what's being filtered out. Capped to keep logs readable.
BORDERLINE_SAMPLES = []
BORDERLINE_CAP = 25


def is_relevant(buyer_name, title, description, cpvs):
    blob = f"{title} {description}"
    is_strong_central = bool(STRONG_CENTRAL_BUYER.search(buyer_name or ""))
    is_weak_central = bool(WEAK_CENTRAL_BUYER.search(buyer_name or ""))
    is_central = is_strong_central or is_weak_central
    # Allow central authorities through even if they don't match the broader
    # HEALTH_BUYER pattern (e.g. "Genomics England" doesn't contain "NHS").
    if not is_central and not HEALTH_BUYER.search(buyer_name or ""):
        return False
    STATS["nhs_buyer_match"] += 1
    # Hard exclude: clearly non-digital NHS services.
    if EXCLUDE_TITLE.search(blob):
        STATS["hard_excluded"] += 1
        return False
    cpvs_str = [str(c) for c in cpvs]
    strong_cpv = any(c.startswith(STRONG_DIGITAL_CPV) for c in cpvs_str)
    kw_hit = bool(KEYWORDS.search(blob))

    # Dedicated digital buyers (NHS Digital, NHSX, NHS Transformation
    # Directorate, NHSBSA, NOE CPC, Genomics England, NHS SBS, LPP) basically
    # never publish non-digital procurement. Auto-pass — this catches the
    # policy-language stealth drops that don't use procurement terminology.
    if is_strong_central:
        STATS["central_buyer_pass"] += 1
        STATS["kept"] += 1
        return True

    # Weak central buyers (NHS England, DHSC, HEE, regional CSUs) DO procure
    # plenty of non-digital things — clinical pathway services, medication
    # programmes, workforce contracts. They must still show a digital signal,
    # same gate as a regular trust. Closes the loophole that previously let
    # the MMPSA medication-service notice through just because the buyer was
    # NHS England.
    if strong_cpv:
        STATS["strong_cpv_pass"] += 1
        STATS["kept"] += 1
        return True
    if kw_hit:
        STATS["keyword_pass"] += 1
        STATS["kept"] += 1
        return True
    STATS["rejected_no_signal"] += 1
    if len(BORDERLINE_SAMPLES) < BORDERLINE_CAP:
        BORDERLINE_SAMPLES.append({
            "title": (title or "")[:120],
            "buyer": (buyer_name or "")[:80],
            "cpvs": cpvs_str[:5],
        })
    return False


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


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_RE = re.compile(r"&(?:nbsp|amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);")
_HTML_ENTITY_MAP = {
    "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&apos;": "'",
}


def _strip_html(text):
    """OCDS descriptions from Find a Tender often contain literal HTML
    (most commonly `<br/>`, `<p>`, `<strong>`, numeric entities). We render
    these as plain text on the site, so leaving the tags in means subscribers
    see `<br/><br/>` in their inbox and on opportunity detail pages. Strip
    tags, decode common entities, and collapse whitespace."""
    if not text:
        return ""
    out = _HTML_TAG_RE.sub(" ", str(text))
    # Decode known entities cheaply; anything else stays as-is.
    out = _HTML_ENTITY_RE.sub(lambda m: _HTML_ENTITY_MAP.get(m.group(0), " "), out)
    # Collapse repeated whitespace (br/br left double spaces, etc.).
    out = re.sub(r"\s+", " ", out).strip()
    return out


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

    title = _strip_html(tender.get("title") or release.get("title") or "")
    description = _strip_html(tender.get("description") or "")
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
        releases = data.get("releases", [])
        STATS["fetched_fts"] += len(releases)
        for release in releases:
            opp = parse_release(release, "Find a Tender", "fts")
            if opp:
                out.append(opp)
        nxt = (data.get("links") or {}).get("next")
        if not nxt or nxt == url:
            break
        url = nxt
        time.sleep(1)
    print(f"[FTS] fetched {STATS['fetched_fts']} notices, kept {len(out)}")
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
        STATS["fetched_cf"] += len(results)
        for entry in results:
            release = entry.get("releasePackage", {}).get("releases", [entry])[0] \
                if isinstance(entry, dict) and "releasePackage" in entry else entry
            opp = parse_release(release, "Contracts Finder", "cf")
            if opp:
                out.append(opp)
        page += 1
        time.sleep(1)
    print(f"[CF] fetched {STATS['fetched_cf']} notices, kept {len(out)}")
    return out


def main():
    since = dt.datetime.utcnow() - dt.timedelta(hours=LOOKBACK_HOURS)
    print(f"Polling for notices updated since {iso(since)} (lookback {LOOKBACK_HOURS}h)")

    items = []
    items += poll_find_a_tender(since)
    items += poll_contracts_finder(since)

    # de-duplicate by id, keep most recently published
    by_id = {}
    for it in items:
        by_id[it["id"]] = it
    merged = sorted(by_id.values(), key=lambda x: x.get("published", ""), reverse=True)

    # Funnel summary so we can SEE the pipeline is alive even on quiet days.
    total_fetched = STATS["fetched_fts"] + STATS["fetched_cf"]
    print("--- Funnel ---")
    print(f"  Total notices fetched (FTS + CF): {total_fetched}")
    print(f"    of which NHS / health-buyer match: {STATS['nhs_buyer_match']}")
    print(f"      hard-excluded (catering, transport, etc.): {STATS['hard_excluded']}")
    print(f"      passed via central digital buyer: {STATS['central_buyer_pass']}")
    print(f"      passed via strong digital CPV: {STATS['strong_cpv_pass']}")
    print(f"      passed via digital keyword: {STATS['keyword_pass']}")
    print(f"      rejected (NHS but no digital signal): {STATS['rejected_no_signal']}")
    print(f"  Kept (after dedup): {len(merged)}")
    if BORDERLINE_SAMPLES:
        print("--- Borderline (NHS buyer, rejected) sample ---")
        for s in BORDERLINE_SAMPLES[:10]:
            print(f"  - {s['title']}  |  {s['buyer']}  |  CPVs: {','.join(s['cpvs'])}")

    DATA.mkdir(exist_ok=True)
    payload = {
        "updated": dt.datetime.utcnow().isoformat() + "Z",
        "count": len(merged),
        "opportunities": merged,
    }
    LIVE_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    # Persist the funnel stats too so the site (or a future dashboard) can
    # surface "X NHS tenders scanned in the last 30 days" as a trust signal.
    stats_payload = {
        "updated": dt.datetime.utcnow().isoformat() + "Z",
        "lookback_hours": LOOKBACK_HOURS,
        "funnel": STATS,
        "kept_total": len(merged),
        "borderline_samples": BORDERLINE_SAMPLES,
    }
    (DATA / "poll-stats.json").write_text(json.dumps(stats_payload, indent=2, ensure_ascii=False))
    print(f"Wrote {len(merged)} live opportunities to {LIVE_FILE}")


if __name__ == "__main__":
    main()
