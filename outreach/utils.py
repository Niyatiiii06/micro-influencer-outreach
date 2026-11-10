"""Small helper functions used by several files."""
import logging
import re
from datetime import datetime, timezone

# Text we store when a value could not be found. We never invent a value.
NOT_FOUND = "Not Found"
NOT_AVAILABLE = "Not Available"

EMAIL_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"


def setup_logging():
    """Print progress messages like: 21:40:05 INFO discovery: 120 channels saved"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")


def now_text():
    """Current UTC time as text, e.g. 2026-11-10T16:30:00Z"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def days_since(date_text):
    """How many days ago a date was. Returns None if the date is missing or unreadable."""
    if not date_text:
        return None
    try:
        date = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - date).days


def count_words(text):
    return len((text or "").split())


def to_number(value):
    """Turn "1234" into 1234. Returns None when the value is missing or not a number."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def find_keyword(text, keywords):
    """Return the first keyword that appears in the text as a whole word, or None.

    Whole word means "gym" matches "my gym day" but not "gymnastics".
    """
    text = (text or "").lower()
    for keyword in keywords:
        pattern = r"(?<![a-z0-9])" + re.escape(keyword.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            return keyword
    return None


def is_valid_email(value):
    """True only for a real-looking address. "Not Found" and empty values are invalid."""
    if not value or value == NOT_FOUND:
        return False
    return re.fullmatch(EMAIL_PATTERN, value) is not None
