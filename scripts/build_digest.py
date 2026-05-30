#!/usr/bin/env python3
"""
MyClinical Growth — daily digest builder.

Compiles the opportunities and grants added/updated in the last 24h into an
HTML email and creates a Mailchimp campaign.

By default the campaign is created as a DRAFT — you review it in Mailchimp
and hit send. Set DIGEST_AUTOSEND=true to have it send automatically once
you trust the editorial pass.

Required environment variables (set as GitHub Actions secrets):
  MAILCHIMP_API_KEY        e.g. abc123...-us21
  MAILCHIMP_AUDIENCE_ID    your audience / list id
  MAILCHIMP_SERVER_PREFIX  e.g. us21  (the bit after the dash in the API key)
Optional:
  DIGEST_AUTOSEND          "true" to send automatically (default: draft only)
  DIGEST_FROM_NAME         default "MyClinical Growth"
  DIGEST_REPLY_TO          default "hello@myclinical..."  <-- set this
"""

import os
import sys
import json
import datetime as dt
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

API_KEY = os.environ.get("MAILCHIMP_API_KEY", "")
AUDIENCE = os.environ.get("MAILCHIMP_AUDIENCE_ID", "")
PREFIX = os.environ.get("MAILCHIMP_SERVER_PREFIX", "")
# AUTOSEND defaults to TRUE — daily brief should arrive without manual approval.
# Set the secret DIGEST_AUTOSEND=false to revert to draft-only mode.
AUTOSEND = os.environ.get("DIGEST_AUTOSEND", "true").lower() != "false"
FROM_NAME = os.environ.get("DIGEST_FROM_NAME", "MyClinical Growth")
REPLY_TO = os.environ.get("DIGEST_REPLY_TO", "hello@myclinical.example")
# UTC window in which the daily brief is allowed to fire. The workflow runs
# hourly. The FIRST run inside this window that hasn't already sent today
# will send; subsequent runs that day check the marker file and skip.
# Window is intentionally wider than a single hour because GitHub Actions
# scheduled crons can be delayed by tens of minutes under load, or even
# skipped entirely for a given hour.
# Target: land in subscribers' inboxes 4-6am UK so they can read on commute.
# 03-08 UTC window. Workflow fires every 15 min from 02-05 UTC to maximise
# the chance of catching an early send (= 4-5am BST on a good day). Window
# extends to 08 UTC as a fallback because GitHub's free-tier scheduled cron
# regularly skips the low-load early-morning ticks; the send marker still
# guarantees only one send per day.
DIGEST_WINDOW_START_UTC = int(os.environ.get("DIGEST_WINDOW_START_UTC", "3"))
DIGEST_WINDOW_END_UTC = int(os.environ.get("DIGEST_WINDOW_END_UTC", "8"))
# WEEKLY_MODE flips the script into "weekly roll-up" behaviour: 7-day lookback,
# Monday-only send gate, different subject + header copy, separate marker file
# so the daily marker isn't touched.
WEEKLY_MODE = os.environ.get("WEEKLY_MODE", "").lower() in ("1", "true", "yes")
# Weekly fires on Mondays. Window is 07-12 UTC so a delayed cron still catches.
WEEKLY_WINDOW_START_UTC = int(os.environ.get("WEEKLY_WINDOW_START_UTC", "7"))
WEEKLY_WINDOW_END_UTC = int(os.environ.get("WEEKLY_WINDOW_END_UTC", "12"))
# When true, segment sends by the per-subscriber preference merge fields.
# Daily sends to DAILY=Yes (or blank, to preserve existing subscribers from
# before the merge field existed). Weekly sends to WEEKLY=Yes.
# Off by default so we don't break sends until the DAILY/WEEKLY merge fields
# actually exist in the Mailchimp audience.
FREQUENCY_SEGMENT_ENABLED = os.environ.get("FREQUENCY_SEGMENT_ENABLED", "").lower() in ("1", "true", "yes")
# FORCE_SEND bypasses BOTH the time-of-day window AND the daily/weekly send
# marker. Used by the workflow_dispatch "force_send" input so we can fire a
# digest mid-day when an editorial change merits it. Still respects the
# Mailchimp credential check at the top of main() — no creds, no send.
FORCE_SEND = os.environ.get("FORCE_SEND", "").lower() in ("1", "true", "yes")
# Marker files checked into the repo to record the last send date. Prevents
# multiple sends in a single day/week if the cron fires several times in the
# send window. Daily and weekly use separate markers so neither clobbers the other.
SEND_MARKER = ROOT / "data" / "last-digest-send.txt"
WEEKLY_SEND_MARKER = ROOT / "data" / "last-weekly-digest-send.txt"


