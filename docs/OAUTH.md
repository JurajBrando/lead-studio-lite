# Mailbox sign-in: Google and Microsoft

Gmail/Workspace and Microsoft 365 no longer allow plain passwords for IMAP and SMTP. Lead Studio
supports the standard OAuth2 flow, so a mailbox is connected by signing in rather than by pasting an
app password.

App passwords still work and are still the simplest option for other providers (Seznam, Websupport,
Zoho, your own server).

## One-time setup

You need an OAuth client in each provider's console. This is your own client - it is not shipped
with the app, because it is tied to your account.

### Google (Gmail, Workspace)

1. [Google Cloud Console](https://console.cloud.google.com/) → new project (or reuse one).
2. **APIs & Services → OAuth consent screen**: External, add your own email as a test user.
3. **Credentials → Create credentials → OAuth client ID → Desktop app** (or Web app).
4. Add the redirect URI shown in Settings: `http://127.0.0.1:8791/oauth/callback`.
5. Copy the client id and secret into **Settings → Mailbox sign-in**.

### Microsoft (Outlook, 365)

1. [Azure Portal](https://portal.azure.com/) → **App registrations → New registration**.
2. Supported account types: "Accounts in any organizational directory and personal Microsoft accounts".
3. **Redirect URI**: Web → `http://127.0.0.1:8791/oauth/callback`.
4. **Certificates & secrets → New client secret**, copy the value.
5. **API permissions**: Microsoft Graph → delegated → `IMAP.AccessAsUser.All`, `SMTP.Send`,
   `offline_access`, `openid`, `email`, then **Grant admin consent** if you are the admin.
6. Copy the application (client) id and the secret value into Settings.

The port in the redirect URI must match the port the app runs on. If the app picks another port
because 8791 is taken, update the redirect URI in the provider console to the printed address.

## Connecting a mailbox

1. **Mailboxes → Add mailbox** - just the name and from-address; you can leave SMTP/IMAP blank.
2. On the mailbox card press **Connect Google** or **Connect Microsoft**.
3. Sign in in the new tab and approve the scopes.
4. The tab says "Connected". Reload the app: the mailbox is now in OAuth mode, with the provider's
   IMAP/SMTP hosts filled in.

## What is stored

`data/settings.json` holds your client id and secret. The database holds each mailbox's refresh
token and a short-lived access token with its expiry. Access tokens are refreshed automatically
when a send or a sync needs one.

XOAUTH2 is used for both protocols:

- SMTP: `AUTH XOAUTH2` with `user=<email>\x01auth=Bearer <token>\x01\x01`
- IMAP: `AUTHENTICATE XOAUTH2` with the same string

## Troubleshooting

| Message | Cause |
| --- | --- |
| "No Google OAuth client configured" | Client id missing in Settings. |
| "redirect_uri_mismatch" | The URI registered at the provider is not exactly the one in Settings. |
| "This mailbox is not connected through OAuth" | The mailbox is still in password mode - connect it, or switch auth type. |
| "The OAuth connection expired and no refresh token is stored" | The provider did not return a refresh token (Google only does on first consent) - reconnect with consent. |
| "invalid_grant" | The refresh token was revoked, or the password changed. Reconnect. |

Revoking access: Google Account → Security → Third-party apps; Microsoft → **My Apps**. The app
cannot recover a revoked token, it will simply report the error.
