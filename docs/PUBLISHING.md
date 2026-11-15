# Publishing Lead Studio Lite

How to get from this folder to a download link people can use. Everything here is optional —
the app runs fine from the folder as it is.

---

## 1. Put it on GitHub

Create an empty repository (no README, no `.gitignore` — this folder already has one), then:

```powershell
cd C:\path\to\LeadStudioLite
git init
git add .
git status                     # check: data/, build/, dist/ must NOT be listed
git commit -m "Lead Studio Lite 1.0.0"
git branch -M main
git remote add origin https://github.com/<your-account>/lead-studio-lite.git
git push -u origin main
```

Replace `<your-account>` in the README's clone URL and in `CHANGELOG.md` with your real account so
the links work.

**Before the first push**, confirm nothing personal goes up:

```powershell
git ls-files | Select-String -Pattern 'data/|settings.json|\.db|\.exe'
```

That must print nothing. `data/` holds your leads and mailbox credentials; it is git-ignored, but a
`git add -f` would override that.

## 2. Publish a release with the executable

The release is what people actually download. `.github/workflows/release.yml` builds it for you:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions then runs the tests, builds `LeadStudio.exe` with PyInstaller, and attaches to the
release:

- `LeadStudio.exe` — standalone Windows app, no Python needed
- `LeadStudioLite-v1.0.0-windows.zip` — exe + README + docs
- `LeadStudioLite-v1.0.0-source.zip` — clean source tree

Watch it under the repository's **Actions** tab. If you prefer building locally:

```powershell
.\BUILD_EXE.bat
python tools\package_release.py
```

Both zips land in `dist\`.

Before you upload anything, run the guard:

```powershell
python tools\check_release.py --zips
```

It fails (exit code 1) when the tree or either zip contains a `data/` folder, a database,
`settings.json`, a log, a `settings.json.broken-*` backup, a personal readme, or a real
`C:\Users\<you>\...` path inside a document; when `VERSION` and the top of `CHANGELOG.md`
disagree; and when `dist\LeadStudio.exe` is a console build instead of a windowed one. The
release workflow runs it automatically before it publishes, and CI runs the tree half on
every push.

## 3. Other places to distribute

| Place | What to upload | Notes |
| --- | --- | --- |
| GitHub Releases | the two zips + `LeadStudio.exe` | Best default: versioned, free, no expiry. |
| Your own site | `LeadStudioLite-v1.0.0-windows.zip` | Link directly to the file; the zip is self-contained. |
| Gumroad / Lemon Squeezy | the windows zip | Can be listed at €0 as a free product and used to collect emails. |
| Product Hunt / Reddit / LinkedIn | a link to the release page | Point at the release, not a raw file, so people see the README first. |
| itch.io | the windows zip | Works as a free "tool" listing. |
| GitHub Pages | `marketing/index.html` | Optional product page; entirely self-contained. |

If you gate the download behind an email form (Gumroad, ConvertKit, MailerLite), keep the GitHub
release public as well — "free and open source" is a better story than a locked zip, and the email
capture still works.

## 4. What to say when you publish it

Keep claims you can defend:

- "Free, open source (MIT), runs on your own computer."
- "Imports a CSV, verifies addresses, sends multi-step sequences from your own inbox, stops on reply."
- "No account, no subscription, your list stays in a SQLite file you own."

Avoid: "guaranteed inbox placement", "replaces your SDR", "unlimited sending", and anything implying
provider limits don't apply. They do, and saying otherwise gets the repo flagged.

## 5. Version bumps after 1.0.0

1. Change `VERSION` in `engine/config.py`.
2. Add a section to `CHANGELOG.md`.
3. Commit, then `git tag v1.0.1 && git push origin v1.0.1`.

The workflows pick the version up from that constant for the zip names.
