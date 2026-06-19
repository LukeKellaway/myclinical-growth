# Welcome email + dual frequency: Mailchimp setup

Three things to do in the Mailchimp UI. They are not automatable from code because they live in account settings, not in any API call we can fire from the site.

---

## 1. Add two merge fields to the audience

Audience > Settings > Audience fields and *|MERGE|* tags > Add a field.

Add two fields. Both as type **Text**, required **No**, visibility **Visible**:

| Label            | Tag      | Default value |
| ---------------- | -------- | ------------- |
| Daily brief      | `DAILY`  | Yes           |
| Weekly roundup   | `WEEKLY` | Yes           |

The default of `Yes` matters: it means the existing list (who signed up before this change) keep getting the daily brief by default and start getting the weekly roundup too. If you'd rather grandfather existing subscribers as daily-only, set WEEKLY default to `No` instead.

Once these two fields exist, the site signup form will populate them on every new submission (the form HTML and the JS handler have already been updated).

---

## 2. Flip the segmentation switch

Once the merge fields exist, go to your repo Settings > Secrets and variables > Actions and add (or update) the secret:

```
FREQUENCY_SEGMENT_ENABLED = true
```

That's the only switch. After it's set:
- The daily build sends only to subscribers with `DAILY=Yes` (or blank, to grandfather pre-change subscribers).
- The weekly build (which runs every Monday via the same workflow) sends only to subscribers with `WEEKLY=Yes`.

If you ever want to pause weekly without code changes, set every subscriber's `WEEKLY` to `No` in Mailchimp via a bulk update.

---

## 3. Set up the welcome automation

Mailchimp > Automations > Customer Journeys > Create journey > "Welcome new contacts".

Trigger:
- Starting point: **Signs up via signup form**
- Audience: MyClinical Growth
- Wait time after trigger: 0 minutes (welcome lands immediately)

Email step:
- From name: `Luke Kellaway (MyClinical Growth)`
- From email: `info@myclinical.co.uk`
- Reply to: `info@myclinical.co.uk`
- Subject line: `Welcome. Here is what to expect.`
- Preview text: `Founder note, when the first brief lands, and the portal guides worth reading before you bid for anything.`
- Content: select **Code your own > Paste in code** and paste the contents of `email-templates/welcome.html`.

Send a test to yourself before turning the journey on. Mailchimp's link checker will flag the `*|UPDATE_PROFILE|*` and `*|UNSUB|*` merge tags as warnings; that's normal because they only render for live subscribers, not in test sends.

When happy, turn the journey on. New signups receive the welcome inside a minute.

---

## What to test once it's live

1. Submit your own email through the homepage signup form with both boxes ticked. Check Mailchimp shows `DAILY=Yes` and `WEEKLY=Yes` on your new contact.
2. Sign up again with a different test email and untick Weekly. Check `WEEKLY=No` on that contact.
3. Wait for the welcome to land. Click the "Change your daily / weekly preferences" link. Confirm it opens Mailchimp's hosted profile-update page with both fields editable.
4. On the next Monday morning, check the weekly roundup goes to the `WEEKLY=Yes` segment only (count should match in the campaign report).
