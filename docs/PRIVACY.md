# Privacy and data handling

Lead Studio Lite is a local application. There is no server of ours, no account, no analytics and
no telemetry. The short version: **everything you put in stays on the computer you installed it on.**

## What is stored, and where

All of it lives in the `data` folder next to the app:

| File | Contents |
| --- | --- |
| `data/leadstudio.db` | Your leads, sequences, every email you sent, detected replies and bounces, the suppression list, and an activity log. |
| `data/settings.json` | App settings, plus mailbox credentials: SMTP/IMAP user names and passwords (needed to send mail on your behalf). |
| `data/logs/app.log` | Technical log used for troubleshooting. Contains email addresses and error messages, never passwords. |

**Credentials are stored in plain text**, protected only by your Windows user account and disk.
That is why the app asks for an *app password* rather than a main account password: an app password
can be revoked without changing your real login. If you want them encrypted at rest, use BitLocker
or another full-disk encryption tool.

## What leaves your computer

Only what has to:

1. **Email you send** — through your own SMTP server, to your own contacts.
2. **DNS lookups** — when you press *Verify*, the domain part of the address is looked up to find
   its mail server. No addresses are sent to a verification service.
3. **Optional SMTP probe** — if you enable it, the app connects to the recipient's mail server to ask
   whether the mailbox exists. Off by default; some providers treat this as suspicious.
4. **Optional tracking** — if you set a public tracking URL, open/click events are recorded from the
   recipient's mail client to that URL, which points at this app.
5. **Nothing else.** No update checks, no crash reports, no "anonymous usage statistics".

## Deleting your data

- Delete individual leads, sequences, replies and sends inside the app.
- **Settings → Delete all data** wipes leads, sequences, sends, replies and the suppression list but
  keeps your mailbox credentials.
- Deleting the whole `data` folder removes every trace of the app.

Back up by copying `data/leadstudio.db` while the app is closed, or export leads to CSV at any time.

## If you give this app away

The `data` folder is excluded from version control on purpose. Before sharing a folder, a zip or a
screenshot set, check that `data/` is absent — it contains your contacts and your mailbox password.

## Your recipients' privacy

Running this app makes you a data controller for the contact details you import. In the EU/UK that
means: a lawful basis for contacting them (usually legitimate interest in a B2B context), a clear
identity and a real postal address in your emails, honouring opt-outs promptly, and keeping only
what you need. The app provides unsubscribe links and a suppression list; the rest is your process.

## Contact

This is a self-hosted tool without a vendor backend. For support, see the README; for privacy
questions about your own deployment, you are the operator.
