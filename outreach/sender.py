"""STAGE 7: the sending layer.

Three modes:
  dry_run  (default) nothing is really sent; each email is saved as a .eml file in data/outbox
  test     really sent through Gmail, but every email goes to YOUR inbox (TEST_RECIPIENT)
  live     really sent to the creator; only works with --confirm-live

Instagram DMs are never sent automatically. Meta's API does not allow messaging
people who have not messaged you first, so DMs go into a list for a human to send.
"""
import logging
import os
import smtplib
import ssl
import time
from email.message import EmailMessage

from . import storage
from .utils import NOT_FOUND, is_valid_email

log = logging.getLogger("sender")


def build_email(subject, body, sender_name, from_address, to_address):
    """Create the email object (subject, from, to, text)."""
    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = sender_name + " <" + from_address + ">"
    email["To"] = to_address
    email.set_content(body)
    return email


def connect_to_mail_server():
    """Log in to the SMTP server (Gmail by default) using the details in .env."""
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    if not user or not password:
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD missing in .env (needed for test and live modes).")
    server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
    server.login(user, password)
    return server


def send_emails(cfg, conn, mode=None, confirm_live=False, limit=None):
    """Send (or simulate) one email per shortlisted creator. Returns a count of what happened."""
    mode = mode or cfg["sending"]["mode"]

    # --- Safety checks before doing anything -------------------------------
    if mode not in ("dry_run", "test", "live"):
        raise ValueError("Unknown sending mode: " + mode)
    if mode == "live" and not confirm_live:
        raise RuntimeError("Live mode emails real people. Add --confirm-live once you have reviewed the messages.")
    test_inbox = os.getenv("TEST_RECIPIENT")
    if mode == "test" and not is_valid_email(test_inbox):
        raise RuntimeError("TEST_RECIPIENT missing in .env (test mode sends every email there).")

    from_address = os.getenv("SMTP_USER") or "outreach@localhost"
    daily_cap = cfg["sending"]["daily_cap"]
    if limit:
        daily_cap = min(limit, daily_cap)

    results = {"sent": 0, "simulated": 0, "failed": 0,
               "skipped_no_email": 0, "skipped_duplicate": 0, "skipped_unapproved": 0}

    # --- Who already got an email in this mode? (duplicate protection) ------
    done_channels = set()
    done_addresses = set()
    for row in storage.fetch(conn, "SELECT channel_id, recipient FROM outreach_log WHERE channel_type = 'email' "
                                   "AND mode = ? AND status IN ('simulated', 'sent')", (mode,)):
        done_channels.add(row["channel_id"])
        done_addresses.add(row["recipient"])

    # --- Shortlisted creators with their message, best fit first ------------
    creators = storage.fetch(conn,
        "SELECT i.channel_id, i.name, i.email, m.email_subject, m.email_body, m.approved "
        "FROM influencers i JOIN messages m ON m.channel_id = i.channel_id "
        "WHERE i.filter_status = 'passed' ORDER BY i.fit_score DESC")

    server = None
    for creator in creators:
        if results["sent"] + results["simulated"] >= daily_cap:
            log.info("daily cap of %d reached", daily_cap)
            break
        if not is_valid_email(creator["email"]):
            results["skipped_no_email"] += 1
            continue
        if not creator["approved"]:
            results["skipped_unapproved"] += 1
            continue
        if creator["channel_id"] in done_channels or creator["email"] in done_addresses:
            results["skipped_duplicate"] += 1
            continue

        to_address = test_inbox if mode == "test" else creator["email"]
        email = build_email(creator["email_subject"], creator["email_body"], cfg["brand"]["sender_name"], from_address, to_address)

        try:
            if mode == "dry_run":
                file_path = cfg["paths"]["outbox"] / (creator["channel_id"] + ".eml")
                file_path.write_bytes(bytes(email))
                status, detail = "simulated", "written to " + file_path.name
            else:
                if server is None:
                    server = connect_to_mail_server()
                server.send_message(email)
                status = "sent"
                detail = "redirected to test inbox " + to_address if mode == "test" else "delivered to SMTP server"
                time.sleep(cfg["sending"]["delay_seconds"])
        except Exception as error:
            storage.add_log(conn, creator["channel_id"], "email", creator["email"], mode, "failed", str(error)[:300])
            results["failed"] += 1
            log.error("send failed for %s: %s", creator["name"], error)
            server = None
            continue

        # Write the log line. The database refuses it if it would be a duplicate.
        if storage.add_log(conn, creator["channel_id"], "email", creator["email"], mode, status, detail):
            results[status] += 1
            done_addresses.add(creator["email"])
        else:
            results["skipped_duplicate"] += 1

    if server is not None:
        server.quit()
    log.info("send (%s): %s", mode, results)
    return results


def queue_dms(cfg, conn):
    """Create the manual Instagram DM list: one "pending" entry per approved creator."""
    creators = storage.fetch(conn,
        "SELECT i.channel_id, i.instagram FROM influencers i JOIN messages m ON m.channel_id = i.channel_id "
        "WHERE i.filter_status = 'passed' AND m.approved = 1")
    queued = 0
    for creator in creators:
        added = storage.add_log(conn, creator["channel_id"], "instagram_dm", creator["instagram"] or NOT_FOUND,
                                "manual", "manual_pending",
                                "send by hand from the brand account, then run: python main.py dm-sent <channel_id>")
        if added:
            queued += 1
    log.info("dm queue: %d new manual tasks", queued)
    return queued


def mark_dm_sent(conn, channel_id):
    """A person sent the DM by hand; record that. Returns False if no pending DM was found."""
    cursor = conn.execute(
        "UPDATE outreach_log SET status = 'manual_sent', detail = 'marked as sent by operator' "
        "WHERE channel_id = ? AND channel_type = 'instagram_dm' AND status = 'manual_pending'", (channel_id,))
    conn.commit()
    return cursor.rowcount > 0
