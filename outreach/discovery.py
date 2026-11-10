"""STAGE 1 (discover channels) and STAGE 2 (collect their recent-video numbers)."""
import logging
from datetime import datetime, timedelta, timezone

from . import storage, youtube
from .utils import now_text, to_number

log = logging.getLogger("discovery")


# ---------------------------------------------------------------- STAGE 1
def discover(cfg, conn, api_key):
    """Search YouTube for each query and save the channels behind the videos.

    Why search videos and not channels? Channel search mostly returns huge,
    famous channels. Searching RECENT videos surfaces small, active creators.
    """
    settings = cfg["discovery"]

    known_ids = set()
    for row in storage.fetch(conn, "SELECT channel_id FROM influencers"):
        known_ids.add(row["channel_id"])

    start_date = datetime.now(timezone.utc) - timedelta(days=settings["published_within_days"])
    start_text = start_date.strftime("%Y-%m-%dT%H:%M:%SZ")

    new_channels = {}
    try:
        for query in cfg["niche"]["search_queries"]:
            page_token = None
            for _page in range(settings["max_pages_per_query"]):
                if len(known_ids) + len(new_channels) >= settings["target_candidates"]:
                    break
                data = youtube.search_videos(api_key, query, start_text, settings["region_code"],
                                             settings["relevance_language"], page_token)
                for item in data.get("items", []):
                    channel_id = item.get("snippet", {}).get("channelId")
                    if channel_id and channel_id not in known_ids and channel_id not in new_channels:
                        new_channels[channel_id] = "youtube search: " + query
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
            log.info("after query '%s': %d new channels", query, len(new_channels))
    except youtube.QuotaExceeded as error:
        log.warning("%s Keeping the %d channels found so far.", error, len(new_channels))

    saved = 0
    try:
        items = youtube.get_channels(api_key, list(new_channels.keys()))
    except youtube.YouTubeError as error:
        log.error("Could not load channel details: %s", error)
        items = []
    for item in items:
        record = read_channel(item, new_channels[item["id"]], cfg["niche"]["platform"])
        storage.add_influencer(conn, record)
        saved += 1

    log.info("discovery done: %d new channels saved (%d quota units used)", saved, youtube.usage["units"])
    return saved


def read_channel(item, discovered_via, platform):
    """Pick the fields we need out of YouTube's channel answer."""
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    branding = item.get("brandingSettings", {}).get("channel", {})
    channel_id = item["id"]
    handle = snippet.get("customUrl") or ""

    if handle.startswith("@"):
        profile_url = "https://www.youtube.com/" + handle
    else:
        profile_url = "https://www.youtube.com/channel/" + channel_id

    # never a made-up number.
    if stats.get("hiddenSubscriberCount"):
        followers = None
    else:
        followers = to_number(stats.get("subscriberCount"))

    topics = []
    for link in item.get("topicDetails", {}).get("topicCategories", []):
        topics.append(link.split("/")[-1].replace("_", " "))

    return {
        "channel_id": channel_id,
        "name": snippet.get("title", "").strip(),
        "handle": handle,
        "platform": platform,
        "profile_url": profile_url,
        "description": snippet.get("description", ""),
        "country": snippet.get("country"),
        "followers": followers,
        "total_views": to_number(stats.get("viewCount")),
        "video_count": to_number(stats.get("videoCount")),
        "topics": topics,
        "channel_keywords": branding.get("keywords", ""),
        "uploads_playlist": item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads"),
        "made_for_kids": 1 if item.get("status", {}).get("madeForKids") else 0,
        "discovered_via": discovered_via,
        "discovered_at": now_text(),
    }


# ---------------------------------------------------------------- STAGE 2
def collect_metrics(cfg, conn, api_key):
    """Load each channel's latest videos and work out its engagement rate.

    To save quota we only do this for channels inside the follower range;
    the others will fail the filter anyway.
    """
    filters = cfg["filters"]
    how_many = cfg["discovery"]["recent_videos"]
    channels = storage.fetch(
        conn,
        "SELECT channel_id, name, uploads_playlist FROM influencers "
        "WHERE metrics_at IS NULL AND followers BETWEEN ? AND ?",
        (filters["min_followers"], filters["max_followers"]),
    )

    done = 0
    for channel in channels:
        try:
            videos = []
            if channel["uploads_playlist"]:
                video_ids = youtube.get_recent_video_ids(api_key, channel["uploads_playlist"], how_many)
                if video_ids:
                    for item in youtube.get_videos(api_key, video_ids):
                        videos.append(read_video(item))
            videos.sort(key=lambda video: video["published_at"] or "", reverse=True)

            rate, average_views = engagement_rate(videos)
            storage.update_influencer(conn, channel["channel_id"], {
                "recent_videos": videos,
                "last_upload_at": videos[0]["published_at"] if videos else None,
                "avg_views": average_views,
                "engagement_rate": rate,
                "metrics_at": now_text(),
            })
            done += 1
        except youtube.QuotaExceeded as error:
            log.warning("%s Run again to continue.", error)
            break
        except youtube.YouTubeError as error:
            log.error("metrics failed for %s: %s", channel["name"], error)

    log.info("metrics done: %d channels (%d quota units used)", done, youtube.usage["units"])
    return done


def read_video(item):
    """Pick the fields we need out of YouTube's video answer."""
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    return {
        "id": item.get("id"),
        "title": snippet.get("title", ""),
        "published_at": snippet.get("publishedAt"),
        "views": to_number(stats.get("viewCount")),
        "likes": to_number(stats.get("likeCount")),
        "comments": to_number(stats.get("commentCount")),
        "tags": (snippet.get("tags") or [])[:10],
        "description": (snippet.get("description") or "")[:1500],
    }


def engagement_rate(videos):
    """Return (engagement rate in %, average views).

    Formula per video:  (likes + comments) / views
    The channel's rate is the average of its recent videos, times 100.
    Videos with hidden likes or zero views are SKIPPED, not counted as 0,
    so the number is never invented. If no video can be used we return None.
    """
    rates = []
    view_counts = []
    for video in videos:
        if not video.get("views"):
            continue
        view_counts.append(video["views"])
        if video.get("likes") is None:
            continue
        comments = video.get("comments") or 0
        rates.append((video["likes"] + comments) / video["views"])

    rate = round(100 * sum(rates) / len(rates), 2) if rates else None
    average_views = round(sum(view_counts) / len(view_counts)) if view_counts else None
    return rate, average_views
