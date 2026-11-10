"""STAGE 3: decide who passes, and record the reason for every check."""
import logging

from . import storage
from .config import all_keywords
from .utils import days_since, find_keyword

log = logging.getLogger("filtering")


def video_text(video):
    """Title + tags of one video, as one piece of text to search in."""
    return video.get("title", "") + " " + " ".join(video.get("tags") or [])


def content_relevance(channel, cfg):
    """What percent of the recent videos are about our niche? None if there are no videos."""
    videos = channel.get("recent_videos") or []
    if not videos:
        return None
    keywords = all_keywords(cfg)
    matching = 0
    for video in videos:
        if find_keyword(video_text(video), keywords):
            matching += 1
    return round(100 * matching / len(videos), 1)


def find_sub_niches(channel, cfg):
    """Classify the channel: which sub-niches (yoga, strength...) does it cover most?"""
    videos = channel.get("recent_videos") or []
    profile = (channel.get("description") or "") + " " + (channel.get("channel_keywords") or "")
    scores = {}
    for sub_niche, keywords in cfg["niche"]["keywords"].items():
        score = 0
        for video in videos:
            if find_keyword(video_text(video), keywords):
                score += 1
        if find_keyword(profile, keywords):
            score += 2
        if score >= 2:
            scores[sub_niche] = score
    ranked = sorted(scores, key=lambda name: scores[name], reverse=True)
    return ranked[:3]


def evaluate(channel, cfg):
    """Run all checks on one channel. Returns the fields to save for it."""
    filters = cfg["filters"]
    reasons = []
    failed_any = False

    def record(check_name, passed, detail):
        reasons.append(("PASS " if passed else "FAIL ") + check_name + ": " + detail)
        return passed

    if not record("platform", channel["platform"] in filters["allowed_platforms"], str(channel["platform"])):
        failed_any = True

    followers = channel.get("followers")
    low, high = filters["min_followers"], filters["max_followers"]
    in_range = followers is not None and low <= followers <= high
    if followers is None:
        record("followers", False, "subscriber count is hidden by the creator")
    else:
        word = "within" if in_range else "outside"
        record("followers", in_range, f"{followers:,} {word} {low:,}-{high:,}")
    if not in_range:
        failed_any = True

    relevance = content_relevance(channel, cfg)

    if not channel.get("metrics_at"):
        # We never loaded videos for this channel (it was outside the follower range)
        reasons.append("SKIP activity/engagement/relevance: not evaluated (outside follower range)")
    else:
        age = days_since(channel.get("last_upload_at"))
        limit = filters["max_days_since_upload"]
        if age is None:
            ok = record("activity", False, "no public uploads found")
        else:
            ok = record("activity", age <= limit, f"last upload {age} days ago (limit {limit})")
        if not ok:
            failed_any = True

        video_count = channel.get("video_count") or 0
        if not record("video count", video_count >= filters["min_video_count"],
                      f"{video_count} videos (minimum {filters['min_video_count']})"):
            failed_any = True

        rate = channel.get("engagement_rate")
        minimum = filters["min_engagement_rate"]
        if rate is None:
            ok = record("engagement", False, "not available (likes hidden or no views)")
        else:
            ok = record("engagement", rate >= minimum, f"{rate}% (minimum {minimum}%)")
        if not ok:
            failed_any = True

        minimum = filters["min_content_relevance"]
        if relevance is None:
            ok = record("content relevance", False, "no recent videos to analyse")
        else:
            ok = record("content relevance", relevance >= minimum,
                        f"{relevance}% of recent videos match {cfg['niche']['name']} keywords (minimum {minimum}%)")
        if not ok:
            failed_any = True

    everything = channel.get("description") or ""
    for video in channel.get("recent_videos") or []:
        everything += " " + video_text(video)
    blocked = find_keyword(everything, cfg["niche"]["negative_keywords"])
    if blocked:
        ok = record("brand fit", False, "blocked keyword '" + blocked + "'")
    elif channel.get("made_for_kids"):
        ok = record("brand fit", False, "channel is made for kids")
    else:
        ok = record("brand fit", True, "no blocked keywords")
    if not ok:
        failed_any = True

    allowed = filters["allowed_countries"]
    country = channel.get("country")
    if not allowed:
        ok = record("geography", True, (country or "unknown") + " (no restriction configured)")
    elif not country:
        ok = record("geography", filters["allow_unknown_country"], "country not published by creator")
    else:
        ok = record("geography", country in allowed, country + (" is allowed" if country in allowed else " is not allowed"))
    if not ok:
        failed_any = True

    tier = None
    if in_range:
        tier = "micro (emerging, 5k-25k)" if followers < 25000 else "micro (established, 25k-100k)"

    fit_score = None
    if not failed_any:
        engagement_points = 40 * min(channel["engagement_rate"] / 6, 1)
        relevance_points = 40 * relevance / 100
        age = days_since(channel.get("last_upload_at")) or 0
        recency_points = 20 * max(0, 1 - age / filters["max_days_since_upload"])
        fit_score = round(engagement_points + relevance_points + recency_points, 1)

    return {
        "filter_status": "failed" if failed_any else "passed",
        "filter_reasons": reasons,
        "content_relevance": relevance,
        "sub_niches": find_sub_niches(channel, cfg),
        "tier": tier,
        "fit_score": fit_score,
    }


def run_filter(cfg, conn):
    """Evaluate every saved channel and store the result."""
    passed = 0
    failed = 0
    for channel in storage.fetch(conn, "SELECT * FROM influencers"):
        result = evaluate(channel, cfg)
        storage.update_influencer(conn, channel["channel_id"], result)
        if result["filter_status"] == "passed":
            passed += 1
        else:
            failed += 1
    log.info("filter done: %d passed, %d failed", passed, failed)
    return passed, failed
