"""Tests for the whole pipeline. Run with: python -m pytest -q

The channels below are FAKE test data served by fake YouTube functions.
They only prove the logic works and are never written to the real data folder.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from outreach import discovery, enrichment, export, filtering, llm, personalize, sender, storage, youtube
from outreach.config import load_config
from outreach.utils import NOT_FOUND, count_words, find_keyword, is_valid_email


def days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


# One fake channel per situation we want to test
FAKE_CHANNELS = {
    "UC_pass_email": {"subs": 18000, "likes": 60, "last_upload": 3, "country": "IN",
                      "about": "Home workouts & yoga. Business: coach.test@fixture-mail.test https://instagram.com/coach.fixture",
                      "titles": ["20 min home workout no equipment", "Yoga for flexibility beginners", "Full body workout at home"]},
    "UC_pass_noemail": {"subs": 45000, "likes": 40, "last_upload": 5, "country": None,
                        "about": "Strength training and gym tips every week.",
                        "titles": ["How to squat properly", "Deadlift mistakes to avoid", "Gym workout for muscle"]},
    "UC_too_big": {"subs": 900000, "likes": 50, "last_upload": 2, "country": "US", "about": "Fitness channel", "titles": ["Workout"]},
    "UC_too_small": {"subs": 800, "likes": 50, "last_upload": 2, "country": "IN", "about": "Fitness channel", "titles": ["Workout"]},
    "UC_hidden": {"subs": None, "likes": 50, "last_upload": 2, "country": "IN", "about": "Fitness workouts", "titles": ["Home workout"]},
    "UC_low_er": {"subs": 30000, "likes": 2, "last_upload": 4, "country": "IN", "about": "Gym workout and fitness",
                  "titles": ["Gym workout day 1", "Gym workout day 2", "Strength workout"]},
    "UC_off_niche": {"subs": 25000, "likes": 50, "last_upload": 4, "country": "IN", "about": "Tech reviews and gadgets",
                     "titles": ["iPhone review", "Best laptops 2026", "Android tips"]},
    "UC_inactive": {"subs": 22000, "likes": 50, "last_upload": 300, "country": "IN", "about": "Yoga and fitness",
                    "titles": ["Yoga flow", "Morning yoga", "Yoga stretching"]},
    "UC_unsafe": {"subs": 40000, "likes": 50, "last_upload": 3, "country": "IN", "about": "Bodybuilding and steroids cycle advice",
                  "titles": ["Gym workout", "Muscle workout", "Strength workout"]},
}


def fake_search_videos(api_key, query, published_after, region, language, page_token):
    return {"items": [{"snippet": {"channelId": channel_id}} for channel_id in FAKE_CHANNELS]}


def fake_get_channels(api_key, channel_ids):
    items = []
    for channel_id in channel_ids:
        fake = FAKE_CHANNELS[channel_id]
        items.append({
            "id": channel_id,
            "snippet": {"title": channel_id.replace("UC_", "").replace("_", " ").title(), "description": fake["about"],
                        "customUrl": "@" + channel_id.lower(), "country": fake["country"]},
            "statistics": {"subscriberCount": str(fake["subs"]), "hiddenSubscriberCount": fake["subs"] is None,
                           "viewCount": "100000", "videoCount": "120"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU" + channel_id}},
            "status": {"madeForKids": False},
        })
    return items


def fake_get_recent_video_ids(api_key, uploads_playlist, limit):
    channel_id = uploads_playlist[2:]
    return [channel_id + "_v" + str(i) for i in range(len(FAKE_CHANNELS[channel_id]["titles"]))]


def fake_get_videos(api_key, video_ids):
    items = []
    for video_id in video_ids:
        channel_id, index = video_id.rsplit("_v", 1)
        fake = FAKE_CHANNELS[channel_id]
        items.append({"id": video_id,
                      "snippet": {"title": fake["titles"][int(index)], "publishedAt": days_ago(fake["last_upload"] + int(index) * 7)},
                      "statistics": {"viewCount": "1000", "likeCount": str(fake["likes"]), "commentCount": "5"}})
    return items


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    """Fresh empty database in a temporary folder, fake YouTube, no API keys."""
    for name in ["GEMINI_API_KEY", "GROQ_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "TEST_RECIPIENT"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(youtube, "search_videos", fake_search_videos)
    monkeypatch.setattr(youtube, "get_channels", fake_get_channels)
    monkeypatch.setattr(youtube, "get_recent_video_ids", fake_get_recent_video_ids)
    monkeypatch.setattr(youtube, "get_videos", fake_get_videos)
    cfg = load_config(tmp_path)
    cfg["llm"]["provider"] = "none"
    conn = storage.connect(cfg["paths"]["db"])
    yield cfg, conn
    conn.close()


def run_all(cfg, conn):
    discovery.discover(cfg, conn, "fake-key")
    discovery.collect_metrics(cfg, conn, "fake-key")
    filtering.run_filter(cfg, conn)
    enrichment.run_enrichment(cfg, conn, visit_websites=False)
    personalize.run_personalization(cfg, conn)


def get_channel(conn, channel_id):
    return storage.fetch(conn, "SELECT * FROM influencers WHERE channel_id = ?", (channel_id,))[0]


def test_filter_passes_and_fails_the_right_channels(setup):
    cfg, conn = setup
    run_all(cfg, conn)
    assert get_channel(conn, "UC_pass_email")["filter_status"] == "passed"
    assert get_channel(conn, "UC_pass_noemail")["filter_status"] == "passed"

    expected_failures = {"UC_too_big": "followers", "UC_too_small": "followers", "UC_hidden": "followers",
                         "UC_low_er": "engagement", "UC_off_niche": "content relevance",
                         "UC_inactive": "activity", "UC_unsafe": "brand fit"}
    for channel_id, check in expected_failures.items():
        channel = get_channel(conn, channel_id)
        assert channel["filter_status"] == "failed", channel_id
        assert any(reason.startswith("FAIL " + check) for reason in channel["filter_reasons"]), channel["filter_reasons"]


def test_email_is_found_or_marked_not_found(setup):
    cfg, conn = setup
    run_all(cfg, conn)
    with_email = get_channel(conn, "UC_pass_email")
    assert with_email["email"] == "coach.test@fixture-mail.test"
    assert with_email["email_source"] == "channel description"
    assert with_email["instagram"] == "https://www.instagram.com/coach.fixture"
    assert get_channel(conn, "UC_pass_noemail")["email"] == NOT_FOUND


def test_engagement_rate_formula():
    videos = [{"views": 1000, "likes": 40, "comments": 10},   # 5%
              {"views": 2000, "likes": 20, "comments": 0},    # 1%
              {"views": 500, "likes": None, "comments": 3},   # likes hidden: skipped
              {"views": 0, "likes": 5, "comments": 0}]        # no views: skipped
    assert discovery.engagement_rate(videos) == (3.0, 1167)
    assert discovery.engagement_rate([{"views": 100, "likes": None}]) == (None, 100)
    assert discovery.engagement_rate([]) == (None, None)


def test_offline_messages_respect_word_limits(setup):
    cfg, conn = setup
    run_all(cfg, conn)
    messages = storage.fetch(conn, "SELECT * FROM messages")
    assert len(messages) == 2
    for message in messages:
        assert 60 <= count_words(message["email_body"]) <= 90, message["email_body"]
        assert 15 <= count_words(message["instagram_dm"]) <= 30, message["instagram_dm"]
        assert message["generated_by"] == "offline_rule_based"
        assert message["needs_review"] == 1 and message["approved"] == 0
    assert messages[0]["email_body"] != messages[1]["email_body"]


def test_bad_llm_answer_is_rejected_and_retried(setup, monkeypatch):
    cfg, conn = setup
    calls = []

    def fake_ask_llm(cfg, system, user):
        calls.append(user)
        if len(calls) == 1:
            return {"email_subject": "Hi", "email_body": "Too short.", "instagram_dm": "Hi"}, "fake-model"
        assert "rejected" in user
        return {"email_subject": "Idea for Pass Email", "email_body": "Hi Pass Email, " + "word " * 65 + "Best, Me",
                "instagram_dm": "Hi Pass Email, " + "word " * 15}, "fake-model"

    monkeypatch.setattr(llm, "ask_llm", fake_ask_llm)
    signals = {"creator": {"name": "Pass Email"}}
    message, written_by, model, problems = personalize.write_messages(signals, cfg, use_llm=True)
    assert written_by == "llm" and model == "fake-model" and problems == [] and len(calls) == 2


def test_dry_run_logs_and_blocks_duplicates(setup):
    cfg, conn = setup
    run_all(cfg, conn)

    first = sender.send_emails(cfg, conn, "dry_run")
    assert first["simulated"] == 0 and first["skipped_unapproved"] == 1 and first["skipped_no_email"] == 1

    conn.execute("UPDATE messages SET approved = 1")
    conn.commit()
    second = sender.send_emails(cfg, conn, "dry_run")
    assert second["simulated"] == 1
    assert len(list(cfg["paths"]["outbox"].glob("*.eml"))) == 1

    third = sender.send_emails(cfg, conn, "dry_run")
    assert third["simulated"] == 0 and third["skipped_duplicate"] == 1
    assert storage.count(conn, "SELECT COUNT(*) FROM outreach_log WHERE status = 'simulated'") == 1

    assert sender.queue_dms(cfg, conn) == 2
    assert sender.queue_dms(cfg, conn) == 0
    assert sender.mark_dm_sent(conn, "UC_pass_email") is True


def test_live_and_test_modes_are_guarded(setup):
    cfg, conn = setup
    with pytest.raises(RuntimeError):
        sender.send_emails(cfg, conn, "live")
    with pytest.raises(RuntimeError):
        sender.send_emails(cfg, conn, "test")


def test_quota_error_does_not_crash(setup, monkeypatch):
    cfg, conn = setup

    def out_of_quota(*args):
        raise youtube.QuotaExceeded("quota used up")

    monkeypatch.setattr(youtube, "search_videos", out_of_quota)
    assert discovery.discover(cfg, conn, "fake-key") == 0


def test_running_twice_creates_no_duplicates_and_exports_work(setup):
    cfg, conn = setup
    run_all(cfg, conn)
    run_all(cfg, conn)
    assert storage.count(conn, "SELECT COUNT(*) FROM influencers") == len(FAKE_CHANNELS)
    files = export.export_all(cfg, conn)
    for path in files:
        assert path.exists() and path.stat().st_size > 0
    dataset = files[0].read_text(encoding="utf-8-sig")
    assert "subscriber count is hidden" in dataset and "Passed all checks" in dataset


def test_small_helpers():
    assert enrichment.find_emails("mail: Collab.Me@Brand-Mail.co.in. or logo@2x.png") == ["collab.me@brand-mail.co.in"]
    assert enrichment.find_website("https://instagram.com/x and https://mysite.example.org/about.") == "https://mysite.example.org/about"
    assert find_keyword("my GYM day", ["gym"]) == "gym"
    assert find_keyword("gymnastics", ["gym"]) is None
    assert is_valid_email("a@b.co") and not is_valid_email(NOT_FOUND) and not is_valid_email("a@b")
