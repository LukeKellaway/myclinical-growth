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
AUTOSEND = os.environ.get("DIGEST_AUTOSEND", "").lower() == "true"
FROM_NAME = os.environ.get("DIGEST_FROM_NAME", "MyClinical Growth")
REPLY_TO = os.environ.get("DIGEST_REPLY_TO", "hello@myclinical.example")


def load(name):
    p = DATA / name
    if not p.exists():
        return []
    blob = json.loads(p.read_text())
    if isinstance(blob, dict):
        return blob.get("opportunities") or blob.get("grants") or blob.get("items") or []
    return blob


def recent(items, hours=24):
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    out = []
    for it in items:
        stamp = it.get("published") or it.get("updated") or ""
        try:
            d = dt.datetime.fromisoformat(stamp.replace("Z", "").split("+")[0])
            if d >= cutoff:
                out.append(it)
        except (ValueError, AttributeError):
            continue
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
                  <a href="{url}" style="color:{accent};font-size:13px;font-weight:700;text-decoration:none;">Read the notice &rarr;</a>
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


def render(opps, grants):
    today = dt.date.today().strftime("%A %-d %B %Y")
    total = len(opps) + len(grants)

    # Summary line for the dark header
    summary_pieces = []
    if opps:
        summary_pieces.append(f'<span style="color:#8fcaa9;font-weight:800;">{len(opps)}</span> procurement')
    if grants:
        summary_pieces.append(f'<span style="color:#8fcaa9;font-weight:800;">{len(grants)}</span> grant{"s" if len(grants) != 1 else ""}')
    summary = " &middot; ".join(summary_pieces) or "Quiet day across both tracks"

    # Preheader (gmail/iOS snippet)
    preheader = f"{total} new today across NHS procurement and UK healthtech funding."

    proc_section = _section(
        title="Track 1 &middot; Procurement",
        subtitle="NHS contracts and framework routes",
        items=opps,
        on_site_url=f"{SITE_URL}/opportunities.html",
        browse_label="See all procurement",
        accent="#4f8a6e",
    )
    grants_section = _section(
        title="Track 2 &middot; Grants",
        subtitle="Non-dilutive UK healthtech funding",
        items=grants,
        on_site_url=f"{SITE_URL}/grants.html",
        browse_label="See all grants",
        accent="#1f3d2d",
    )

    return f"""<!DOCTYPE html>
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
          <div style="color:#aab1aa;font-size:13.5px;margin-top:8px;letter-spacing:.01em;">The daily brief &middot; {today}</div>
          <div style="color:#dfe4df;font-size:14px;margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,.10);">
            Today: {summary}.
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
              <a href="{SITE_URL}/bid-writers.html" style="color:#4f8a6e;font-weight:700;text-decoration:none;">NHS bid writers</a>
              and
              <a href="{SITE_URL}/capital.html" style="color:#4f8a6e;font-weight:700;text-decoration:none;">healthtech investors</a>.
              Register your interest if you&rsquo;d like to be considered.
            </td></tr>
          </table>
        </td></tr>

        <!-- Footer -->
        <tr><td style="background:#0e1410;color:#aab1aa;padding:22px 32px;border-radius:0 0 14px 14px;font-size:12px;line-height:1.65;">
          <div style="margin-bottom:8px;">
            <a href="{SITE_URL}/opportunities.html" style="color:#cfd3cd;text-decoration:none;font-weight:600;margin-right:16px;">Procurement</a>
            <a href="{SITE_URL}/grants.html" style="color:#cfd3cd;text-decoration:none;font-weight:600;margin-right:16px;">Grants</a>
            <a href="{SITE_URL}/directory.html" style="color:#cfd3cd;text-decoration:none;font-weight:600;">Directory</a>
          </div>
          You&rsquo;re receiving this because you subscribed to MyClinical Growth at <a href="{SITE_URL}" style="color:#8fcaa9;">growth.myclinical.co.uk</a>.
          <a href="*|UNSUB|*" style="color:#8fcaa9;">Unsubscribe in one click</a>. We don&rsquo;t share the list. Ever.
        </td></tr>

      </table>
    </td></tr>
  </table>
</body></html>"""


def mc(method, path, payload=None):
    url = f"https://{PREFIX}.api.mailchimp.com/3.0{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    })
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    if not (API_KEY and AUDIENCE and PREFIX):
        print("Mailchimp env vars not set — skipping digest. "
              "Set MAILCHIMP_API_KEY, MAILCHIMP_AUDIENCE_ID, MAILCHIMP_SERVER_PREFIX.",
              file=sys.stderr)
        return 0

    # Pull procurement from BOTH live (OCDS auto-poll) and the standing/curated
    # set. The standing items normally have older published dates and won't
    # qualify as "new", but when one is freshly added or has its `updated`
    # field touched, it should be included in the brief.
    opps = recent(load("opportunities-live.json") + load("opportunities.json"))
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
    grants = recent(load("grants.json"))
    if not opps and not grants:
        print("Nothing new in the last 24h — no digest created.")
        return 0

    html = render(opps, grants)
    # Subject line foregrounds the procurement/grants split so subscribers
    # can see at-a-glance which track this digest is heavier on.
    if opps and grants:
        subject = f"{len(opps)} procurement, {len(grants)} grant{'s' if len(grants) != 1 else ''} | daily brief"
    elif opps:
        subject = f"{len(opps)} new NHS procurement {'opportunity' if len(opps)==1 else 'opportunities'}"
    else:
        subject = f"{len(grants)} new grant {'call' if len(grants)==1 else 'calls'}"

    campaign = mc("POST", "/campaigns", {
        "type": "regular",
        "recipients": {"list_id": AUDIENCE},
        "settings": {
            "subject_line": subject,
            "title": f"Growth daily brief {dt.date.today().isoformat()}",
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
