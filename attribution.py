"""Tag every published link so ExamBoot can tell which channel earned what.

The platform records the tags on the lead a visitor becomes and on the account
they open, so joining ``orders`` back to them answers "which post produced
revenue" instead of merely "how much did we publish".
"""

from __future__ import annotations

from datetime import date
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Channel -> the medium it belongs to. Anything unknown is left untagged rather
# than guessed, so a typo never silently pollutes the reporting.
CHANNEL_MEDIUMS = {
    "article": "blog",
    "linkedin": "social",
    "x": "social",
    "carousel": "social",
    "outreach": "outreach",
    "tiktok": "video",
    "shorts": "video",
    "reels": "video",
}


def default_campaign(topic_type: str = "", on: Optional[date] = None) -> str:
    """A stable campaign name when the caller has none: subject plus month."""

    stamp = (on or date.today()).strftime("%Y-%m")
    subject = _slug(topic_type)

    return f"{subject}-{stamp}" if subject else stamp


def tag_link(
    url: str,
    *,
    channel: str,
    campaign: Optional[str] = None,
    content: Optional[str | int] = None,
) -> str:
    """Append the campaign tags to ``url``.

    Existing query parameters are preserved, and tags already present on the URL
    win: a link that was tagged by hand is never silently rewritten.
    """

    if not url or not url.strip():
        return url

    medium = CHANNEL_MEDIUMS.get(channel)
    if medium is None:
        return url

    try:
        parts = urlparse(url.strip())
    except ValueError:
        return url

    if not parts.scheme or not parts.netloc:
        return url

    query = dict(parse_qsl(parts.query, keep_blank_values=True))

    tags = {
        "utm_source": channel,
        "utm_medium": medium,
        "utm_campaign": _slug(campaign) or default_campaign(),
    }
    if content is not None and str(content).strip():
        tags["utm_content"] = str(content).strip()

    for key, value in tags.items():
        query.setdefault(key, value)

    return urlunparse(parts._replace(query=urlencode(query)))


def _slug(value: Optional[str]) -> str:
    """Lowercase, dash separated, safe for a URL and for grouping in SQL."""

    if not value:
        return ""

    cleaned = []
    previous_dash = False
    for char in str(value).strip().lower():
        if char.isalnum():
            cleaned.append(char)
            previous_dash = False
        elif not previous_dash:
            cleaned.append("-")
            previous_dash = True

    return "".join(cleaned).strip("-")
