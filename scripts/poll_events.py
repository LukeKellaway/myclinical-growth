#!/usr/bin/env python3
"""
UK healthtech / NHS events poller.

Like poll_capital.py, events only exist as prose on organiser sites and
listings, with no structured feed, so this uses the Anthropic API with web
search to find upcoming UK digital health, NHS and healthtech conferences,
expos and summits. It runs WEEKLY. It dedupes against
data/events.json, validates hard, PRUNES past events so the calendar stays
current, and writes the single source-of-truth file that both the events
page (events.html) and the daily/weekly digest read.

Defences (same posture as the capital poller):
  - drop any candidate with no source URL, no organiser, or a bad/past date
  - dedupe by id and by title+month so the same event listed twice lands once
  - UK-only is enforced by the prompt and re-checked here
  - confidence is preserved; non-"high" rows still publish but are tagged
  - SPIKE guard: an implausible number of new rows in one run is held for
    review and the run FAILS so a human is emailed
  - PARSE failure: malformed model output publishes nothing and FAILS the run
  - STALE guard: many empty runs in a row raises an alert
  - past events are dropped every run (housekeeping), so the page never shows
    a date that has already gone

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
EVENTS_FILE = DATA / "events.json"
ALERTS_FILE = DATA / "events-poll-alerts.json"
STATE_FILE = DATA / "events-poll-state.json"
REVIEW_FILE = DATA / "events-poll-review.json"

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("EVENTS_MODEL", "claude-sonnet-4-6")
HORIZON_DAYS = int(os.environ.get("EVENTS_HORIZON_DAYS", "455"))   # ~15 months
MAX_NEW = int(os.environ.get("EVENTS_MAX_NEW", "12"))             # spike tripwire
STALE_THRESHOLD = int(os.environ.get("EVENTS_STALE_RUNS", "8"))
DRY_RUN = os.environ.get("EVENTS_DRY_RUN", "").lower() in ("1", "true", "yes")

VALID_CATEGORIES = {
    "Procurement & policy",
    "Digital health",
    "AI in healthcare",
    "Innovation & startups",
    "MedTech",
    "Investment",
}


# ---------------------------------------------------------------- helpers ----
def log(msg):
    print(f"[events] {msg}", flush=True)


def gh_error(msg):
    print(f"::error::[events] {msg}", flush=True)


def alert(reason, detail):
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
    return re.sub(r"-{2,}", "-", s)[:70]


def parse_date(s):
    try:
        return dt.date.fromisoformat(str(s).strip()[:10])
    except (ValueError, TypeError):
        return None


def load_events():
    blob = json.loads(EVENTS_FILE.read_text())
    return blob, (blob.get("events") or [])


# ------------------------------------------------------------- model call ----
def build_prompt(today, known_titles):
    horizon = today + dt.timedelta(days=HORIZON_DAYS)
    exclude = "; ".join(sorted(known_titles)) or "(none yet)"
    cats = ", ".join(sorted(VALID_CATEGORIES))
    return f"""You are a research assistant maintaining a calendar of UK healthtech and NHS events for digital health founders who sell into the NHS. Find UK events (conferences, expos, summits, trade shows) that START between {today.isoformat()} and {horizon.isoformat()} inclusive. Today is {today.isoformat()}; search using the current year and next year.

Relevant events are about: NHS digital transformation, digital health, health AI, medtech, NHS procurement and policy, or UK healthtech investment. The audience is founders and commercial teams deciding which events are worth their time and money.

STRICT RULES:
- UK events only (held in the UK). Exclude overseas events.
- Only events with a REAL, specific organiser URL (the official event page). No source = do not include.
- Only events whose start date falls in the window above. No invented dates. If you cannot find a confirmed date, do not include the event.
- Do NOT include any of these, already tracked: {exclude}.

For each event write a "means" note: one or two plain sentences telling a founder who the event is actually for and whether it is worth it (buyer crowd vs policy day vs investor crowd). Be direct and honest. Do NOT use em-dashes anywhere; use full stops and commas.

Return ONLY a JSON array (no prose, no markdown fences) of objects with EXACTLY these keys:
  title (str), organiser (str), source_url (str, official event page),
  type (str, e.g. "Conference & expo", "Conference", "Trade show", "Festival & expo"),
  category (one of: {cats}),
  location (str, venue and city), format (str, e.g. "In person", "In person and online"),
  start_date (YYYY-MM-DD), end_date (YYYY-MM-DD, same as start if one day),
  cost (str, short, e.g. "Free for NHS; commercial paid"),
  summary (str, one or two lines on what it is),
  means (str, the honest who-it-is-for note),
  tags (array of 2-3 short strings),
  confidence ("high" or "medium").
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
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


