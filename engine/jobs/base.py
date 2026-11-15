"""The contract every automation module implements.

A job is a small, idempotent function that pulls something from the internet,
writes rows into `domains` / `signals` / `leads`, and returns a summary dict.
The scheduler never keeps state between runs: everything is in SQLite.
"""
import json
import time

from .. import db, net


class Job:
    def __init__(self, key, name, group, description, run, schedule="", params=None,
                 needs_network=True, daily_cap=0):
        self.key = key
        self.name = name
        self.group = group
        self.description = description
        self.run = run
        self.schedule = schedule            # "daily 06:30" | "weekly mon 07:00" | "" = manual
        self.params = params or {}
        self.needs_network = needs_network
        self.daily_cap = daily_cap


class Context:
    """Passed to every job: logging, parameters and the enrichment helpers."""

    def __init__(self, job, params=None, log_sink=None):
        self.job = job
        self.params = dict(job.params)
        self.params.update(params or {})
        self.logs = []
        self.stats = {"signals": 0, "domains": 0, "leads": 0, "warnings": 0}
        self._sink = log_sink or []

    # ---- logging
    def log(self, message):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), message)
        self.logs.append(line)
        if len(self.logs) > 4000:
            del self.logs[:1000]
        try:
            self._sink.append(line)
        except Exception:
            pass
        print("  " + message, flush=True)

    def warn(self, message):
        self.stats["warnings"] += 1
        self.log("WARN " + message)

    # ---- parameters
    def param(self, name, default=None):
        value = self.params.get(name, default)
        return default if value is None or value == "" else value

    def int_param(self, name, default=0):
        try:
            return int(self.param(name, default))
        except (TypeError, ValueError):
            return default

    def list_param(self, name, default=None):
        value = self.param(name, default or [])
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return list(value or [])

    # ---- storage helpers
    def upsert_domain(self, domain, **fields):
        domain = net.domain_of(domain) or (domain or "").strip().lower()
        if not domain or "." not in domain:
            return None
        now = db.now()
        row = db.one("SELECT * FROM domains WHERE domain=?", (domain,))
        if not row:
            payload = {"domain": domain, "tld": domain.rsplit(".", 1)[-1],
                       "first_seen_ts": now, "updated_ts": now}
            payload.update({k: v for k, v in fields.items() if v is not None})
            if "data" in payload and isinstance(payload["data"], (dict, list)):
                payload["data"] = json.dumps(payload["data"])
            db.insert("domains", payload)
            self.stats["domains"] += 1
            return domain
        patch = {k: v for k, v in fields.items() if v is not None}
        if "data" in patch and isinstance(patch["data"], (dict, list)):
            existing = {}
            try:
                existing = json.loads(row.get("data") or "{}")
            except ValueError:
                existing = {}
            existing.update(patch["data"])
            patch["data"] = json.dumps(existing)
        if patch:
            patch["updated_ts"] = now
            db.execute("UPDATE domains SET %s WHERE domain=?" % ", ".join("%s=?" % k for k in patch),
                       list(patch.values()) + [domain])
        return domain

    def add_signal(self, kind, domain="", company="", title="", source="", source_url="",
                   payload=None, ts=None):
        """Record one enrichment signal. Returns True when it is new."""
        domain = net.domain_of(domain) if domain and "." in (domain or "") else (domain or "").lower()
        try:
            cur = db.execute(
                "INSERT OR IGNORE INTO signals (ts, kind, domain, company, title, source, source_url, payload) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (ts or db.now(), kind, domain, (company or "")[:200], (title or "")[:300],
                 (source or "")[:80], (source_url or "")[:600],
                 json.dumps(payload or {}, ensure_ascii=False, default=str)[:8000]))
            created = cur.rowcount > 0
        except Exception:
            return False
        if created:
            self.stats["signals"] += 1
            if domain:
                self.upsert_domain(domain, source=source or kind)
        return created

    def find_lead(self, email="", domain="", company=""):
        if email:
            row = db.one("SELECT * FROM leads WHERE email=?", (email.lower(),))
            if row:
                return row
        if domain:
            row = db.one("SELECT * FROM leads WHERE domain=? LIMIT 1", (net.domain_of(domain),))
            if row:
                return row
        if company and len(company) > 3:
            row = db.one("SELECT * FROM leads WHERE company LIKE ? LIMIT 1", (company.strip(),))
            if row:
                return row
        return None

    def flag_lead(self, lead, kind, payload=None):
        """Attach an enrichment note to an existing lead."""
        if not lead:
            return False
        note = "[%s] %s" % (kind, json.dumps(payload or {}, ensure_ascii=False)[:300])
        existing = lead.get("notes") or ""
        if note not in existing:
            db.update("leads", lead["id"], {"notes": (existing + "\n" + note).strip()[:4000],
                                            "updated_ts": db.now()})
        return True

    def create_lead(self, email, **fields):
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            return None
        if db.one("SELECT id FROM leads WHERE email=?", (email,)):
            return None
        payload = {"email": email, "status": fields.pop("status", "new"),
                   "source": fields.pop("source", self.job.key),
                   "created_ts": db.now(), "updated_ts": db.now(),
                   "verify_status": "unverified"}
        payload.update({k: v for k, v in fields.items() if v not in (None, "")})
        if not payload.get("domain"):
            payload["domain"] = email.rsplit("@", 1)[-1]
        lead_id = db.insert("leads", payload)
        self.stats["leads"] += 1
        return lead_id

    def summary(self, **extra):
        data = {"signals": self.stats["signals"], "domains": self.stats["domains"],
                "leads": self.stats["leads"], "warnings": self.stats["warnings"]}
        data.update(extra)
        return data
