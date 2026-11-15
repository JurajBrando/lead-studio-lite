"""Demo data: enough to explore the app before connecting a real mailbox.

Every seeded lead uses a reserved example domain, is tagged source='demo' and
sits in a paused sequence, so nothing can accidentally be emailed.
"""
import random
import time

from . import db

FIRST = ["Marta", "Tomas", "Elena", "Lukas", "Petra", "Daniel", "Sofia", "Martin", "Ivana", "Jakub",
         "Clara", "Oliver", "Nina", "Felix", "Amelia", "Victor", "Julia", "Radek", "Anna", "Eric"]
LAST = ["Novak", "Horvath", "Kovac", "Bauer", "Schmidt", "Dubois", "Rossi", "Andersen", "Nowak",
        "Varga", "Weber", "Moreau", "Lindqvist", "Petrov", "Silva", "Okafor", "Meyer", "Jansen"]
COMPANIES = [
    ("Northline Logistics", "logistics", "Rotterdam"), ("Vertex Dental Group", "dental", "Munich"),
    ("Lumen Interiors", "interiors", "Vienna"), ("Kestrel Analytics", "analytics", "Prague"),
    ("Bluepine Clinics", "healthcare", "Bratislava"), ("Harbor & Grain", "food", "Hamburg"),
    ("Stonebridge Legal", "legal", "Warsaw"), ("Aurora Fitness", "fitness", "Budapest"),
    ("Meridian Roofing", "construction", "Lyon"), ("Cobalt Software", "software", "Berlin"),
    ("Pinecrest Realty", "real estate", "Vienna"), ("Silverline Motors", "automotive", "Brno"),
    ("Oakfield Pharmacy", "pharmacy", "Zurich"), ("Nimbus Travel", "travel", "Lisbon"),
    ("Juniper Beauty", "beauty", "Milan"), ("Redwood Accounting", "accounting", "Prague"),
    ("Falcon Print", "printing", "Krakow"), ("Lakeview Hotels", "hospitality", "Salzburg"),
    ("Ironclad Security", "security", "Amsterdam"), ("Greenfield Farms", "agriculture", "Brno"),
    ("Atlas Freight", "logistics", "Hamburg"), ("Brightpath Education", "education", "Vienna"),
    ("Copperline Brewing", "beverage", "Pilsen"), ("Sundance Solar", "energy", "Barcelona"),
]
TITLES = ["Owner", "Managing Director", "Head of Marketing", "Operations Manager", "Founder",
          "Sales Director", "Practice Manager", "CEO"]
STATUS_WEIGHTS = (["new"] * 10 + ["contacted"] * 6 + ["replied"] * 3 +
                  ["interested"] * 2 + ["won", "lost", "bounced"])

SEQ_COLD = {
    "name": "Cold outreach - 3 touches",
    "description": "The default cadence: short intro, a value follow-up, then a polite close.",
    "steps": [
        {"delay_days": 0,
         "subject": "Quick question about {{company}}",
         "body": "Hi {{first_name|there}},\n\nI'm reaching out because I work with {{industry}} teams "
                 "that want more predictable inbound leads without adding headcount.\n\n"
                 "Worth a 15-minute call this week?\n\nBest,\n{{sender_name}}"},
        {"delay_days": 3,
         "subject": "Re: Quick question about {{company}}",
         "body": "Hi {{first_name|there}},\n\nFollowing up on my note - I put together a short breakdown "
                 "of what usually moves the needle first for teams like {{company}}.\n\n"
                 "Want me to send it over?\n\n{{sender_name}}"},
        {"delay_days": 7,
         "subject": "Closing the loop",
         "body": "Hi {{first_name|there}},\n\nI don't want to keep landing in your inbox, so this is my "
                 "last note. If lead generation is not a priority this quarter, no problem at all.\n\n"
                 "If it becomes one, I'm easy to find.\n\n{{sender_name}}"},
    ],
}

SEQ_WARM = {
    "name": "Replied leads - booked call follow-up",
    "description": "What to send after someone answers: confirm, deliver, keep it short.",
    "steps": [
        {"delay_days": 0,
         "subject": "Great to hear from you, {{first_name|there}}",
         "body": "Hi {{first_name|there}},\n\nThanks for getting back to me. I'll keep this short: "
                 "does Tuesday or Thursday work better for a 20-minute call?\n\n{{sender_name}}"},
        {"delay_days": 4,
         "subject": "Checking in - {{company}}",
         "body": "Hi {{first_name|there}},\n\nJust floating this back to the top of your inbox. "
                 "Happy to send a one-page summary instead if that's easier.\n\n{{sender_name}}"},
    ],
}


def _domain(company):
    base = "".join(ch for ch in company.lower() if ch.isalnum())[:18]
    return base + ".example.com"


