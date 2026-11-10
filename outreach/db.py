"""SQLite storage. One file, three tables, safe to re-run any stage."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS influencers (
    channel_id      TEXT PRIMARY KEY,
    name            TEXT,
    handle          TEXT,
    platform        TEXT,
    profile_url     TEXT,
    description     TEXT,
    country         TEXT,
    followers       INTEGER,
    hidden_followers INTEGER DEFAULT 0,
    total_views     INTEGER,
    video_count     INTEGER,
    channel_created TEXT,
    topics          TEXT,
    channel_keywords TEXT,
    uploads_playlist TEXT,
    made_for_kids   INTEGER DEFAULT 0,
    discovered_via  TEXT,
    discovered_at   TEXT,
    -- metrics
    recent_videos   TEXT,
    last_upload_at  TEXT,
    avg_views       INTEGER,
    engagement_rate REAL,
    metrics_at      TEXT,
    -- filtering
    content_relevance REAL,
    sub_niches      TEXT,
    tier            TEXT,
    fit_score       REAL,
    filter_status   TEXT DEFAULT 'pending',
    filter_reasons  TEXT,
    -- enrichment
    email           TEXT,
    email_source    TEXT,
    website         TEXT,
    instagram       TEXT,
    tiktok          TEXT,
    content_themes  TEXT,
    content_tone    TEXT,
    audience_age    TEXT,
    audience_gender TEXT,
    audience_geo    TEXT,
    enriched_at     TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    channel_id    TEXT PRIMARY KEY REFERENCES influencers(channel_id),
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
    channel_id   TEXT REFERENCES influencers(channel_id),
    channel_type TEXT,      -- email | instagram_dm
    recipient    TEXT,
    mode         TEXT,      -- dry_run | test | live | manual
    status       TEXT,      -- simulated | sent | failed | skipped | manual_pending | manual_sent
    detail       TEXT,
    created_at   TEXT
);

-- Database-level duplicate protection: one successful delivery per creator,
-- per channel type, per mode. A second insert raises IntegrityError.
CREATE UNIQUE INDEX IF NOT EXISTS uq_outreach_success
    ON outreach_log(channel_id, channel_type, mode)
    WHERE status IN ('simulated', 'sent', 'manual_pending', 'manual_sent');
"""

JSON_COLS = {"topics", "recent_videos", "sub_niches", "filter_reasons", "content_themes", "signals"}


class DB:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @staticmethod
    def _encode(row: dict) -> dict:
        return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_COLS and v is not None else v) for k, v in row.items()}

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        out = dict(row)
        for k in JSON_COLS & out.keys():
            if out[k]:
                try:
                    out[k] = json.loads(out[k])
                except json.JSONDecodeError:
                    pass
        return out

    def upsert(self, table: str, key: str, row: dict) -> None:
        row = self._encode(row)
        cols = list(row)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != key)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            f"ON CONFLICT({key}) DO UPDATE SET {updates}"
        )
        self.conn.execute(sql, [row[c] for c in cols])
        self.conn.commit()

    def update(self, table: str, key: str, key_value, fields: dict) -> None:
        fields = self._encode(fields)
        sql = f"UPDATE {table} SET {', '.join(f'{c}=?' for c in fields)} WHERE {key}=?"
        self.conn.execute(sql, [*fields.values(), key_value])
        self.conn.commit()

    def insert(self, table: str, row: dict) -> None:
        cols = list(row)
        self.conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )
        self.conn.commit()

    def rows(self, sql: str, params: tuple | list = ()) -> list[dict]:
        return [self._decode(r) for r in self.conn.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: tuple | list = ()):
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.conn.close()
