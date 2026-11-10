"""Run the pipeline from the command line.

    python main.py run                 discover -> metrics -> filter -> enrich -> personalise -> export
    python main.py review              list messages (--approve-all, --approve ID, --reject ID)
    python main.py send                dry run: saves .eml files, updates the tracker, blocks duplicates
    python main.py send --mode test    real send, but to your own TEST_RECIPIENT inbox
    python main.py status              counts for every stage

Single stages: discover, metrics, filter, enrich, personalize, export, queue-dms, dm-sent
"""
import argparse
import os
import sys

from outreach import discovery, enrichment, export, filtering, personalize, sender, storage
from outreach.config import load_config
from outreach.utils import setup_logging
from outreach.youtube import YouTubeError


def run_pipeline(cfg, conn, args):
    api_key = os.getenv("YOUTUBE_API_KEY")
    discovery.discover(cfg, conn, api_key)
    discovery.collect_metrics(cfg, conn, api_key)
    filtering.run_filter(cfg, conn)
    enrichment.run_enrichment(cfg, conn, visit_websites=not args.no_scrape)
    personalize.run_personalization(cfg, conn, regenerate=args.regenerate)
    sender.queue_dms(cfg, conn)
    export.export_all(cfg, conn)

    print("\n" + export.summary(conn))
    total = storage.count(conn, "SELECT COUNT(*) FROM influencers")
    if total < 50:
        print("\nOnly " + str(total) + " creators found. Add search queries or raise max_pages_per_query in config.yaml.")
    print("\nNext: read data/messages.md, then run: python main.py send")


def review(conn, args):
    if args.approve_all:
        conn.execute("UPDATE messages SET approved = 1")
    for channel_id in args.approve or []:
        conn.execute("UPDATE messages SET approved = 1 WHERE channel_id = ?", (channel_id,))
    for channel_id in args.reject or []:
        conn.execute("UPDATE messages SET approved = 0 WHERE channel_id = ?", (channel_id,))
    conn.commit()

    rows = storage.fetch(conn, "SELECT i.name, m.channel_id, m.generated_by, m.approved FROM messages m "
                               "JOIN influencers i ON i.channel_id = m.channel_id ORDER BY m.approved, i.name")
    approved = 0
    for row in rows:
        label = "APPROVED" if row["approved"] else "PENDING "
        approved += row["approved"]
        print(" ", label, row["channel_id"], row["name"][:40], "-", row["generated_by"])
    print("\n" + str(approved) + " of " + str(len(rows)) + " approved. Read the texts in data/messages.md")


def main():
    parser = argparse.ArgumentParser(description="Micro-influencer discovery and outreach pipeline")
    parser.add_argument("command", choices=["run", "discover", "metrics", "filter", "enrich", "personalize", "review",
                                            "send", "queue-dms", "dm-sent", "export", "status"])
    parser.add_argument("channel_id", nargs="?", help="only for dm-sent")
    parser.add_argument("--data-dir", help="folder for the database and exports (default: data)")
    parser.add_argument("--no-scrape", action="store_true", help="do not visit creators' websites for emails")
    parser.add_argument("--regenerate", action="store_true", help="rewrite messages that already exist")
    parser.add_argument("--approve", nargs="*", metavar="CHANNEL_ID")
    parser.add_argument("--reject", nargs="*", metavar="CHANNEL_ID")
    parser.add_argument("--approve-all", action="store_true")
    parser.add_argument("--mode", choices=["dry_run", "test", "live"])
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--limit", type=int, help="send at most this many emails")
    args = parser.parse_args()

    setup_logging()
    try:
        cfg = load_config(args.data_dir)
    except (FileNotFoundError, ValueError) as error:
        print("Configuration error:", error)
        return 2
    conn = storage.connect(cfg["paths"]["db"])
    api_key = os.getenv("YOUTUBE_API_KEY")
    command = args.command

    try:
        if command == "run":
            run_pipeline(cfg, conn, args)
        elif command == "discover":
            discovery.discover(cfg, conn, api_key)
        elif command == "metrics":
            discovery.collect_metrics(cfg, conn, api_key)
        elif command == "filter":
            filtering.run_filter(cfg, conn)
        elif command == "enrich":
            enrichment.run_enrichment(cfg, conn, visit_websites=not args.no_scrape)
        elif command == "personalize":
            personalize.run_personalization(cfg, conn, regenerate=args.regenerate)
        elif command == "review":
            review(conn, args)
        elif command == "send":
            print(sender.send_emails(cfg, conn, args.mode, args.confirm_live, args.limit))
            sender.queue_dms(cfg, conn)
        elif command == "queue-dms":
            sender.queue_dms(cfg, conn)
        elif command == "dm-sent":
            found = sender.mark_dm_sent(conn, args.channel_id)
            print("marked as sent" if found else "no pending DM found for that channel id")
        elif command == "status":
            print(export.summary(conn))

        if command in ("review", "send", "queue-dms", "dm-sent", "export"):
            for path in export.export_all(cfg, conn):
                if command == "export":
                    print(" ", path)
    except (YouTubeError, RuntimeError, ValueError) as error:
        print("\nStopped:", error)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