def seed():
    ts = db.now()
    rand = random.Random(7)
    existing = db.scalar("SELECT COUNT(*) FROM leads WHERE source='demo'")
    if existing:
        return {"ok": True, "already": True, "leads": existing}

    seq_ids = {}
    for spec, key in ((SEQ_COLD, "cold"), (SEQ_WARM, "warm")):
        sid = db.insert("sequences", {"name": spec["name"], "description": spec["description"],
                                      "active": 1, "created_ts": ts, "updated_ts": ts})
        seq_ids[key] = sid
        for i, st in enumerate(spec["steps"], start=1):
            db.insert("steps", {"sequence_id": sid, "step_no": i,
                                "delay_days": st["delay_days"], "subject": st["subject"],
                                "body": st["body"], "stop_on_reply": 1})

    lead_ids = []
    for i, (company, industry, city) in enumerate(COMPANIES):
        first = rand.choice(FIRST)
        last = rand.choice(LAST)
        domain = _domain(company)
        email = "%s.%s@%s" % (first.lower(), last.lower(), domain)
        status = STATUS_WEIGHTS[i % len(STATUS_WEIGHTS)]
        created = ts - rand.randint(2, 40) * 86400
        row = {
            "email": email, "first_name": first, "last_name": last, "company": company,
            "title": rand.choice(TITLES), "domain": domain, "city": city,
            "country": "EU", "industry": industry, "status": status, "source": "demo",
            "verify_status": rand.choice(["valid", "valid", "valid", "risky", "unverified"]),
            "verify_detail": "Demo data", "tags": "demo", "created_ts": created,
            "updated_ts": created,
            "last_contact_ts": (created + 86400) if status not in ("new",) else 0,
            "replied_ts": (created + 3 * 86400) if status in ("replied", "interested", "won") else 0,
        }
        lead_ids.append(db.insert("leads", row))

    # a little history so the dashboard and inbox are not empty
    send_n = 0
    for i, lead_id in enumerate(lead_ids):
        if i % 3:
            continue
        lead = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
        when = ts - rand.randint(1, 5) * 86400
        db.insert("sends", {
            "enrollment_id": 0, "step_id": 0, "lead_id": lead_id, "mailbox_id": 0,
            "to_email": lead["email"], "subject": "Quick question about %s" % lead["company"],
            "body": "Demo history", "message_id": "<demo-%d@example.com>" % lead_id,
            "token": "demo%d" % lead_id, "status": "sent", "sent_ts": when,
            "opened_ts": when + 3600 if i % 2 == 0 else 0, "opens": 1 if i % 2 == 0 else 0,
        })
        send_n += 1

    for lead_id in lead_ids:
        lead = db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
        if lead["status"] not in ("replied", "interested", "won"):
            continue
        db.insert("replies", {
            "lead_id": lead_id, "mailbox_id": 0, "from_email": lead["email"],
            "subject": "Re: Quick question about %s" % lead["company"],
            "snippet": "Thanks for reaching out - timing is interesting. Can you send more detail?",
            "message_id": "<demo-reply-%d@example.com>" % lead_id,
            "received_ts": lead["replied_ts"] or ts, "is_bounce": 0, "seen": 0,
        })

    # paused enrollment so the pipeline stays silent until the user opts in
    for lead_id in lead_ids[:8]:
        db.insert("enrollments", {
            "sequence_id": seq_ids["cold"], "lead_id": lead_id, "mailbox_id": 0,
            "status": "paused", "step_no": 1, "next_run_ts": ts + 3600,
            "started_ts": ts - 86400,
        })

    db.event("demo", "seeded %d demo leads" % len(lead_ids))
    return {"ok": True, "leads": len(lead_ids), "sequences": 2, "sends": send_n}


def remove():
    seq_ids = [r["id"] for r in db.rows("SELECT id FROM sequences WHERE description LIKE '%cadence%' "
                                        "OR description LIKE '%after someone answers%'")]
    lead_ids = [r["id"] for r in db.rows("SELECT id FROM leads WHERE source='demo'")]
    for lid in lead_ids:
        db.execute("DELETE FROM sends WHERE lead_id=?", (lid,))
        db.execute("DELETE FROM replies WHERE lead_id=?", (lid,))
    db.execute("DELETE FROM enrollments WHERE lead_id IN (SELECT id FROM leads WHERE source='demo')")
    db.execute("DELETE FROM leads WHERE source='demo'")
    for sid in seq_ids:
        db.execute("DELETE FROM sequences WHERE id=?", (sid,))
    db.event("demo", "removed demo data")
    return {"ok": True, "leads_removed": len(lead_ids), "sequences_removed": len(seq_ids)}
