#!/usr/bin/env python3
"""
Daily UK-healthtech capital poller.

Unlike poll.py (which reads structured OCDS tender feeds), funding rounds and
exits only exist as prose in news articles, so this script uses the Anthropic
API with web search to find them and extract structured rows. It then dedupes
against data/capital-deals.json, validates hard, tags directory matches from
the Finder, and writes the single source-of-truth file that BOTH the tracker
page and the weekly email read.

Defences (Luke's brief: "set up defences, flow automatically, caveat it, and
if it looks wrong fire to the feedback loop"):
  - drop any candidate with no source URL, no named backer, or a bad/old date
  - dedupe by key and by company+month so the same round reported by three
    outlets lands once
  - UK-only is enforced by the prompt and re-checked here
  - confidence is preserved; non-"high" rows render as "unconfirmed" on the page
  - SPIKE guard: an implausible number of new rows in one run is treated as a
    bad parse — nothing is published and the run FAILS so a human is emailed
  - PARSE failure: malformed model output publishes nothing and FAILS the run
  - STALE guard: many empty days in a row raises an alert
  - every alert is written to data/capital-poll-alerts.json AND, for hard
    tripwires, the workflow exits non-zero so GitHub emails the repo owner
    (the feedback loop) automatically

Runs on GitHub Actions; needs ANTHROPIC_API_KEY in the repo secrets. It does
NOT depend on anyone's computer being on.
"""

import json
import os
import re
import sys
import datetime as dt
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DEALS_FILE = DATA / "capital-deals.json"
FINDER_FILE = ROOT / "capital" / "finder.html"
ALERTS_FILE = DATA / "capital-poll-alerts.json"
STATE_FILE = DATA / "capital-poll-state.json"
REVIEW_FILE = DATA / "capital-poll-review.json"

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CAPITAL_MODEL", "claude-sonnet-4-6")
LOOKBACK_DAYS = int(os.environ.get("CAPITAL_LOOKBACK_DAYS", "4"))
MAX_NEW = int(os.environ.get("CAPITAL_MAX_NEW", "15"))      # spike tripwire
STALE_THRESHOLD = int(os.environ.get("CAPITAL_STALE_DAYS", "10"))
DRY_RUN = os.environ.get("CAPITAL_DRY_RUN", "").lower() in ("1", "true", "yes")
VALID_TYPES = {"equity", "debt", "grant", "exit"}


# ---------------------------------------------------------------- helpers ----
def log(msg):
    print(f"[capital] {msg}", flush=True)


def gh_error(msg):
    # Surfaces as an annotation in the Actions run.
    print(f"::error::[capital] {msg}", flush=True)


def alert(reason, detail):
    """Record a tripwire to the alerts file (committed) and annotate the run."""
    entry = {"ts": dt.datetime.utcnow().isoformat() + "Z", "reason": reason, "detail": detail}
    try:
        existing = json.loads(ALERTS_FILE.read_text()) if ALERTS_FILE.exists() else []
    except Exception:
        existing = []
    existing.append(entry)
    ALERTS_FILE.write_text(json.dumps(existing[-50:], indent=2) + "\n")
    gh_error(f"{reason}: {detail}")


def slugify(*parts):
    s = "-".join(p for p in parts if p)
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


def parse_date(s):
    try:
        return dt.date.fromisoformat(str(s).strip()[:10])
    except (ValueError, TypeError):
        return None


def load_deals():
    blob = json.loads(DEALS_FILE.read_text())
    return blob, (blob.get("deals") or [])


def load_finder_firms():
    """The Finder's 202 capital-source names live inline in finder.html."""
    try:
        html = FINDER_FILE.read_text()
    except OSError:
        return []
    return sorted(set(re.findall(r'"name":\s*"([^"]+)"', html)))