def load(name):
    p = DATA / name
    if not p.exists():
        return []
    blob = json.loads(p.read_text())
    if isinstance(blob, dict):
        return blob.get("opportunities") or blob.get("grants") or blob.get("items") or []
    return blob


def recent(items, hours=24):
    """Return items published within the last `hours` UTC.

    Timestamps from OCDS feeds and editorial files are a mix of UTC ("Z"),
    BST (+01:00), and naive. The previous implementation just stripped the
    timezone before parsing, which meant a notice published at 22:00 BST
    yesterday (= 21:00 UTC) was treated as 22:00 UTC and could fall outside
    the 24h window once the digest ran the next morning. Result: real new
    items got filtered out and the digest fell back to "Quiet day" by
    mistake. Now we parse with timezone awareness and compare in UTC.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    out = []
    for it in items:
        stamp = it.get("published") or it.get("updated") or ""
        if not stamp:
            continue
        try:
            d = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        # Treat naive timestamps as UTC (matches how the poller writes them).
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        if d >= cutoff:
            out.append(it)
    return out


SITE_URL = "https://growth.myclinical.co.uk"


def _parse_date(s):
    """Forgiving date parser. Returns a date or None."""
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s).strip()[:10])
    except (ValueError, TypeError):
        return None


def _deadline_chip(deadline_str):
    """Coloured pill showing deadline status. Red for urgent, amber for soon, neutral otherwise."""
    d = _parse_date(deadline_str)
    if not d:
        if deadline_str:
            return f'<span style="display:inline-block;background:#f0eee6;color:#3a403a;font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;text-transform:uppercase;letter-spacing:.05em;">{deadline_str}</span>'
        return ""
    days = (d - dt.date.today()).days
    label_date = d.strftime("%-d %b") if hasattr(d, "strftime") else str(d)
    if days < 0:
        return f'<span style="display:inline-block;background:#e8e6dd;color:#5f655f;font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;text-transform:uppercase;letter-spacing:.05em;">Closed {label_date}</span>'
    if days == 0:
        return '<span style="display:inline-block;background:#a3492f;color:#fff;font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;text-transform:uppercase;letter-spacing:.05em;">Closes today</span>'
    if days <= 7:
        return f'<span style="display:inline-block;background:#a3492f;color:#fff;font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;text-transform:uppercase;letter-spacing:.05em;">Closes in {days} day{"s" if days != 1 else ""}</span>'
    if days <= 21:
        return f'<span style="display:inline-block;background:#c97e1a;color:#fff;font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;text-transform:uppercase;letter-spacing:.05em;">Closes {label_date} ({days}d)</span>'
    return f'<span style="display:inline-block;background:#eceae4;color:#3a403a;font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;text-transform:uppercase;letter-spacing:.05em;">Closes {label_date}</span>'


def _item_card(it, accent="#4f8a6e"):
    title = it.get("title", "")
    # Email tiles link to the editorial detail page on the site (not the raw
    # source). The detail page leads with the "what it means" note and has its
    # own clear "Read the official notice" button at the bottom for users who
    # want the underlying notice. Keeps subscribers on the resource.
    item_id = it.get("id", "")
    if item_id:
        url = f"{SITE_URL}/opportunity#{item_id}"
    else:
        url = it.get("source_url") or SITE_URL
    source = it.get("source", "")
    category = it.get("category", "")
    value = it.get("value", "")
    means = it.get("means", "")
    deadline_chip = _deadline_chip(it.get("deadline", ""))

    category_html = (
        f'<div style="font-size:10.5px;color:{accent};font-weight:800;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">{category}</div>'
        if category else ""
    )
    source_bits = " &middot; ".join(x for x in [source, value] if x)
    source_html = (
        f'<div style="font-size:13px;color:#5f655f;margin-top:4px;">{source_bits}</div>'
        if source_bits else ""
    )
    means_html = (
        f'<div style="font-size:14px;color:#2a302a;line-height:1.55;margin:12px 0 14px;padding:12px 14px;background:#f8f7f2;border-radius:8px;">{means}</div>'
        if means else '<div style="height:8px;"></div>'
    )

    # Two-cell footer: chip on left, link on right. Tables for Outlook safety.
    chip_cell = deadline_chip or '<span style="font-size:11px;color:#8a918a;font-weight:600;letter-spacing:.04em;text-transform:uppercase;">Ongoing</span>'
    return f"""
      <tr><td style="padding:8px 0;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border:1px solid #e8e6dd;border-radius:12px;">
          <tr><td style="padding:20px 22px 18px;border-left:3px solid {accent};border-top-left-radius:12px;border-bottom-left-radius:12px;">
            {category_html}
            <div style="font-size:17px;font-weight:800;line-height:1.3;color:#0e1410;letter-spacing:-0.01em;">
              <a href="{url}" style="color:#0e1410;text-decoration:none;">{title}</a>
            </div>
            {source_html}
            {means_html}
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td align="left" style="vertical-align:middle;">{chip_cell}</td>
                <td align="right" style="vertical-align:middle;">
                  <a href="{url}" style="color:{accent};font-size:13px;font-weight:700;text-decoration:none;">Read more &rarr;</a>
                </td>
              </tr>
            </table>
          </td></tr>
        </table>
      </td></tr>"""


def _section(title, subtitle, items, on_site_url, browse_label, accent):
    count = len(items)
    if count:
        rows = "".join(_item_card(it, accent=accent) for it in items)
        list_html = f'<table width="100%" cellpadding="0" cellspacing="0">{rows}</table>'
        cta_html = f"""
        <div style="text-align:center;margin-top:18px;">
          <a href="{on_site_url}" style="display:inline-block;background:{accent};color:#fff;font-weight:700;text-decoration:none;padding:12px 24px;border-radius:9px;font-size:14px;">{browse_label} &rarr;</a>
        </div>"""
    else:
        list_html = f"""
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr><td style="padding:24px;background:#f8f7f2;border:1px dashed #d9d7cd;border-radius:10px;text-align:center;color:#5f655f;font-size:14px;">
            Nothing new in this track today.
            <a href="{on_site_url}" style="color:{accent};font-weight:700;text-decoration:none;">Browse the existing list &rarr;</a>
          </td></tr>
        </table>"""
        cta_html = ""

    count_label = f"{count} new" if count else "Quiet day"
    return f"""
      <tr><td style="padding:8px 0 16px;">
        <div style="background:{accent};color:#fff;font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;display:inline-block;padding:6px 13px;border-radius:999px;">{title}</div>
        <div style="font-size:22px;font-weight:800;color:#0e1410;margin-top:12px;letter-spacing:-0.015em;line-height:1.25;">{subtitle}</div>
        <div style="font-size:13px;color:#5f655f;margin-top:3px;font-weight:600;">{count_label}</div>
      </td></tr>
      <tr><td>{list_html}</td></tr>
      <tr><td>{cta_html}</td></tr>"""


def render(opps, grants, is_quiet=False, weekly=False):
    today = dt.date.today().strftime("%A %-d %B %Y")
    total = len(opps) + len(grants)
    period_word = "week" if weekly else "today"
    brief_label = "The weekly brief" if weekly else "The daily brief"

    # Summary line for the dark header
    if is_quiet:
        summary = f"<strong>Nothing new this {period_word}.</strong> Showing the items closest to deadline so they don't slip."
        preheader = f"Quiet {period_word}. Here are the live items closest to deadline."
        proc_subtitle = "On the radar &middot; closest deadlines"
        grants_subtitle = "On the radar &middot; closest deadlines"
        proc_track_label = "Track 1 &middot; Procurement"
        grants_track_label = "Track 2 &middot; Grants"
    else:
        summary_pieces = []
        if opps:
            summary_pieces.append(f'<span style="color:#8fcaa9;font-weight:800;">{len(opps)}</span> procurement')
        if grants:
            summary_pieces.append(f'<span style="color:#8fcaa9;font-weight:800;">{len(grants)}</span> grant{"s" if len(grants) != 1 else ""}')
        summary = " &middot; ".join(summary_pieces) or f"Quiet {period_word} across both tracks"
        # "today" reads oddly with "this" ("1 new this today") so only the
        # weekly variant gets the "this" prefix.
        preheader_period = "this week" if weekly else "today"
        preheader = f"{total} new {preheader_period} across NHS procurement and UK healthtech funding."
        proc_subtitle = "NHS contracts and framework routes"
        grants_subtitle = "Non-dilutive UK healthtech funding"
        proc_track_label = "Track 1 &middot; Procurement"
        grants_track_label = "Track 2 &middot; Grants"

    proc_section = _section(
        title=proc_track_label,
        subtitle=proc_subtitle,
        items=opps,
        on_site_url=f"{SITE_URL}/opportunities",
        browse_label="See all procurement",
        accent="#4f8a6e",
    )
    grants_section = _section(
        title=grants_track_label,
        subtitle=grants_subtitle,
        items=grants,
        on_site_url=f"{SITE_URL}/grants",
        browse_label="See all grants",
        accent="#1f3d2d",
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>MyClinical Growth daily brief</title></head>
<body style="margin:0;padding:0;background:#eceae4;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#222823;">
  <div style="display:none;max-height:0;overflow:hidden;font-size:1px;line-height:1px;color:#eceae4;opacity:0;">{preheader}</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#eceae4;">
    <tr><td align="center" style="padding:28px 12px;">
      <table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;">

        <!-- Header -->
        <tr><td style="background-color:#0e1410;background:linear-gradient(135deg,#0b110d 0%,#16231b 100%);padding:30px 34px;border-radius:14px 14px 0 0;">
          <a href="{SITE_URL}" style="text-decoration:none;">
            <div style="color:#fff;font-size:22px;font-weight:900;letter-spacing:-0.025em;line-height:1;">MyClinical <span style="color:#8fcaa9;">Growth</span></div>
          </a>
          <div style="color:#aab1aa;font-size:13.5px;margin-top:8px;letter-spacing:.01em;">{brief_label} &middot; {today}</div>
          <div style="color:#dfe4df;font-size:14px;margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,.10);">
            {"This week" if weekly else "Today"}: {summary}.
          </div>
        </td></tr>

        <!-- Body -->
        <tr><td style="background:#fff;padding:28px 32px 8px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            {proc_section}
            <tr><td style="padding:24px 0 18px;"><div style="height:1px;background:#e8e6dd;"></div></td></tr>
            {grants_section}
          </table>
        </td></tr>

        <!-- Partners teaser -->
        <tr><td style="background:#fff;padding:6px 32px 28px;">
          <table width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="background:#f8f7f2;border-radius:12px;padding:18px 20px;font-size:13.5px;color:#3a403a;line-height:1.6;">
              <div style="font-size:10.5px;color:#5f655f;font-weight:800;letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px;">New</div>
              We&rsquo;re opening a small, paid placement for
              <a href="{SITE_URL}/bid-writers" style="color:#4f8a6e;font-weight:700;text-decoration:none;">NHS bid writers</a>
              and
              <a href="{SITE_URL}/capital" style="color:#4f8a6e;font-weight:700;text-decoration:none;">healthtech investors</a>.
              Register your interest if you&rsquo;d like to be considered.
            </td></tr>
          </table>
        </td></tr>

        <!-- Footer -->
        <tr><td style="background:#0e1410;color:#aab1aa;padding:22px 32px;border-radius:0 0 14px 14px;font-size:12px;line-height:1.65;">
          <div style="margin-bottom:8px;">
            <a href="{SITE_URL}/opportunities" style="color:#cfd3cd;text-decoration:none;font-weight:600;margin-right:16px;">Procurement</a>
            <a href="{SITE_URL}/grants" style="color:#cfd3cd;text-decoration:none;font-weight:600;margin-right:16px;">Grants</a>
            <a href="{SITE_URL}/directory" style="color:#cfd3cd;text-decoration:none;font-weight:600;margin-right:16px;">Directory</a>
            <a href="{SITE_URL}/submit?type=feedback" style="color:#cfd3cd;text-decoration:none;font-weight:600;">Leave feedback</a>
          </div>
          You&rsquo;re receiving this because you subscribed to MyClinical Growth at <a href="{SITE_URL}" style="color:#8fcaa9;">growth.myclinical.co.uk</a>.
          Prefer this weekly instead of daily? <a href="*|UPDATE_PROFILE|*" style="color:#8fcaa9;">Manage your preferences</a>.
          Or <a href="*|UNSUB|*" style="color:#8fcaa9;">unsubscribe in one click</a>. We don&rsquo;t share the list. Ever.
        </td></tr>

      </table>
    </td></tr>
  </table>
</body></html>"""
    # Return both: render() now hands back the body HTML plus the plain-text
    # preheader so main() can set Mailchimp's `preview_text` campaign field.
    # Without that field Gmail and Apple Mail fall back to scraping the first
    # visible line of body copy, which can pick up alt text or button labels.
    return html, preheader


