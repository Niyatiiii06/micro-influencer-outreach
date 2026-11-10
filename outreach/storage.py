"""Saves and loads data using SQLite (a database stored in one file: data/outreach.db).

Three tables:
  influencers   one row per YouTube channel, filled in step by step by each stage
  messages      the email + DM written for each shortlisted channel
  outreach_log  one row per send attempt (this is what prevents duplicates)
"""
import json
import sqlite3

from .utils import now_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS influencers (
    channel_id        TEXT PRIMARY KEY,
    name              TEXT,
    handle            TEXT,
    platform          TEXT,
    profile_url       TEXT,
    description       TEXT,
    country           TEXT,
    followers         INTEGER,
    total_views       INTEGER,
    video_count       INTEGER,
    topics            TEXT,
    channel_keywords  TEXT,
    uploads_playlist  TEXT,
    made_for_kids     INTEGER DEFAULT 0,
    discovered_via    TEXT,
    discovered_at     TEXT,

    recent_videos     TEXT,      -- filled by the metrics stage
    last_upload_at    TEXT,
    avg_views         INTEGER,
    engagement_rate   REAL,
    metrics_at        TEXT,

    content_relevance REAL,      -- filled by the filter stage
    sub_niches        TEXT,
    tier              TEXT,
    fit_score         REAL,
    filter_status     TEXT DEFAULT 'pending',
    filter_reasons    TEXT,

    email             TEXT,      -- filled by the enrichment stage
    email_source      TEXT,
    website           TEXT,
    instagram         TEXT,
    tiktok            TEXT,
    content_themes    TEXT,
    content_tone      TEXT,
    audience_age      TEXT,
    audience_gender   TEXT,
    audience_geo      TEXT,
    enriched_at       TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    channel_id    TEXT PRIMARY KEY,
    email_subject TEXT,
    email_body    TEXT,
    instagram_dm  TEXT,
    collab_angle  TEXT,
    signals       TEXT,
    generated_by  TEXT,
    model         TEXT,
    email_words   INTEGER,
    dm_words      INTEGER,
    needs_review  INTEGER DEFAULT 0,
    approved      INTEGER DEFAULT 0,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS outreach_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id   TEXT,
    channel_type TEXT,      -- email or instagram_dm
    recipient    TEXT,
    mode         TEXT,      -- dry_run, test, live or manual
    status       TEXT,      -- simulated, sent, failed, manual_pending, manual_sent
    detail       TEXT,
    created_at   TEXT
);

-- Duplicate protection inside the database itself: only ONE successful row is
-- allowed per creator + channel type + mode. A second one is rejected.
CREATE UNIQUE INDEX IF NOT EXISTS one_success_per_creator
    ON outreach_log(channel_id, channel_type, mode)
    WHERE status IN ('simulated', 'sent', 'manual_pending', 'manual_sent');
"""

JSON_COLUMNS = ["topics", "recent_videos", "sub_niches", "filter_reasons", "content_themes", "signals"]


def connect(db_path):
    """Open the database file (created automatically the first time)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _to_text(fields):
    """Copy of the fields with list/dict columns turned into JSON text."""
    result = dict(fields)
    for column in JSON_COLUMNS:
        if result.get(column) is not None:
            result[column] = json.dumps(result[column], ensure_ascii=False)
    return result


def fetch(conn, sql, params=()):
    """Run a SELECT and return the rows as a list of normal dictionaries."""
    rows = []
    for row in conn.execute(sql, params).fetchall():
        record = dict(row)
        for column in JSON_COLUMNS:
            if record.get(column):
                record[column] = json.loads(record[column])
        rows.append(record)
    return rows


def count(conn, sql, params=()):
    """Run a SELECT COUNT(*) query and return the number."""
    return conn.execute(sql, params).fetchone()[0]


def _insert(conn, table, fields, command="INSERT"):
    fields = _to_text(fields)
    columns = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    conn.execute(command + " INTO " + table + " (" + columns + ") VALUES (" + placeholders + ")", list(fields.values()))
    conn.commit()


def add_influencer(conn, record):
    """Save a newly discovered channel. If it is already saved, do nothing."""
    _insert(conn, "influencers", record, "INSERT OR IGNORE")


def update_influencer(conn, channel_id, fields):
    """Change some columns of one channel, e.g. {"email": "a@b.com"}."""
    fields = _to_text(fields)
    assignments = ", ".join(column + " = ?" for column in fields)
    conn.execute("UPDATE influencers SET " + assignments + " WHERE channel_id = ?", list(fields.values()) + [channel_id])
    conn.commit()


def save_message(conn, message):
    """Save the generated email + DM (replaces an older version for the same channel)."""
    _insert(conn, "messages", message, "INSERT OR REPLACE")


def add_log(conn, channel_id, channel_type, recipient, mode, status, detail):
    """Add one line to the outreach log.

    Returns False if the database rejected it as a duplicate success.
    """
    row = {"channel_id": channel_id, "channel_type": channel_type, "recipient": recipient,
           "mode": mode, "status": status, "detail": detail}
    row["created_at"] = now_text()
    try:
        _insert(conn, "outreach_log", row)
        return True
    except sqlite3.IntegrityError:
        return False