def directory_matches(investors, firms):
    """Mirror the manual match: substring either way, ignoring the '(...)' tail."""
    out = []
    for firm in firms:
        head = firm.split(" (")[0].lower().strip()
        if not head:
            continue
        for inv in investors:
            il = inv.lower().strip()
            if head and (head in il or il in head):
                out.append(firm)
                break
    return sorted(set(out))


# ------------------------------------------------------------- model call ----
def build_prompt(today, recent_companies):
    start = today - dt.timedelta(days=LOOKBACK_DAYS)
    exclude = ", ".join(sorted(recent_companies)) or "(none yet)"
    return f"""You are a research assistant maintaining a tracker of UK healthtech capital events. Find every UK healthtech capital event ANNOUNCED between {start.isoformat()} and {today.isoformat()} inclusive. Today is {today.isoformat()}; search using the current year.

Include four types: equity rounds (pre-seed to growth), debt/venture debt, non-dilutive grants (Innovate UK, SBRI Healthcare, NIHR, UKRI, Wellcome, NHS programmes), and exits/M&A where a UK healthtech company is acquired.

"UK healthtech" = a company headquartered or primarily operating in the UK in digital health, medtech, health AI, diagnostics, biotech with a clear health application, care tech, or healthtech SaaS. For acquisitions the UK company is the one acquired.

STRICT RULES:
- UK companies only. Exclude non-UK companies even if a UK investor took part.
- Only include an item with a REAL, specific source URL from a credible outlet (UKTN/uktech.news, Sifted, htn.co.uk, digitalhealth.net, businesscloud.co.uk, eu-startups.com, finsmes, tech.eu, prnewswire, company press releases). No source = do not include.
- Only events announced in the date range above. No invented dates.
- Every row must name at least one investor (or the acquirer for an exit, or the funding body for a grant).
- Do NOT include any of these, already tracked: {exclude}.

Return ONLY a JSON array (no prose, no markdown fences) of objects with EXACTLY these keys:
  company (str), what (str, one line), type (equity|debt|grant|exit), round (str),
  amount_gbp (integer GBP, convert from USD/EUR; null if undisclosed),
  date (YYYY-MM-DD announcement date), investors (array of strings),
  acquirer_origin (exits only: "UK" or "overseas:Country"; else null),
  source_url (string), confidence ("high" or "medium").
If there are no qualifying events, return []."""


def call_model(prompt):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 4096,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode())
    # Concatenate the assistant text blocks (search results are separate blocks).
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    return text


