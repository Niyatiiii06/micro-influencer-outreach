"""STAGE 5: write a personalised email + Instagram DM for each shortlisted creator.

How it works:
  1. build_signals()   collects the real facts about the creator (name, themes, recent titles...)
  2. the LLM writes both messages from those facts
  3. check_message()   verifies word counts etc. If it fails, we tell the LLM why and ask again
  4. if there is no LLM key (or it keeps failing) write_offline() builds a simpler message
"""
import json
import logging

from . import llm, storage
from .utils import count_words, now_text

log = logging.getLogger("personalize")

SYSTEM_PROMPT = """You write influencer outreach for a brand's creator-partnerships team.
You will receive a JSON object with BRAND details and a CREATOR profile built only from public data.

Write two messages and return ONLY a JSON object with exactly these keys:
  "email_subject": string, at most 9 words, specific to this creator
  "email_body": string, EMAIL_MIN-EMAIL_MAX words in total (count the greeting and sign-off)
  "instagram_dm": string, DM_MIN-DM_MAX words, no links, no hashtags
  "personalization_signals": array of the specific creator details you referenced

Rules:
- Greet the creator by "creator.name".
- Reference at least one item from "recent_video_titles" naturally (paraphrase it, do not paste a long title in full)
  and at least one item from "content_themes".
- Match the creator's "content_tone".
- Propose exactly the collaboration in "collaboration.angle" using "collaboration.offer", and state the value for the creator.
- Use ONLY facts present in the input. Never invent statistics, past collaborations, compliments about things
  you cannot see, or audience demographics.
- Sound like a person: plain, warm, concrete. No "I hope this email finds you well", no "we came across your profile",
  no emojis in the email, at most one in the DM.
- End the email with a single low-pressure question, then the sign-off: "Best," / sender name / brand name.
- No placeholders such as [Name] and no markdown."""


def choose_angle(channel, cfg):
    """Pick the type of collaboration from the creator's size and engagement.

    Small + engaged creators are great for UGC, mid-size for affiliate deals,
    larger ones for paid placements. Returns (angle name, offer text).
    """
    offers = cfg["brand"]["collaboration_offers"]
    followers = channel.get("followers") or 0
    rate = channel.get("engagement_rate") or 0

    if followers < 20000:
        angle = "UGC content creation" if rate >= 3 else "Barter collaboration"
    elif followers < 60000:
        angle = "Affiliate campaign" if rate >= 3 else "UGC content creation"
    else:
        angle = "Paid product placement"

    if angle not in offers:
        angle = list(offers.keys())[0]
    return angle, offers[angle]


def build_signals(channel, cfg):
    """The exact facts the writer is allowed to use. Saved with each message as proof."""
    brand = cfg["brand"]
    angle, offer = choose_angle(channel, cfg)

    titles = []
    for video in (channel.get("recent_videos") or [])[:3]:
        if video.get("title"):
            titles.append(video["title"])

    return {
        "brand": {"name": brand["name"], "product": brand["product"], "audience": brand["audience"],
                  "sender_name": brand["sender_name"], "sender_role": brand["sender_role"]},
        "creator": {
            "name": channel["name"],
            "platform": channel["platform"],
            "niche": cfg["niche"]["name"],
            "content_themes": channel.get("content_themes") or channel.get("sub_niches") or [],
            "content_tone": channel.get("content_tone"),
            "recent_video_titles": titles,
            "followers": channel.get("followers"),
            "engagement_rate_percent": channel.get("engagement_rate"),
            "country": channel.get("country"),
        },
        "collaboration": {"angle": angle, "offer": offer},
    }


def check_message(message, signals, cfg):
    """Return a list of problems with a message. An empty list means it is fine."""
    limits = cfg["messages"]
    body = str(message.get("email_body") or "").strip()
    dm = str(message.get("instagram_dm") or "").strip()
    problems = []

    if not str(message.get("email_subject") or "").strip():
        problems.append("email_subject is empty")

    words = count_words(body)
    if words < limits["email_min_words"] or words > limits["email_max_words"]:
        problems.append(f"email_body has {words} words; must be {limits['email_min_words']}-{limits['email_max_words']}")

    words = count_words(dm)
    if words < limits["dm_min_words"] or words > limits["dm_max_words"]:
        problems.append(f"instagram_dm has {words} words; must be {limits['dm_min_words']}-{limits['dm_max_words']}")

    if "[" in body + dm or "{" in body + dm:
        problems.append("contains an unfilled placeholder")
    if "http" in dm.lower():
        problems.append("instagram_dm must not contain links")

    first_name = signals["creator"]["name"].split()[0].lower()
    if first_name not in body.lower():
        problems.append("email_body must greet the creator by name")
    return problems


def shorten_title(title, max_words):
    """Keep a long video title readable: first few words only."""
    words = title.split("|")[0].split("#")[0].strip(" -:!").split()
    if len(words) > max_words:
        return " ".join(words[:max_words]) + "..."
    return " ".join(words)


