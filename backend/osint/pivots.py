"""High-signal pivots derived from already collected public-source leads."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from backend.osint.normalization.models import NormalizedLead


_HOST_RULES: dict[str, tuple[int, str]] = {
    "github.com": (0, ""),
    "instagram.com": (0, ""),
    "tiktok.com": (0, "@"),
    "twitter.com": (0, ""),
    "x.com": (0, ""),
    "reddit.com": (1, "user"),
    "youtube.com": (0, "@"),
    "snapchat.com": (1, "add"),
    "pinterest.com": (0, ""),
}

_RESERVED = {
    "about", "accounts", "api", "channel", "channels", "explore", "feed",
    "groups", "home", "login", "marketplace", "p", "pages", "privacy",
    "reel", "reels", "search", "settings", "share", "signup", "stories",
    "terms", "user", "users", "watch",
}


def username_from_url(value: str) -> str | None:
    """Extract a username only from a recognized public-profile URL shape."""

    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return None
    host = parsed.netloc.lower().removeprefix("www.")
    rule = _HOST_RULES.get(host)
    if not rule:
        return None
    segments = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    index, prefix = rule
    if len(segments) <= index:
        return None
    if prefix:
        if index == 0:
            if not segments[0].startswith(prefix):
                return None
            candidate = segments[0].removeprefix(prefix)
        else:
            if segments[index - 1].lower() != prefix:
                return None
            candidate = segments[index]
    else:
        candidate = segments[index]
    candidate = candidate.strip("@. ")
    if candidate.lower() in _RESERVED or not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", candidate):
        return None
    return candidate


def discover_usernames(leads: list[NormalizedLead], limit: int = 8) -> list[str]:
    """Return unique URL-backed usernames for downstream enumeration tools."""

    usernames: list[str] = []
    seen: set[str] = set()
    for lead in leads:
        candidate = username_from_url(lead.source_url)
        key = candidate.lower() if candidate else ""
        if not candidate or key in seen:
            continue
        seen.add(key)
        usernames.append(candidate)
        if len(usernames) >= limit:
            break
    return usernames
