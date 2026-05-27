// MyClinical Growth — subscribe Netlify Function.
//
// Takes a POSTed form from index.html / opportunities.html / grants.html /
// opportunity.html, validates it, calls Mailchimp's Members API server-side
// (PUT /lists/{id}/members/{md5(email)} with status_if_new=pending so a
// double-opt-in email is triggered), then 302-redirects the browser to a
// branded page on growth.myclinical.co.uk. The subscriber never sees a
// Mailchimp-hosted page.
//
// Environment variables (set in Netlify dashboard, Site settings > Build &
// deploy > Environment):
//   MAILCHIMP_API_KEY        the long key ending in "-us20"
//   MAILCHIMP_AUDIENCE_ID    d0dd920405
//   MAILCHIMP_SERVER_PREFIX  us20

const crypto = require("crypto");

const SUCCESS_URL = "/signing-up.html";
const ERROR_URL   = "/signup-error.html";

exports.handler = async function (event) {
  if (event.httpMethod !== "POST") {
    return redirect(ERROR_URL + "?reason=method");
  }

  // Parse the POSTed form body (application/x-www-form-urlencoded)
  const params = new URLSearchParams(event.body || "");
  const email = (params.get("EMAIL") || "").trim().toLowerCase();
  const daily = (params.get("DAILY") || "Yes").trim();
  const weekly = (params.get("WEEKLY") || "Yes").trim();

  // Honeypot: any input starting with "b_" filled = bot. Silently succeed
  // so the bot thinks it won, but never hits Mailchimp.
  for (const [k, v] of params.entries()) {
    if (k.startsWith("b_") && v) {
      return redirect(SUCCESS_URL);
    }
  }

  // Basic email shape check. Mailchimp will reject anything malformed
  // anyway, but this prevents pointless API calls.
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return redirect(ERROR_URL + "?reason=email");
  }

  const apiKey = process.env.MAILCHIMP_API_KEY || "";
  const audience = process.env.MAILCHIMP_AUDIENCE_ID || "";
  const prefix = process.env.MAILCHIMP_SERVER_PREFIX || "";

  if (!apiKey || !audience || !prefix) {
    console.error("Mailchimp env vars missing: API_KEY=%s, AUDIENCE_ID=%s, SERVER_PREFIX=%s",
      apiKey ? "set" : "MISSING",
      audience ? "set" : "MISSING",
      prefix ? "set" : "MISSING");
    return redirect(ERROR_URL + "?reason=config");
  }

  const memberHash = crypto.createHash("md5").update(email).digest("hex");
  const url = `https://${prefix}.api.mailchimp.com/3.0/lists/${audience}/members/${memberHash}`;

  // PUT upsert: creates with status_if_new=pending (double opt-in) for new
  // emails, updates merge fields on existing without changing their status.
  const body = {
    email_address: email,
    status_if_new: "pending",
    merge_fields: {
      DAILY: daily === "Yes" ? "Yes" : "No",
      WEEKLY: weekly === "Yes" ? "Yes" : "No",
    },
  };

  try {
    const res = await fetch(url, {
      method: "PUT",
      headers: {
        "Authorization": "Basic " + Buffer.from("anystring:" + apiKey).toString("base64"),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });

    if (res.ok) {
      return redirect(SUCCESS_URL);
    }

    const data = await res.json().catch(() => ({}));
    const title = (data.title || "").toLowerCase();
    // "Member Exists" or "Member In Compliance State" = already on the list
    // in some state. We treat that as success from the user's perspective
    // (they're effectively subscribed or have been; nothing to do).
    if (res.status === 400 && (title.includes("exists") || title.includes("compliance"))) {
      return redirect(SUCCESS_URL + "?already=1");
    }
    console.error("Mailchimp API error %s: %j", res.status, data);
    return redirect(ERROR_URL + "?reason=mailchimp&status=" + res.status);
  } catch (err) {
    console.error("Subscribe function error:", err);
    return redirect(ERROR_URL + "?reason=exception");
  }
};

function redirect(location) {
  return {
    statusCode: 302,
    headers: { Location: location, "Cache-Control": "no-store" },
    body: "",
  };
}
