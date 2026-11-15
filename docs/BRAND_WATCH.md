# Brand watch

Monitor any number of businesses for copies, lookalikes and scam clones. Add a brand once; the app
keeps watching it and shows you a case file per brand.

This is the "Red Points" idea from the original Lead Studio, rebuilt without the 561k-row private
dataset: it works from the brand's own domain, public DNS and the feeds the app already collects.

---

## What a scan actually does

For each monitored brand (`acme.com`) it builds the names a fake shop or phishing site would use:

| Family | Examples for `acme.com` | Why it matters |
| --- | --- | --- |
| Other TLDs | `acme.shop`, `acme.store`, `acme.co`, `acme.de` | Cheapest clone to set up |
| Brand + word | `acmeshop.com`, `acmeofficial.com`, `acme-kaufen.com` | Typical fake-shop naming |
| Word + brand | `shopacme.com`, `officialacme.com` | Same, reversed |
| Dropped/swapped letters | `acm.com`, `acme.com` variants | Typosquatting for email and ads |
| Lookalike characters | `4cme.com`, `acrne.com` | Visual confusion in a URL bar |

Then it asks **DNS** which of them are registered, and for the ones that are, whether they have a
**mail server** (a domain that accepts mail is one someone is actively using).

It also cross-checks two corpuses the app already keeps:

- **Newly registered domains** (from the daily new-domain feed) whose name contains the brand.
- **Phishing lists** (from the fraud watch job) whose domain contains the brand.

Nothing fetches the candidate website itself — a resolved name plus an MX record is enough to open a
case, and it keeps the scan fast and quiet. A scan of 40 candidates takes a few seconds.

## How a finding is scored

| Signal | Points |
| --- | --- |
| Brand + shop/store/sale/official/kaufen/versand... | 3 (+2 for a strong word) |
| Lookalike character | 4 |
| Typo (dropped or swapped letter) | 2 |
| Other TLD | 1 |
| Hyphenated variant | 1 |
| Has a mail server | +2 |
| Different TLD from the real brand | +1 |
| Appears in the new-domain feed | 5 |
| Appears on a phishing list | 6 |

Only findings at or above `min_score` (default 4) are stored. A bare TLD swap with no mail server
scores 2 and is ignored — `acme.net` being registered is not a scam, it is usually just a squatter
or another company.

## Watching many brands

The job rotates: it scans the brands with the **oldest last scan**, up to `brands_per_run` (default
25) per run. A list of 25 brands is fully covered daily; 2,500 brands are covered in about three
months, or you raise `brands_per_run` and accept a longer run.

Every brand keeps `last_scan_ts`, so nothing is starved and nothing is scanned twice in a row.

## Using it

1. **Brands** page → type a name and a domain → **Add brand**.
2. Have a list? **Import list** — one per line, `Brand name, domain.com` or just the domain. URLs
   are reduced to their domain.
3. **Scan now** runs the scan for one brand immediately and opens its case file.
4. **Cases** shows everything found: domain, what kind of copy it is, score, whether it takes mail,
   and a link to open it (carefully).
5. **Export CSV** gives a case file you can send to the client: brand, lookalike, kind, score,
   has-mail, found-at, source.

The scheduled **Brand watch** job (Automation page, daily 08:00 by default) does the same for the
whole list, oldest first. Findings also appear in the **Signals** feed with kind
`lookalike_live` / `lookalike_nrd` / `lookalike_listed`.

## What this is not

- **A finding is not proof of fraud.** `acme.shop` existing means the name exists. It may be a
  squatter, a fan site, a legitimate company in another country, or a parked page. Open it, look at
  it, and only then describe it — to a client and especially to the domain owner.
- It does not screenshot, crawl or contact the candidate site, and it does not file complaints.
- It does not know trademarks. Whether a lookalike infringes is a legal question, not a DNS one.

## Ethics and law

Monitoring your own brand, or a client's with their permission, is ordinary brand protection. Two
rules keep it clean:

1. **Only monitor brands you are entitled to monitor** — your own, or a client's under an
   engagement. Watching a competitor's brand is market research; publishing "they have fake shops"
   without evidence is not.
2. **A lookalike domain is personal data about whoever registered it** (often a privacy-shielded
   registrant). Store what you need for the case, delete it when the case closes, and do not build
   a profile of unrelated people.

The scan is just DNS queries — the least invasive signal available — and the `pause` parameter keeps
it polite to the resolver.