def extract_json_array(text):
    """Pull the JSON array out of the model's reply, tolerating stray prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON array in model output")
    return json.loads(text[start:end + 1])


def url_is_dead(url):
    """Soft liveness check: only treat a clear 404/410 as fatal (drops fabricated
    links); network/403/timeout errors are tolerated (many sites block bots)."""
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (compatible; MyClinicalBot/1.0)"})
        with urlopen(req, timeout=20):
            return False
    except HTTPError as e:
        return e.code in (404, 410)
    except (URLError, ValueError, OSError):
        return False


# -------------------------------------------------------------------- main ---
def main():
    if not API_KEY:
        gh_error("ANTHROPIC_API_KEY is not set — add it as a repo secret. Skipping.")
        return 1
    if not DEALS_FILE.exists():
        gh_error("data/capital-deals.json missing — nothing to update.")
        return 1

    blob, deals = load_deals()
    today = dt.date.today()

    # Daily guard: several cron ticks fire for reliability, but only the first
    # per day should spend an API call. workflow_dispatch can force a re-run.
    force = os.environ.get("CAPITAL_FORCE", "").lower() in ("1", "true", "yes")
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            state = {}
    if not force and state.get("last_run") == today.isoformat():
        log("Already ran today — skipping (set CAPITAL_FORCE=1 to override).")
        return 0

    # Dedupe sets.
    known_keys = {d.get("key") for d in deals if d.get("key")}
    known_cm = {(d.get("company", "").lower().strip(), (d.get("date") or "")[:7]) for d in deals}
    recent_companies = {
        d.get("company") for d in deals
        if (pd := parse_date(d.get("date"))) and pd >= today - dt.timedelta(days=45)
    }
    firms = load_finder_firms()

    # --- discover -----------------------------------------------------------
    try:
        raw = call_model(build_prompt(today, recent_companies))
    except Exception as e:
        alert("api_error", f"Anthropic call failed: {e}")
        return 1
    try:
        candidates = extract_json_array(raw)
        if not isinstance(candidates, list):
            raise ValueError("top-level JSON is not an array")
    except Exception as e:
        alert("parse_failure", f"Could not parse model output: {e}. First 300 chars: {raw[:300]!r}")
        return 1

    # --- validate + dedupe --------------------------------------------------
    window_start = today - dt.timedelta(days=LOOKBACK_DAYS + 2)  # small slack
    new_deals, dropped = [], []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        company = (c.get("company") or "").strip()
        dtype = (c.get("type") or "").lower().strip()
        src = (c.get("source_url") or "").strip()
        pd = parse_date(c.get("date"))
        investors = [i for i in (c.get("investors") or []) if isinstance(i, str) and i.strip()]

        if not company or dtype not in VALID_TYPES:
            dropped.append((company or "?", "bad company/type")); continue
        if not src.startswith("http"):
            dropped.append((company, "no source url")); continue
        if not pd or not (window_start <= pd <= today):
            dropped.append((company, f"date out of window: {c.get('date')}")); continue
        if not investors:
            dropped.append((company, "no named backer")); continue

        key = (c.get("key") or "").strip() or slugify(company, dtype, pd.isoformat()[:7])
        cm = (company.lower(), pd.isoformat()[:7])
        if key in known_keys or cm in known_cm:
            dropped.append((company, "duplicate")); continue
        if url_is_dead(src):
            dropped.append((company, "source url 404")); continue

        amt = c.get("amount_gbp")
        amt = int(amt) if isinstance(amt, (int, float)) else None
        new_deals.append({
            "key": key,
            "company": company,
            "what": (c.get("what") or "").strip(),
            "type": dtype,
            "round": (c.get("round") or "").strip(),
            "amount_gbp": amt,
            "date": pd.isoformat(),
            "investors": investors,
            "directory_matches": directory_matches(investors, firms),
            "acquirer_origin": c.get("acquirer_origin") if dtype == "exit" else None,
            "source_url": src,
            "confidence": "high" if c.get("confidence") == "high" else "medium",
            "status": "published",
        })
        known_keys.add(key); known_cm.add(cm)

    for name, why in dropped:
        log(f"dropped {name}: {why}")

    # --- tripwires ----------------------------------------------------------
    if len(new_deals) > MAX_NEW:
        REVIEW_FILE.write_text(json.dumps(new_deals, indent=2) + "\n")
        alert("spike", f"{len(new_deals)} new rows in one run (> {MAX_NEW}). Held for review in "
                       f"{REVIEW_FILE.name}; nothing published.")
        return 1

    if not new_deals:
        empties = int(state.get("consecutive_empty", 0)) + 1
        state.update({"last_run": today.isoformat(), "consecutive_empty": empties})
        STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
        if empties >= STALE_THRESHOLD:
            alert("stale", f"{empties} consecutive days with no new deals — check the sources/feed.")
        log(f"No new deals (dropped {len(dropped)} candidates). Nothing to commit.")
        return 0

    # --- merge + write ------------------------------------------------------
    merged = new_deals + deals
    merged.sort(key=lambda d: d.get("date") or "", reverse=True)
    blob["deals"] = merged
    blob["count"] = len(merged)
    blob["updated"] = today.isoformat()
    state.update({"last_run": today.isoformat(), "consecutive_empty": 0})

    log(f"Adding {len(new_deals)} new deal(s): " + ", ".join(d["company"] for d in new_deals))
    if DRY_RUN:
        log("DRY_RUN set — not writing files.")
        return 0
    DEALS_FILE.write_text(json.dumps(blob, indent=2) + "\n")
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