def mc(method, path, payload=None):
    url = f"https://{PREFIX}.api.mailchimp.com/3.0{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=30) as r:
            body = r.read().decode()
            return json.loads(body) if body else {}
    except HTTPError as e:
        # Surface Mailchimp's error body so the logs tell us WHY (their 4xx
        # responses always include a JSON body with title/detail).
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = "<no body>"
        print(f"Mailchimp {method} {path} -> {e.code}: {err_body}", file=sys.stderr)
        raise


def _is_closed(it):
    """True when an item is past its deadline OR marked closed."""
    status = (it.get("status") or "").lower()
    if any(s in status for s in ("closed", "completed", "awarded")):
        return True
    d = _parse_date(it.get("deadline", ""))
    if d and (d - dt.date.today()).days < 0:
        return True
    return False


def _deadline_sort_key(it):
    """Sort key: items with closest non-past deadline first; no-deadline last."""
    d = _parse_date(it.get("deadline", ""))
    if not d:
        return (1, dt.date.max)
    days = (d - dt.date.today()).days
    if days < 0:
        return (2, d)  # past — push to the end (also caught by _is_closed)
    return (0, d)


def _fallback_items(filename, key, limit=3):
    """Top items still live, sorted by deadline urgency."""
    items = load(filename)
    live = [it for it in items if not _is_closed(it)]
    live.sort(key=_deadline_sort_key)
    return live[:limit]


