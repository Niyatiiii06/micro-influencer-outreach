"""Talks to the official YouTube Data API v3 (free: 10,000 quota units per day).

Cost per call: search = 100 units, everything else = 1 unit.
"""
import logging
import time

import requests

log = logging.getLogger("youtube")

API_URL = "https://www.googleapis.com/youtube/v3"
COST = {"search": 100, "channels": 1, "playlistItems": 1, "videos": 1}
usage = {"units": 0}


class YouTubeError(Exception):
    """Something went wrong calling YouTube."""


class QuotaExceeded(YouTubeError):
    """The free daily quota is used up. Saved progress is kept; run again tomorrow."""


def api_get(api_key, resource, params):
    """Call one YouTube endpoint and return the JSON answer as a dictionary.

    Retries up to 3 times on temporary problems (network error, server busy).
    """
    if not api_key:
        raise YouTubeError("YOUTUBE_API_KEY is missing. Add it to your .env file (see .env.example).")

    clean_params = {"key": api_key}
    for name, value in params.items():
        if value not in (None, ""):
            clean_params[name] = value

    for attempt in range(1, 4):
        try:
            response = requests.get(API_URL + "/" + resource, params=clean_params, timeout=20)
        except requests.RequestException as error:
            if attempt == 3:
                raise YouTubeError("Network error calling " + resource + ": " + str(error))
            time.sleep(2 * attempt)
            continue

        usage["units"] += COST.get(resource, 1)

        if response.status_code == 200:
            return response.json()

        reason = ""
        try:
            reason = response.json()["error"]["errors"][0].get("reason", "")
        except Exception:
            pass

        if response.status_code == 403 and reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            raise QuotaExceeded("YouTube daily quota used up. Run again tomorrow; progress is saved.")
        if response.status_code == 404:
            return {}
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2 * attempt)
            continue
        raise YouTubeError(resource + " failed: HTTP " + str(response.status_code) + " " + reason)
    return {}


def search_videos(api_key, query, published_after, region, language, page_token):
    """One page (up to 50) of recent videos matching a search query."""
    return api_get(api_key, "search", {
        "part": "snippet", "type": "video", "q": query, "maxResults": 50, "order": "relevance",
        "publishedAfter": published_after, "regionCode": region, "relevanceLanguage": language,
        "pageToken": page_token,
    })


def get_channels(api_key, channel_ids):
    """Details for many channels. The API accepts 50 ids per call, so we go in groups of 50."""
    items = []
    for start in range(0, len(channel_ids), 50):
        group = channel_ids[start:start + 50]
        data = api_get(api_key, "channels", {
            "part": "snippet,statistics,contentDetails,topicDetails,brandingSettings,status",
            "id": ",".join(group), "maxResults": 50,
        })
        items.extend(data.get("items", []))
    return items


def get_recent_video_ids(api_key, uploads_playlist, limit):
    """Ids of a channel's newest uploads (every channel has an "uploads" playlist)."""
    data = api_get(api_key, "playlistItems", {"part": "contentDetails", "playlistId": uploads_playlist, "maxResults": limit})
    video_ids = []
    for item in data.get("items", []):
        video_ids.append(item["contentDetails"]["videoId"])
    return video_ids


def get_videos(api_key, video_ids):
    """Title, tags, views, likes and comments for a list of videos."""
    data = api_get(api_key, "videos", {"part": "snippet,statistics", "id": ",".join(video_ids), "maxResults": 50})
    return data.get("items", [])