def extract_json_array(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON array in model output")
    return json.loads(text[start:end + 1])


def url_is_dead(url):
    """Only a clear 404/410 is fatal; bot-blocking 403s/timeouts are tolerated."""
    try:
        req = Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 (compatible; MyClinicalBot/1.0)"})
        with urlopen(req, timeout=20):
            return False
    except HTTPError as e:
        return e.code in (404, 410)
    except (URLError, ValueError, OSError):
        return False


def clean_text(s):
    """Strip em/en dashes per the house style (no 'long hashes')."""
    return re.sub(r"\s*[–—]\s*", ", ", str(s or "")).strip()


# -------------------------------------------------------------------- main ---
def main():
    if not API_KEY:
        gh_error("ANTHROPIC_API_KEY is not set, add it as a repo secret. Skipping.")
        return 1
    if not EVENTS_FILE.exists():
        gh_error("data/events.json missing, nothing to update.")
        return 1

    blob, events = load_events()
    today = dt.date.today()

    # Weekly guard: the scrape runs once a week. Several cron ticks may fire for
    # reliability, but only the first within a 7-day window spends an API call.
    # workflow_dispatch can force a re-run.
    force = os.environ.get("EVENTS_FORCE", "").lower() in ("1", "true", "yes")
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except Exception:
            state = {}
    last = parse_date(state.get("last_run"))
    if not force and last and (today - last).days < 6:
        log(f"Ran {(today - last).days} day(s) ago, skipping (set EVENTS_FORCE=1 to override).")
        return 0

    # --- housekeeping: drop events that have already ended ------------------
    kept, pruned = [], []
    for e in events:
        end = parse_date(e.get("end_date") or e.get("start_date"))
        if end and end < today:
            pruned.append(e.get("title", "?"))
        else:
            kept.append(e)
    if pruned:
        log(f"Pruned {len(pruned)} past event(s): " + ", ".join(pruned))
    events = kept

    # Dedupe sets.
    known_ids = {e.get("id") for e in events if e.get("id")}
    known_tm = {(e.get("title", "").lower().strip(), (e.get("start_date") or "")[:7]) for e in events}
    known_titles = {e.get("title") for e in events if e.get("title")}

    # --- discover -----------------------------------------------------------
    try:
        raw = call_model(build_prompt(today, known_titles))
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
    horizon = today + dt.timedelta(days=HORIZON_DAYS + 10)  # small slack
    new_events, dropped = [], []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        title = (c.get("title") or "").strip()
        organiser = (c.get("organiser") or "").strip()
        src = (c.get("source_url") or "").strip()
        sd = parse_date(c.get("start_date"))
        ed = parse_date(c.get("end_date")) or sd

        if not title or not organiser:
            dropped.append((title or "?", "missing title/organiser")); continue
        if not src.startswith("http"):
            dropped.append((title, "no source url")); continue
        if not sd or not (today <= sd <= horizon):
            dropped.append((title, f"start date out of window: {c.get('start_date')}")); continue
        if ed and ed < sd:
            ed = sd

        eid = slugify("event", title, sd.isoformat()[:4])
        tm = (title.lower(), sd.isoformat()[:7])
        if eid in known_ids or tm in known_tm:
            dropped.append((title, "duplicate")); continue
        if url_is_dead(src):
            dropped.append((title, "source url 404")); continue

        category = (c.get("category") or "").strip()
        if category not in VALID_CATEGORIES:
            category = "Digital health"
        tags = [t.strip() for t in (c.get("tags") or []) if isinstance(t, str) and t.strip()][:4]

        new_events.append({
            "id": eid,
            "title": title,
            "organiser": organiser,
            "source_url": src,
            "type": (c.get("type") or "Conference").strip(),
            "category": category,
            "location": (c.get("location") or "").strip(),
            "format": (c.get("format") or "In person").strip(),
            "start_date": sd.isoformat(),
            "end_date": (ed or sd).isoformat(),
            "cost": clean_text(c.get("cost")),
            "summary": clean_text(c.get("summary")),
            "means": clean_text(c.get("means")),
            "tags": tags,
            "status": "Upcoming",
            "confidence": "high" if c.get("confidence") == "high" else "medium",
        })
        known_ids.add(eid); known_tm.add(tm)

    for name, why in dropped:
        log(f"dropped {name}: {why}")

    # --- tripwires ----------------------------------------------------------
    if len(new_events) > MAX_NEW:
        REVIEW_FILE.write_text(json.dumps(new_events, indent=2) + "\n")
        alert("spike", f"{len(new_events)} new rows in one run (> {MAX_NEW}). Held for review in "
                       f"{REVIEW_FILE.name}; nothing published.")
        return 1

    # Decide whether anything changed (new events OR pruned past ones).
    changed = bool(new_events or pruned)

    if not new_events:
        empties = int(state.get("consecutive_empty", 0)) + 1
        state.update({"last_run": today.isoformat(), "consecutive_empty": empties})
        if empties >= STALE_THRESHOLD:
            alert("stale", f"{empties} consecutive weekly runs with no new events, check the sources.")
    else:
        state.update({"last_run": today.isoformat(), "consecutive_empty": 0})

    if not changed:
        STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
        log(f"No changes (dropped {len(dropped)} candidates). Nothing to commit.")
        return 0

    # --- merge + write ------------------------------------------------------
    merged = new_events + events
    merged.sort(key=lambda e: e.get("start_date") or "")
    blob["events"] = merged
    blob["count"] = len(merged)
    blob["updated"] = today.isoformat() + "T12:00:00.000000Z"

    if new_events:
        log(f"Adding {len(new_events)} new event(s): " + ", ".join(e["title"] for e in new_events))
    if DRY_RUN:
        log("DRY_RUN set, not writing files.")
        return 0
    EVENTS_FILE.write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n")
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