def main():
    if not (API_KEY and AUDIENCE and PREFIX):
        print("Mailchimp env vars not set — skipping digest. "
              "Set MAILCHIMP_API_KEY, MAILCHIMP_AUDIENCE_ID, MAILCHIMP_SERVER_PREFIX.",
              file=sys.stderr)
        return 0

    # Weekly mode adds an extra gate: weekly only fires on Mondays.
    now = dt.datetime.utcnow()
    current_hour = now.hour
    today_iso = dt.date.today().isoformat()
    if WEEKLY_MODE:
        # Weekly only on Mondays. weekday() returns 0 for Monday.
        # FORCE_SEND bypasses the Monday-only and window checks but still
        # uses the weekly marker so we don't accidentally send twice.
        if not FORCE_SEND and now.weekday() != 0:
            print(f"Weekly mode but today is {now.strftime('%A')}, not Monday. Skipping.")
            return 0
        if not FORCE_SEND and (current_hour < WEEKLY_WINDOW_START_UTC or current_hour >= WEEKLY_WINDOW_END_UTC):
            print(f"Outside weekly send window ({WEEKLY_WINDOW_START_UTC:02d}-"
                  f"{WEEKLY_WINDOW_END_UTC:02d} UTC, now {current_hour:02d}). Skipping.")
            return 0
        # ISO week marker so we can't double-fire if a Monday run gets repeated.
        iso_year, iso_week, _ = dt.date.today().isocalendar()
        week_id = f"{iso_year}-W{iso_week:02d}"
        marker = WEEKLY_SEND_MARKER
        if not FORCE_SEND and marker.exists():
            try:
                last = marker.read_text().strip()
                if last == week_id:
                    print(f"Already sent this week ({week_id}). Skipping.")
                    return 0
            except OSError:
                pass
        lookback_hours = 24 * 7
    else:
        # Daily window gate. FORCE_SEND skips it (and the marker check) so a
        # manual workflow_dispatch can fire a digest mid-day.
        if not FORCE_SEND and (current_hour < DIGEST_WINDOW_START_UTC or current_hour >= DIGEST_WINDOW_END_UTC):
            print(f"Outside daily send window ({DIGEST_WINDOW_START_UTC:02d}-"
                  f"{DIGEST_WINDOW_END_UTC:02d} UTC, now {current_hour:02d}). Skipping digest.")
            return 0
        marker = SEND_MARKER
        if not FORCE_SEND and marker.exists():
            try:
                last = marker.read_text().strip()
                if last == today_iso:
                    print(f"Already sent today ({today_iso}). Skipping digest.")
                    return 0
            except OSError:
                pass
        if FORCE_SEND:
            print(f"FORCE_SEND=true — bypassing window and marker. now={current_hour:02d}Z, marker last sent={SEND_MARKER.read_text().strip() if SEND_MARKER.exists() else 'never'}")
        lookback_hours = 24

    # Pull procurement from BOTH live (OCDS auto-poll) and the standing/curated
    # set. The standing items normally have older published dates and won't
    # qualify as "new", but when one is freshly added or has its `updated`
    # field touched, it should be included in the brief.
    opps = recent(load("opportunities-live.json") + load("opportunities.json"), hours=lookback_hours)
    # De-duplicate by id (a curated item that later appears in the OCDS feed
    # should only show once).
    seen = set()
    unique_opps = []
    for o in opps:
        oid = o.get("id")
        if oid and oid not in seen:
            seen.add(oid)
            unique_opps.append(o)
    opps = unique_opps
    grants = recent(load("grants.json"), hours=lookback_hours)

    # Quiet-day/week fallback: if nothing new in the window, still send a brief.
    # Subscribers should hear from us every cycle. The email makes the situation
    # explicit ("Nothing new today") and lists the most imminent live items
    # from each track as a reminder.
    is_quiet = not opps and not grants
    if is_quiet:
        opps = _fallback_items("opportunities.json", "deadline", limit=3)
        grants = _fallback_items("grants.json", "deadline", limit=3)
        period_word = "week" if WEEKLY_MODE else "day"
        print(f"Quiet {period_word}: no new items in last {lookback_hours}h. "
              f"Falling back to {len(opps)} procurement + {len(grants)} grants by deadline urgency.")

    html, preheader = render(opps, grants, is_quiet=is_quiet, weekly=WEEKLY_MODE)
    today_ddmm = dt.date.today().strftime("%-d %b")
    period_word = "this week" if WEEKLY_MODE else "today"
    brief_label = "weekly brief" if WEEKLY_MODE else "daily brief"
    if is_quiet:
        subject = f"Quiet {'week' if WEEKLY_MODE else 'day'} | NHS procurement & UK healthtech funding ({today_ddmm})"
    elif opps and grants:
        subject = f"{len(opps)} procurement, {len(grants)} grant{'s' if len(grants) != 1 else ''} | {brief_label}"
    elif opps:
        subject = f"{len(opps)} new NHS procurement {'opportunity' if len(opps)==1 else 'opportunities'} {period_word}"
    else:
        subject = f"{len(grants)} new grant {'call' if len(grants)==1 else 'calls'} {period_word}"

    # Build recipients block. If frequency segmenting is enabled, daily goes
    # to anyone with DAILY=Yes OR blank (the blank rule preserves subscribers
    # from before the merge field existed). Weekly goes to WEEKLY=Yes only,
    # so legacy subscribers are not opted in by accident.
    # Off by default so we don't break sends until the DAILY/WEEKLY merge
    # fields actually exist in the audience.
    recipients = {"list_id": AUDIENCE}
    if FREQUENCY_SEGMENT_ENABLED:
        if WEEKLY_MODE:
            recipients["segment_opts"] = {
                "match": "all",
                "conditions": [{
                    "condition_type": "TextMerge",
                    "field": "WEEKLY",
                    "op": "is",
                    "value": "Yes",
                }],
            }
        else:
            # Daily: include explicit DAILY=Yes plus the existing (blank) cohort
            # who signed up before the merge field was introduced.
            recipients["segment_opts"] = {
                "match": "any",
                "conditions": [
                    {"condition_type": "TextMerge", "field": "DAILY", "op": "is", "value": "Yes"},
                    {"condition_type": "TextMerge", "field": "DAILY", "op": "is", "value": ""},
                ],
            }

    # Mailchimp's preview_text is what Gmail and Apple Mail show beside the
    # subject line in the inbox list. Strip the period_word HTML the
    # preheader already includes; the API accepts plain text only.
    import re as _re
    preview_text = _re.sub(r"<[^>]+>", "", preheader)[:140]

    campaign = mc("POST", "/campaigns", {
        "type": "regular",
        "recipients": recipients,
        "settings": {
            "subject_line": subject,
            "preview_text": preview_text,
            "title": f"Growth {brief_label} {today_iso}",
            "from_name": FROM_NAME,
            "reply_to": REPLY_TO,
        },
    })
    cid = campaign["id"]
    mc("PUT", f"/campaigns/{cid}/content", {"html": html})
    print(f"Created campaign {cid} ({len(opps)} opportunities, {len(grants)} grants)")

    if AUTOSEND:
        mc("POST", f"/campaigns/{cid}/actions/send")
        print("Campaign sent.")
    else:
        print("Campaign saved as DRAFT — review and send from Mailchimp.")

    # Record marker so subsequent runs in the same cycle skip. The workflow's
    # commit step pushes this file back to the repo as part of the data/ commit.
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        if WEEKLY_MODE:
            iso_year, iso_week, _ = dt.date.today().isocalendar()
            marker.write_text(f"{iso_year}-W{iso_week:02d}")
        else:
            marker.write_text(today_iso)
    except OSError as e:
        print(f"Warning: could not write send marker: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
