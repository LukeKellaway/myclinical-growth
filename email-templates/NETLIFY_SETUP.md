# Netlify Function setup for the signup form

The signup form now POSTs to `/.netlify/functions/subscribe`, a small serverless function that calls Mailchimp's Members API server-side and then redirects to branded pages on `growth.myclinical.co.uk`. Subscribers never see a Mailchimp-hosted page.

Two clicks needed for you, then it works.

---

## 1. Add the Mailchimp API key to Netlify

Netlify dashboard > Site `myclinical-growth` > Site settings > Build & deploy > Environment > Environment variables > Add a variable.

Add three:

| Key                        | Value                                       |
| -------------------------- | ------------------------------------------- |
| `MAILCHIMP_API_KEY`        | (your full Mailchimp key, ending in `-us20`) |
| `MAILCHIMP_AUDIENCE_ID`    | `d0dd920405`                                |
| `MAILCHIMP_SERVER_PREFIX`  | `us20`                                      |

You already have the API key in GitHub Actions secrets, same value. Copy it in.

Apply to: All scopes (Production, Deploy previews, Branch deploys). Save.

After saving, trigger a redeploy: Deploys > Trigger deploy > Deploy site. The next build picks up the env vars and the function comes online.

---

## 2. Redirect the Mailchimp "you're confirmed" page back to our thanks page

When a subscriber clicks the link in the double-opt-in confirmation email, Mailchimp processes it and shows its own "confirmed" page. We redirect that to our branded `/thanks.html` so they end up back on our site.

Mailchimp > Audience > MyClinical Growth > Forms (in the left nav under Audience) > "Signup form" form-builder.

In the Form Designer page, in the top dropdown that says "Forms and response emails" > pick **"Confirmation thank you page"**.

Scroll to the bottom and tick **"Send subscribers to another URL after they confirm their signup"**. Paste:

```
https://growth.myclinical.co.uk/thanks.html
```

Save & Close.

Repeat for **"Subscribe form success page"** (same checkbox + URL) so anyone hitting the legacy Mailchimp hosted form also gets bounced back to us.

---

## 3. (Optional but recommended) Tidy the confirmation email body

This is the email Mailchimp sends asking subscribers to confirm. The body still has Mailchimp's default copy. Worth replacing.

Same Form Designer page > Forms and response emails dropdown > pick **"Opt-in confirmation email"**.

Edit the subject and body. Suggested:

- Subject: `Confirm your MyClinical Growth subscription`
- Preview text: `One click and you're in.`
- Body: replace the default with something like:

```
Almost there.

Click the button below to confirm your subscription to the MyClinical Growth brief.

[Confirm subscription]   <-- this stays as the *|CONFIRM_LINK|* button

That's all. Your first brief lands the next morning.

Luke
```

Save & Close.

---

## What's deployed once env vars are set

- `netlify/functions/subscribe.js` — receives the form POST, calls Mailchimp's PUT /lists/{id}/members/{md5(email)} with `status_if_new=pending` (triggers double opt-in), then redirects.
- `signing-up.html` — "Check your inbox" page, branded.
- `thanks.html` — "You're in" page, branded.
- `signup-error.html` — Error page with a Try again button and an email link, branded.

The form action is `/.netlify/functions/subscribe` on `index.html`, `opportunities.html`, `grants.html`, and `opportunity.html`. `target="_blank"` removed so the redirect happens in the same tab.

---

## How to test

1. Deploy with the env vars set.
2. Go to growth.myclinical.co.uk, enter a real email, click Join the list.
3. Expect to land on `/signing-up.html` immediately.
4. Check your inbox for the Mailchimp confirmation email.
5. Click the link in that email.
6. Expect to land on `/thanks.html` (because of step 2 above).
7. Welcome journey email lands seconds later (the automation you already turned on).

If anything fails, you'll land on `/signup-error.html` with a `?reason=...` query param. Common reasons:

| reason     | meaning                                                              |
| ---------- | -------------------------------------------------------------------- |
| `config`   | One of the three env vars is missing. Check Netlify.                 |
| `email`    | Submitted email failed basic format validation.                      |
| `mailchimp`| Mailchimp returned an error. `&status=` shows the HTTP status code.  |
| `exception`| Function threw. Check Netlify function logs for the stack.           |

Function logs: Netlify dashboard > Functions > `subscribe` > Logs.
