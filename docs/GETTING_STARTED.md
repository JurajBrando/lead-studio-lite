# Getting started

A practical walkthrough. Ten minutes end to end, assuming you already have a mailbox.

---

## 1. Connect your mailbox

**Mailboxes → Add mailbox.**

| Provider | SMTP host / port / security | IMAP host / port |
| --- | --- | --- |
| Gmail / Google Workspace | `smtp.gmail.com` / 587 / STARTTLS | `imap.gmail.com` / 993 |
| Outlook / Microsoft 365 | `smtp-mail.outlook.com` or `smtp.office365.com` / 587 / STARTTLS | `outlook.office365.com` / 993 |
| Zoho | `smtp.zoho.eu` / 465 / SSL | `imap.zoho.eu` / 993 |
| Seznam | `smtp.seznam.cz` / 465 / SSL | `imap.seznam.cz` / 993 |
| Websupport | `smtp.m1.websupport.sk` / 587 / STARTTLS | `imap.m1.websupport.sk` / 993 |
| Your own server | ask your host | ask your host |

**Passwords:** Gmail, Outlook, Zoho and most providers need an **app password** created in the
account's security settings (usually under "two-step verification"). Normal passwords are refused
for SMTP/IMAP, and that is a good thing.

Press **Test connection**. Green on SMTP means you can send; green on IMAP means replies will be
picked up. Then **Send test email** to yourself and confirm it arrives in the inbox, not spam.

> Deliverability note: a brand-new mailbox that sends 100 cold emails on day one gets filtered.
> Start with 10–20 a day, reply to the people who answer, and raise the cap over a few weeks.

## 2. Import leads

**Leads → Import** (or drag a file onto the drop zone).

- CSV, TSV, semicolon-separated and Excel `.xlsx` are supported.
- One column must contain the email address. It can be called `email`, `e-mail`, `E-mail`,
  `email_address`, `work_email`, `best_email`, `contact_email`...
- Recognised columns: first name, last name, full name, company, title, domain/website, phone,
  city, country, industry, LinkedIn, notes, tags.
- Rows with the same address are skipped, already-existing addresses are counted separately,
  invalid addresses are reported but not imported.
- The summary shows exactly what was mapped, so you can see whether `Title` became *title* or not.
- Tick **"Fill empty fields on existing leads"** to enrich rows you already have.

Afterwards press **Verify emails**. Green = the domain accepts mail; amber = role address
(`info@`) or a disposable provider; red = the address cannot exist. Verification costs nothing
and saves sends.

## 3. Build a sequence

**Sequences → New sequence.** A proven three-touch shape:

| Step | Wait | Purpose |
| --- | --- | --- |
| 1 | sends immediately | One sentence on why you're writing, one concrete benefit, one small ask. |
| 2 | 3 days | Add value: a specific observation about their company, or a resource. |
| 3 | 7 days | Close the loop politely and give them an easy out. |

Merge fields:

```
{{first_name|there}}   first name, or "there" when empty
{{company}}            company name
{{title}} {{city}}     anything from the import
{{sender_name}}        from Settings
```

Rules that keep you out of spam:

- 60–120 words per email. No images. One link at most.
- No "Dear Sir/Madam", no fake "Re:" threads.
- Plain questions at the end ("Worth a 15-minute call?").
- One CTA per email.

If a required merge field is empty and there is no fallback, the lead is **paused** with a clear
reason rather than receiving `Hi ,`.

## 4. Enroll and let it run

**Sequences → Enroll leads** (or select rows in *Leads* and press *Enroll selected*).

- Pick the mailbox that sends.
- The queue sends **one email at a time**, never faster than the gap you set (default 45s + jitter).
- It respects the sending window (default 08:00–18:00), weekdays only, and the per-mailbox daily cap.
- Closing the app stops the queue. Reopening resumes where it left off; nothing is sent twice.

**Send next now** on the dashboard forces one immediate send, but the daily cap still applies.

## 5. Work the replies

**Inbox** shows replies and bounces. The app polls IMAP every 10 minutes; *Check mail now* is
immediate. When someone replies:

- their sequence stops immediately,
- the lead becomes **replied**,
- the message appears in the inbox with the company name attached.

Bounces mark the lead **bounced** and suppress the address forever. Unsubscribe clicks do the same.

Move leads along the pipeline with *Edit → Status*: replied → interested → won/lost. Export
anytime to CSV.

## 6. Tracking (optional)

Open/click tracking needs a URL that the recipient's mail client can reach. **Settings → Public
address for tracking links.**

- While the app runs on your PC, `http://127.0.0.1:8791` only works on your machine. It is fine for
  testing, useless for real tracking.
- For real tracking, expose the app through a tunnel (Cloudflare Tunnel, ngrok...) and put that
  HTTPS address in the field.
- Leave it empty to disable tracking entirely. Nothing else depends on it.

Some privacy laws treat tracking pixels as personal-data processing. Disclose it if you use it.

## Compliance in one paragraph

Cold email to business addresses is legal in most of the EU/UK only with a legitimate interest
basis (GDPR), a clear identity, a real postal address and an easy opt-out; in the US, CAN-SPAM
requires accurate headers, a physical address and honouring opt-outs within 10 days. The app
provides unsubscribe links and a suppression list, but the legal basis, the list's provenance and
the content are yours. When in doubt, warm the contact first (LinkedIn, a phone call) and email
second.

## Troubleshooting quick reference

| Symptom | Cause |
| --- | --- |
| `SMTP refused the login` | Normal password used instead of an app password, or SMTP disabled on the account. |
| `Could not connect to ...` | Wrong host/port, or the provider blocks the port on your network. |
| `Nothing is due right now` | Queue empty, or the next step is scheduled for a later day. |
| Sends stop after N emails | Daily cap reached for that mailbox. |
| Replies not detected | No IMAP configured, or app password missing. |
| App opens on a different port | 8791 was taken; the console window prints the real address. |
