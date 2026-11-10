"""STAGE 4: add contact details and content context to shortlisted channels.

IMPORTANT RULE: an email is only ever COPIED from text the creator published
(channel description, video descriptions, or their own website). We never
guess or build an address. If none is found, we store "Not Found".
"""
import logging
import re
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests

from . import storage
from .utils import EMAIL_PATTERN, NOT_AVAILABLE, NOT_FOUND, now_text

log = logging.getLogger("enrichment")

USER_AGENT = "influencer-outreach-research-bot/1.0 (student project; respects robots.txt)"

NOT_A_WEBSITE = ["youtube.com", "youtu.be", "instagram.com", "tiktok.com", "facebook.com", "twitter.com", "x.com",
                 "t.me", "wa.me", "whatsapp.com", "linkedin.com", "amzn.to", "amazon.", "bit.ly", "spotify.com",
                 "discord.", "play.google.com", "apple.com", "linktr.ee", "threads.net", "snapchat.com", "pinterest."]

JUNK_WORDS = ["example.", "sentry", "wixpress", "domain.com", "yourname", "noreply", "no-reply"]
JUNK_ENDINGS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css")


def find_emails(text):
    """All real-looking email addresses in a piece of text (lower-case, no repeats)."""
    emails = []
    for match in re.findall(EMAIL_PATTERN, text or ""):
        email = match.strip(".").lower()
        if email.endswith(JUNK_ENDINGS):
            continue
        is_junk = False
        for word in JUNK_WORDS:
            if word in email:
                is_junk = True
        if not is_junk and email not in emails:
            emails.append(email)
    return emails


def find_website(text):
    """First link in the text that is the creator's own site (not social media)."""
    for url in re.findall(r"https?://[^\s<>\"')\]]+", text or ""):
        host = urlparse(url).netloc.lower()
        is_social = False
        for site in NOT_A_WEBSITE:
            if site in host:
                is_social = True
        if host and not is_social:
            return url.rstrip(".,;")
    return None


def find_email_on_website(url):
    """Look for an email on the creator's site: home page, /contact and /about.

    Polite scraping: we read robots.txt first and skip pages it disallows,
    and we make at most 3 small requests. Returns (email, page) or (None, None).
    """
    parts = urlparse(url)
    base = parts.scheme + "://" + parts.netloc
    headers = {"User-Agent": USER_AGENT}

    rules = robotparser.RobotFileParser()
    try:
        robots = requests.get(base + "/robots.txt", headers=headers, timeout=8)
        rules.parse(robots.text.splitlines() if robots.status_code == 200 else [])
    except requests.RequestException:
        return None, None

    pages = [url, urljoin(base, "/contact"), urljoin(base, "/about")]
    for page in pages:
        if not rules.can_fetch(USER_AGENT, page):
            continue
        try:
            response = requests.get(page, headers=headers, timeout=8)
        except requests.RequestException:
            continue
        if response.status_code != 200:
            continue
        emails = find_emails(response.text[:300000])
        if emails:
            return emails[0], page
    return None, None


def find_social_link(text, pattern, base_url):
    """Find e.g. instagram.com/username in the text and return the full profile link."""
    for username in re.findall(pattern, text, flags=re.IGNORECASE):
        if username.lower() not in ["p", "reel", "reels", "explore", "stories", "tv", "accounts"]:
            return base_url + username.rstrip(".")
    return None


def guess_tone(videos):
    """A rough label for the creator's style, read from their video titles."""
    titles = ""
    for video in videos:
        titles += " " + video.get("title", "").lower()
    if re.search(r"\b(vlog|day in my life|what i eat)\b", titles):
        return "casual, vlog-style"
    if re.search(r"\b(how to|tutorial|guide|explained|tips|mistakes|beginners?)\b", titles):
        return "educational, coaching-style"
    if re.search(r"\b(challenge|transformation|days? of)\b", titles):
        return "high-energy, challenge-driven"
    return "follow-along, practical"


def find_content_themes(channel):
    """Sub-niches plus the tags the creator uses on at least 2 recent videos (max 6 themes)."""
    themes = list(channel.get("sub_niches") or [])
    tag_counts = {}
    for video in channel.get("recent_videos") or []:
        for tag in video.get("tags") or []:
            tag = tag.lower().strip()
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    most_used = sorted(tag_counts, key=lambda tag: tag_counts[tag], reverse=True)
    for tag in most_used:
        if len(themes) >= 6:
            break
        if tag_counts[tag] >= 2 and tag not in themes and len(tag) <= 30:
            themes.append(tag)
    return themes


def enrich_one(channel, visit_websites=True):
    """Work out all the enrichment fields for one channel."""
    videos = channel.get("recent_videos") or []
    description = channel.get("description") or ""
    video_descriptions = ""
    for video in videos:
        video_descriptions += "\n" + video.get("description", "")

    # --- Email: try three public places, in order -------------------------
    email = NOT_FOUND
    source = None

    found = find_emails(description)
    if found:
        email, source = found[0], "channel description"

    if email == NOT_FOUND:
        found = find_emails(video_descriptions)
        if found:
            email, source = found[0], "video description"

    website = find_website(description) or find_website(video_descriptions)
    if email == NOT_FOUND and website and visit_websites:
        site_email, page = find_email_on_website(website)
        if site_email:
            email, source = site_email, "creator website (" + page + ")"

    # --- Other fields ------------------------------------------------------
    all_text = description + "\n" + video_descriptions
    if channel.get("country"):
        geography = "Likely " + channel["country"] + " (channel country; audience split not public)"
    else:
        geography = NOT_AVAILABLE

    return {
        "email": email,
        "email_source": source,
        "website": website,
        "instagram": find_social_link(all_text, r"instagram\.com/([A-Za-z0-9_.]{2,30})", "https://www.instagram.com/"),
        "tiktok": find_social_link(all_text, r"tiktok\.com/@([A-Za-z0-9_.]{2,30})", "https://www.tiktok.com/@"),
        "content_themes": find_content_themes(channel),
        "content_tone": guess_tone(videos),
        "audience_age": NOT_AVAILABLE,
        "audience_gender": NOT_AVAILABLE,
        "audience_geo": geography,
        "enriched_at": now_text(),
    }


def run_enrichment(cfg, conn, visit_websites=True):
    """Enrich every channel that passed the filter and is not enriched yet."""
    channels = storage.fetch(conn, "SELECT * FROM influencers WHERE filter_status = 'passed' AND enriched_at IS NULL")
    for channel in channels:
        try:
            storage.update_influencer(conn, channel["channel_id"], enrich_one(channel, visit_websites))
        except Exception as error:
            log.error("enrichment failed for %s: %s", channel["name"], error)
    with_email = storage.count(conn, "SELECT COUNT(*) FROM influencers WHERE filter_status = 'passed' "
                                     "AND email IS NOT NULL AND email != ?", (NOT_FOUND,))
    log.info("enrichment done: %d profiles, %d shortlisted creators have an email", len(channels), with_email)
    return len(channels)