def write_offline(signals, cfg):
    """Backup writer that needs no LLM.

    It still uses each creator's own details (name, latest video, themes,
    numbers, offer), but the wording is simpler, so these messages are always
    marked "needs review".
    """
    creator, brand, collab = signals["creator"], signals["brand"], signals["collaboration"]
    limits = cfg["messages"]
    themes = creator["content_themes"] or [creator["niche"].lower()]
    theme = themes[0]
    second_theme = themes[1] if len(themes) > 1 else creator["niche"].lower() + " content"
    tone = (creator["content_tone"] or "practical").split(",")[0]
    title = shorten_title(creator["recent_video_titles"][0], 7) if creator["recent_video_titles"] else None

    if title:
        openers = [
            f'Your recent video "{title}" is what made our team stop scrolling.',
            f'I just watched "{title}" and the {tone} way you teach {theme} really works.',
            f'"{title}" was shared in our team chat this week, and for good reason.',
        ]
    else:
        openers = [f"Your {theme} videos stood out to our team.",
                   f"The {tone} way you teach {theme} really works.",
                   f"Your {theme} channel was shared in our team chat this week."]
    opener = openers[len(creator["name"]) % 3]

    greeting = f"Hi {creator['name']},"
    sign_off = f"Best,\n{brand['sender_name']}\n{brand['name']}"
    sentences = [
        opener,
        f"I'm {brand['sender_name']} from {brand['name']}; we make {brand['product']}.",
        f"We'd like to offer you {collab['offer']}.",
        "Would you be open to a quick chat this week?",
    ]
    extras = [
        f"Your mix of {theme} and {second_theme} is a natural match for {brand['audience']}.",
        "You would keep full creative control, and we can work around your upload schedule.",
        "There is no script to follow; we only ask for your honest take.",
    ]

    def build_body():
        return greeting + "\n\n" + " ".join(sentences) + "\n\n" + sign_off

    for extra in extras:
        if count_words(build_body()) >= limits["email_min_words"] + 4:
            break
        sentences.insert(len(sentences) - 1, extra)
    while count_words(build_body()) > limits["email_max_words"] and len(sentences) > 4:
        sentences.pop(len(sentences) - 2)

    angle = collab["angle"].lower()
    dm_options = []
    if title:
        dm_options.append(f'Hi {creator["name"]}! Loved your {theme} content, especially "{shorten_title(title, 4)}". '
                          f"We're {brand['name']} and have a collaboration idea for your audience ({angle}). Open to details?")
    dm_options.append(f"Hey {creator['name']}, your {theme} videos are a great fit for {brand['name']}. "
                      f"We have a collaboration idea for your audience ({angle}). Can I send over the details?")
    dm_options.append(f"Hey {creator['name']}, your {theme} videos are a great fit for {brand['name']}. "
                      "We have a collaboration idea for your audience. Can I send over the details?")
    dm = dm_options[-1]
    for option in dm_options:
        if limits["dm_min_words"] <= count_words(option) <= limits["dm_max_words"]:
            dm = option
            break

    return {
        "email_subject": f"{brand['name']} x {creator['name']}: {angle} idea",
        "email_body": build_body(),
        "instagram_dm": dm,
        "personalization_signals": ["name: " + creator["name"], "theme: " + theme, "angle: " + collab["angle"]]
                                   + (["recent video: " + title] if title else []),
    }


def write_messages(signals, cfg, use_llm):
    """Returns (message, who wrote it, model name, remaining problems)."""
    if use_llm:
        limits = cfg["messages"]
        system = (SYSTEM_PROMPT.replace("EMAIL_MIN", str(limits["email_min_words"]))
                  .replace("EMAIL_MAX", str(limits["email_max_words"]))
                  .replace("DM_MIN", str(limits["dm_min_words"]))
                  .replace("DM_MAX", str(limits["dm_max_words"])))
        user = json.dumps(signals, ensure_ascii=False, indent=2)
        feedback = ""

        for attempt in range(1, cfg["llm"]["max_attempts"] + 1):
            try:
                message, model = llm.ask_llm(cfg, system, user + feedback)
            except llm.LLMError as error:
                log.warning("%s: %s", signals["creator"]["name"], error)
                break
            problems = check_message(message, signals, cfg)
            if not problems:
                return message, "llm", model, []
            log.info("%s: attempt %d rejected (%s)", signals["creator"]["name"], attempt, "; ".join(problems))
            feedback = ("\n\nYour previous answer was rejected for these reasons: " + "; ".join(problems)
                        + ". Rewrite both messages, fix every problem, and return the full JSON again.")

    message = write_offline(signals, cfg)
    return message, "offline_rule_based", None, check_message(message, signals, cfg)


def run_personalization(cfg, conn, regenerate=False):
    """Write messages for every shortlisted creator that does not have them yet."""
    provider, _key = llm.choose_provider(cfg)
    use_llm = provider is not None
    if not use_llm:
        log.warning("No LLM key found: using the offline writer. Add GEMINI_API_KEY or GROQ_API_KEY to .env.")

    sql = ("SELECT i.* FROM influencers i LEFT JOIN messages m ON m.channel_id = i.channel_id "
           "WHERE i.filter_status = 'passed' AND i.enriched_at IS NOT NULL")
    if not regenerate:
        sql += " AND m.channel_id IS NULL"
    channels = storage.fetch(conn, sql + " ORDER BY i.fit_score DESC")

    for number, channel in enumerate(channels, start=1):
        signals = build_signals(channel, cfg)
        message, written_by, model, problems = write_messages(signals, cfg, use_llm)

        # Only an LLM message that passed every check is approved automatically.
        needs_review = 1 if (written_by != "llm" or problems) else 0

        storage.save_message(conn, {
            "channel_id": channel["channel_id"],
            "email_subject": str(message.get("email_subject", "")).strip(),
            "email_body": str(message.get("email_body", "")).strip(),
            "instagram_dm": str(message.get("instagram_dm", "")).strip(),
            "collab_angle": signals["collaboration"]["angle"],
            "signals": {"input": signals, "referenced": message.get("personalization_signals", []), "problems": problems},
            "generated_by": written_by,
            "model": model,
            "email_words": count_words(str(message.get("email_body", ""))),
            "dm_words": count_words(str(message.get("instagram_dm", ""))),
            "needs_review": needs_review,
            "approved": 0 if needs_review else 1,
            "created_at": now_text(),
        })
        log.info("[%d/%d] %s -> %s%s", number, len(channels), channel["name"], written_by,
                 " (needs review)" if needs_review else "")
    return len(channels)
