"""Mailbox jobs: prove every sender can sign in, and send one real message from it."""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .. import db, mail, mailboxes
from .base import Job


def run_test_all(ctx):
    domains = str(ctx.param("domains") or "")
    boxes = mailboxes.select_boxes(domains, only_password=bool(ctx.param("only_password", True)))
    if not boxes:
        return ctx.summary(ok=True, note="No mailbox matched - import the senders CSV first.")
    workers = max(1, min(16, ctx.int_param("workers", 6)))
    deactivate = bool(ctx.param("deactivate_failed", True))
    ctx.log("%d mailbox(es) to test%s, %d at a time" % (
        len(boxes), (" for " + domains) if domains else "", workers))
    smtp_ok = imap_ok = paused = index = 0
    pending = list(boxes)
    while pending:
        ctx.check_cancel()
        batch = pending[:workers * 2]
        pending = pending[workers * 2:]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(mail.probe_box, box): box for box in batch}
            for future in as_completed(futures):
                box = futures[future]
                index += 1
                try:
                    result = future.result()
                except Exception as exc:                # noqa: BLE001 - one box never stops the run
                    result = {"smtp": "failed", "imap": "skipped", "smtp_detail": str(exc),
                              "imap_detail": "", "unread": None, "ok": False}
                mailboxes.store_probe(box["id"], result)
                smtp_ok += 1 if result["smtp"] == "ok" else 0
                imap_ok += 1 if result["imap"] == "ok" else 0
                failed_smtp = result["smtp"] != "ok"
                if failed_smtp and deactivate and int(box.get("active") or 0):
                    db.update("mailboxes", box["id"], {"active": 0})
                    paused += 1
                detail = result["smtp_detail"] or result["imap_detail"] or ""
                ctx.log("%d/%d  %s  SMTP %s | IMAP %s%s%s" % (
                    index, len(boxes), box["from_email"], result["smtp"], result["imap"],
                    "  paused" if failed_smtp and deactivate else "",
                    ("  (" + detail[:170] + ")") if detail else ""))
                ctx.progress(index, len(boxes), "testing mailboxes")
    ctx.log("%d SMTP ok, %d IMAP ok, %d failed%s" % (
        smtp_ok, imap_ok, len(boxes) - smtp_ok, (", %d paused" % paused) if paused else ""))
    return ctx.summary(ok=True, mailboxes=len(boxes), smtp_ok=smtp_ok, imap_ok=imap_ok,
                        failed=len(boxes) - smtp_ok, paused=paused)


def run_send_test(ctx):
    domains = str(ctx.param("domains") or "")
    to = str(ctx.param("to") or "").strip()
    limit = ctx.int_param("limit", 0)
    gap = float(ctx.param("gap", 4) or 4)
    boxes = [b for b in mailboxes.select_boxes(domains, only_password=True)
             if int(b.get("active") or 0)]
    if limit:
        boxes = boxes[:limit]
    if not boxes:
        return ctx.summary(ok=True, note="No active mailbox with a password matched.")
    ctx.log("%d mailbox(es) will send one real message to %s" % (
        len(boxes), to or "their own address"))
    sent = failed = 0
    for index, box in enumerate(boxes, start=1):
        ctx.check_cancel()
        target = to or box["from_email"]
        subject = "Lead Studio test - %s" % box["from_email"]
        body = ("This is a one-line test from Lead Studio.\n\n"
                "Mailbox: %s\nServer: %s:%s (%s)\n\n"
                "If this arrived, the mailbox can send." % (
                    box["from_email"], box.get("smtp_host"), box.get("smtp_port"),
                    box.get("smtp_security")))
        ok, detail = mail.smtp_send(box, target, subject, body, mail.plain_to_html(body))
        sent += 1 if ok else 0
        failed += 0 if ok else 1
        ctx.log("%d/%d  %s -> %s  %s%s" % (
            index, len(boxes), box["from_email"], target,
            "sent" if ok else "FAILED", ("" if ok else "  (" + str(detail)[:170] + ")")))
        ctx.progress(index, len(boxes), "sending test mail")
        if index < len(boxes):
            time.sleep(gap)
    ctx.log("%d sent, %d failed" % (sent, failed))
    return ctx.summary(ok=True, mailboxes=len(boxes), sent=sent, failed=failed)


JOBS = [
    Job(key="mailboxes_test_all",
        name="Mailboxes: test every sender login",
        group="Mailboxes",
        description="One SMTP sign-in and one IMAP sign-in per mailbox, with the result "
                    "stored on the mailbox card. Blank = every mailbox that has a saved "
                    "password; put a domain (efficientads.eu) to test just those.",
        run=run_test_all,
        schedule="",
        needs_network=True,
        params={"domains": "", "only_password": True, "workers": 6,
                "deactivate_failed": True}),
    Job(key="mailboxes_send_test",
        name="Mailboxes: send one real test from each",
        group="Mailboxes",
        description="Sends a short real message from every active mailbox that has a "
                    "password, spaced out so the provider does not see a burst. Blank "
                    "'to' sends each mailbox a message to itself.",
        run=run_send_test,
        schedule="",
        needs_network=True,
        params={"domains": "", "to": "", "limit": 0, "gap": 4}),
]
