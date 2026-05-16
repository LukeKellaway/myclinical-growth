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


def render(opps, grants):
    today = dt.date.today().strftime("%A %d %B %Y")
    rows = []
    for it in opps + grants:
        means = it.get("means", "")
        meta = " · ".join(x for x in [it.get("source", ""), it.get("deadline", "")] if x)
        url = it.get("source_url") or SITE_URL
        title = it.get("title", "")
        means_div = (
            f'<div style="font-size:14px;color:#222823;margin-top:6px;border-left:2px solid #4f8a6e;padding-left:10px;">{means}</div>'
            if means else ""
        )
        rows.append(f"""
          <tr><td style="padding:14px 0;border-bottom:1px solid #e1e0d9;">
            <div style="font-size:12px;color:#4f8a6e;font-weight:700;text-transform:uppercase;letter-spacing:.04em;">{it.get('category','')}</div>
            <div style="font-size:16px;font-weight:700;margin:3px 0;">
              <a href="{url}" style="color:#0e1410;text-decoration:none;">{title}</a>
            </div>
            <div style="font-size:13px;color:#5f655f;">{meta}</div>
            {means_div}
            <div style="margin-top:8px;">
              <a href="{url}" style="color:#4f8a6e;font-size:13px;font-weight:700;text-decoration:none;">Read the notice &rarr;</a>
              &nbsp;&middot;&nbsp;
              <a href="{SITE_URL}/opportunities.html" style="color:#5f655f;font-size:13px;text-decoration:none;">see on site</a>
            </div>
          </td></tr>""")
    body = "".join(rows) or '<tr><td style="padding:20px 0;color:#5f655f;">Nothing new today — back tomorrow.</td></tr>'
    return f"""<!DOCTYPE html><html><body style="margin:0;background:#f6f6f3;font-family:Arial,Helvetica,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px;">
        <table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;">
          <tr><td style="background:#0e1410;padding:28px 32px;">
            <a href="{SITE_URL}" style="text-decoration:none;">
              <div style="color:#fff;font-size:20px;font-weight:800;">MyClinical <span style="color:#4f8a6e;">Growth</span></div>
            </a>
            <div style="color:#aab1aa;font-size:13px;margin-top:4px;">The daily brief — {today}</div>
          </td></tr>
          <tr><td style="padding:24px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0">{body}</table>
            <div style="margin-top:22px;padding-top:18px;border-top:1px solid #e1e0d9;text-align:center;">
              <a href="{SITE_URL}/opportunities.html" style="display:inline-block;background:#4f8a6e;color:#fff;font-weight:700;text-decoration:none;padding:12px 22px;border-radius:8px;font-size:14px;">Browse all opportunities</a>
            </div>
          </td></tr>
          <tr><td style="padding:20px 32px;background:#f6f6f3;font-size:12px;color:#5f655f;">
            You're receiving this because you subscribed to the MyClinical Growth brief at <a href="{SITE_URL}" style="color:#4f8a6e;">growth.myclinical.co.uk</a>.
            <a href="*|UNSUB|*" style="color:#4f8a6e;">Unsubscribe in one click</a>. We don't share the list. Ever.
          </td></tr>
        </table>
      </td></tr></table></body></html>"""


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

    opps = recent(load("opportunities-live.json"))
    grants = recent(load("grants.json"))
    if not opps and not grants:
        print("Nothing new in the last 24h — no digest created.")
        return 0

    html = render(opps, grants)
    subject = f"{len(opps)+len(grants)} new — NHS opportunities & UK healthtech funding"

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
