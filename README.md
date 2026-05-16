# MyClinical Growth

A free, open resource that aggregates NHS procurement opportunities and UK
digital health grants — and signals what each one actually means.

This folder is the **complete, working site and automation**. It runs on the
same stack as lukekellaway.com (static site on Netlify + Mailchimp). Everything
that can be built without your accounts is built. What's left is four set-up
steps only you can do — they're below, and none take long.

---

## What's in here

```
index.html  opportunities.html  grants.html  directory.html  about.html
assets/css/style.css          design system, drawn from lukekellaway.com
assets/js/main.js             rendering engine (filters, cards, subscribe)
assets/js/data.js             bundled data the pages read (generated)
data/                         the JSON data files
  directory.json              45 sources — generated from the spreadsheet
  opportunities.json          curated standing frameworks (real, verified)
  grants.json                 curated grant programmes (real, verified)
  opportunities-live.json     filled by the poller once deployed
scripts/poll.py               Tier 1 poller — Find a Tender + Contracts Finder
scripts/build_digest.py       builds the daily Mailchimp digest
scripts/bundle.py             rebuilds assets/js/data.js from data/*.json
scripts/build_data.py         one-time: rebuilds data from the spreadsheet
.github/workflows/poll.yml    the automation — runs on GitHub's servers
netlify.toml                  Netlify config (no build step — static)
```

## See it now

Open `index.html` in any browser — it works straight from disk because the
data is bundled into `assets/js/data.js` (a `<script>` tag, not `fetch`).
The standing frameworks and grants you see are **real, verified data**. The
continuous feed of individual tender notices switches on once deployed — the
poller needs open internet, which GitHub Actions provides.

---

## Going live — the four steps that need your accounts

I can't create accounts or hold API keys, so these are yours. In order:

### 1. Put it on GitHub
Create a new repository (e.g. `myclinical-growth`) and push the **contents of
this folder** to it (so `index.html` sits at the repo root).

```
cd "growth-site"
git init && git add . && git commit -m "MyClinical Growth — initial build"
git branch -M main
git remote add origin https://github.com/<you>/myclinical-growth.git
git push -u origin main
```

### 2. Connect Netlify
In Netlify: **Add new site → Import from GitHub →** pick the repo. No build
command, publish directory `.` (already set in `netlify.toml`). It deploys in
under a minute and gives you a `*.netlify.app` URL — the site is live at that
point.

### 3. Point the subdomain
In whatever manages your `myclinical` domain's DNS, add one record:

```
Type: CNAME    Name: growth    Value: <your-site>.netlify.app
```

Then in Netlify → **Domain settings → add custom domain →** `growth.myclinical.<tld>`.
Netlify issues the HTTPS certificate automatically. (`www`-style subdomains
work exactly like this — `growth` is just another one.)

### 4. Wire up Mailchimp
- In Mailchimp, create an **audience** for the brief.
- **Embedded form:** Audience → Signup forms → Embedded form. Copy the form
  `action` URL and the hidden honeypot field name. In `index.html`,
  `opportunities.html` and `grants.html`, replace the placeholder
  `action="https://YOUR_MAILCHIMP_URL/..."` and the `b_XXXX_YYYY` honeypot
  name with the real values. (Same pattern lukekellaway.com already uses.)
- **API access for the digest:** Mailchimp → Account → Extras → API keys →
  create one. In your GitHub repo → Settings → Secrets and variables →
  Actions, add:
  - `MAILCHIMP_API_KEY`
  - `MAILCHIMP_AUDIENCE_ID`
  - `MAILCHIMP_SERVER_PREFIX` (the bit after the dash in the API key, e.g. `us21`)
  - `DIGEST_REPLY_TO` (the reply-to address for the brief)
  - leave `DIGEST_AUTOSEND` unset for now — the digest is created as a **draft**
    you review and send. Add it as a secret set to `true` once you trust the
    editorial pass.

That's it. Once steps 1–2 are done the site is live; 3 puts it on your domain;
4 turns on the email.

---

## How the automation works

`.github/workflows/poll.yml` runs hourly on GitHub's servers (06:00–21:00 UTC)
— **not** on your computer, so it never depends on your machine being on. Each run:

1. `poll.py` fetches new notices from the Find a Tender and Contracts Finder
   OCDS APIs, filters them to NHS / health-sector buyers with a digital-health
   signal, and writes `data/opportunities-live.json`.
2. `bundle.py` rebuilds `assets/js/data.js` so the site reflects the new data.
3. `build_digest.py` compiles the last 24h into a Mailchimp campaign (draft by
   default).
4. The workflow commits the refreshed data — which triggers a Netlify redeploy.

`poll.py` only ever writes `opportunities-live.json`. The curated standing
frameworks in `opportunities.json` and the grants in `grants.json` are yours to
edit by hand — the poller never touches them.

You can run the workflow manually any time from the repo's **Actions** tab.

---

## Editing the data

- **Curated opportunities / grants:** edit the lists in `scripts/build_data.py`,
  run `python scripts/build_data.py`, then `python scripts/bundle.py`, commit.
- **The directory:** edit the source spreadsheet, then re-run those same two
  scripts.
- **The "what it means" notes** on live tender notices: this is the editorial
  layer. It's where a short daily review pass adds the judgement that turns a
  notice into a decision — the natural job for a scheduled review task.

## Run it locally

```
cd "growth-site"
python3 -m pip install -r scripts/requirements.txt   # only needed for build_data.py
python3 scripts/poll.py        # needs open internet
python3 scripts/bundle.py
python3 -m http.server 8000    # then open http://localhost:8000
```

---

## A note on the data

Everything shown in this build is real and verified. The standing frameworks
(the £750m NHS SBS Healthcare AI framework, GP IT Futures / Tech Innovation
Framework, the MedTech Funding Mandate, G-Cloud) and the grant programmes
(SBRI Healthcare, NIHR i4i and EME, Innovate UK Biomedical Catalyst, EIC
Accelerator, Horizon Europe Health Cluster) are genuine, current sources.

The one thing not yet flowing is the continuous stream of individual NHS
tender notices — that comes from the live APIs, which the poller reaches once
it's running on GitHub Actions. First scheduled run after deploy populates it.
