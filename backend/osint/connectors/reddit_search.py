"""Reddit public RSS search connector — no API key required.

Searches Reddit's public RSS feed for community discussions about missing persons.
Returns posts from subreddits like r/UnresolvedMysteries, r/missingpersons, etc.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable
from email.utils import parsedate_to_datetime

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext


def _parse_rss_entries(xml_text: str) -> list[dict[str, str]]:
    """Minimal RSS/Atom entry parser — avoids lxml/feedparser dependency."""
    entries: list[dict[str, str]] = []
    # Parse <entry> blocks (Atom format used by Reddit RSS)
    for match in re.finditer(r"<entry>(.*?)</entry>", xml_text, re.DOTALL):
        block = match.group(1)
        entry: dict[str, str] = {}
        # Title
        t = re.search(r"<title>(.*?)</title>", block, re.DOTALL)
        if t:
            entry["title"] = t.group(1).strip()
        # Link
        l = re.search(r'<link\s+href="([^"]+)"', block)
        if l:
            entry["link"] = l.group(1).strip()
        # Updated timestamp
        u = re.search(r"<updated>(.*?)</updated>", block, re.DOTALL)
        if u:
            entry["updated"] = u.group(1).strip()
        # Content
        c = re.search(r"<content[^>]*>(.*?)</content>", block, re.DOTALL)
        if c:
            entry["content"] = c.group(1).strip()[:500]
        # Category (subreddit)
        cat = re.search(r'<category[^>]*term="([^"]+)"', block)
        if cat:
            entry["subreddit"] = cat.group(1).strip()
        if entry.get("link"):
            entries.append(entry)
    return entries


class RedditSearchConnector:
    """Free Reddit search via public RSS feed (no authentication needed)."""

    metadata = ConnectorMetadata(
        name="reddit-search",
        source_kind="clear-web",
        disabled_by_default=True,
        description="Public Reddit RSS search for community discussions about missing persons.",
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="Reddit search connector disabled by configuration.")

        # Build targeted queries (unquoted — Reddit RSS blocks quoted searches)
        queries: list[str] = []
        if context.name:
            queries.append(f'{context.name} missing')
            if context.city:
                queries.append(f'{context.name} {context.city}')
            if context.province and context.province != context.city:
                queries.append(f'{context.name} {context.province}')
        if not queries:
            return ConnectorRunResult(warning="No Reddit queries could be built from the case facts.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        factory = self.client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout, follow_redirects=True))

        async with factory(settings.connector_timeout_seconds) as client:
            for query in queries:
                try:
                    response = await client.get(
                        "https://www.reddit.com/search.rss",
                        params={
                            "q": query,
                            "sort": "relevance",
                            "limit": "10",
                        },
                        headers={
                            "User-Agent": "maat-intelligence/2.0 (academic research tool)",
                        },
                    )
                    response.raise_for_status()
                    entries = _parse_rss_entries(response.text)
                except Exception as exc:
                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": query,
                        "status": "failed",
                        "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                        "result_count": 0,
                        "notes": f"Reddit RSS search failed: {exc}",
                    })
                    continue

                await rate_limit_sleep()
                added = 0

                for entry in entries[:10]:
                    source_url = entry.get("link", "")
                    if not source_url or source_url in seen_urls:
                        continue

                    # Parse timestamp
                    published_at = None
                    updated = entry.get("updated", "")
                    if updated:
                        try:
                            published_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            pass

                    subreddit = entry.get("subreddit", "unknown")
                    title = entry.get("title", "Untitled Reddit post")
                    content = entry.get("content", "")
                    # Strip HTML from content snippet
                    clean_content = re.sub(r"<[^>]+>", " ", content)
                    clean_content = re.sub(r"&[a-z]+;", " ", clean_content)
                    clean_content = re.sub(r"\s+", " ", clean_content).strip()[:400]

                    # Relevance gate: require meaningful name overlap.
                    # For multi-word names, need at least 2 parts matching
                    # (or the full last name).  This filters out noise like
                    # "Kai'Sa" matching "Kaisa Raine Morin".
                    text_blob = f"{title} {clean_content}".lower()
                    name_parts = [p.lower() for p in (context.name or "").split() if len(p) > 2]
                    if name_parts:
                        matching_parts = sum(1 for p in name_parts if p in text_blob)
                        threshold = min(2, len(name_parts))  # need 2 parts unless single-word name
                        if matching_parts < threshold:
                            continue

                    # Extra: boost if missing-person keywords are present
                    _missing_kw = {"missing", "disappeared", "last seen", "police", "search",
                                   "amber alert", "rcmp", "help find", "have you seen"}
                    has_missing_context = any(kw in text_blob for kw in _missing_kw)

                    seen_urls.add(source_url)
                    added += 1

                    # Determine trust based on subreddit relevance
                    trust = 0.40
                    rationale_notes = [
                        f"Found on r/{subreddit} via Reddit RSS search.",
                        f"Query: {query}",
                    ]

                    relevant_subs = {
                        "unresolvedmysteries", "missingpersons", "rbi",
                        "truecrime", "gratefuldoe", "missing411",
                        "canada", "ontario", "britishcolumbia", "alberta",
                        "saskatchewan", "manitoba", "quebec", "newbrunswick",
                        "novascotia", "pei", "newfoundland",
                    }
                    if subreddit.lower() in relevant_subs:
                        trust = 0.55
                        rationale_notes.append(f"Posted in a relevant subreddit (r/{subreddit}).")
                    if has_missing_context:
                        trust = min(trust + 0.15, 0.70)
                        rationale_notes.append("Contains missing-person keywords.")

                    summary_text = clean_content if clean_content else f"Reddit discussion on r/{subreddit}"

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type="community-discussion",
                            category="social-media",
                            source_name=f"Reddit r/{subreddit}",
                            source_url=source_url,
                            query_used=query,
                            found_at=datetime.now(timezone.utc),
                            published_at=published_at,
                            title=title,
                            summary=summary_text,
                            content_excerpt=clean_content,
                            location_text=None,
                            source_trust=trust,
                            rationale=rationale_notes,
                        )
                    )

                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": query,
                    "status": "completed",
                    "http_status": response.status_code,
                    "result_count": added,
                    "notes": f"Reddit RSS returned {len(entries)} entries, {added} new after dedupe.",
                })

        if not leads:
            return ConnectorRunResult(
                warning="No Reddit posts matched the case queries.",
                query_logs=query_logs,
            )

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
