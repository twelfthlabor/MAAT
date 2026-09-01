"""Canadian news media RSS connector — CBC, CTV, Global News.

Searches the public RSS feeds and search pages of Canada's three largest
national broadcasters for coverage of missing-person cases.  No API key
required — all endpoints are public.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import (
    ConnectorRunResult,
    NormalizedLead,
    QueryContext,
)

# ── Canadian broadcaster search endpoints ──────────────────────────
_SOURCES: list[dict[str, str]] = [
    {
        "name": "CBC News",
        "search_url": "https://www.cbc.ca/cmlink/rss-search",
        "param_key": "q",
        "domain": "cbc.ca",
    },
    {
        "name": "CTV News",
        "search_url": "https://www.ctvnews.ca/rss/search",
        "param_key": "searchTerm",
        "domain": "ctvnews.ca",
    },
    {
        "name": "Global News",
        "search_url": "https://globalnews.ca/feed/",
        "param_key": "s",
        "domain": "globalnews.ca",
    },
]


def _parse_pub_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except Exception:
        pass
    # Try ISO-8601 fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


class CanadianNewsMediaConnector:
    """Search CBC, CTV, and Global News RSS feeds for missing-person coverage."""

    metadata = ConnectorMetadata(
        name="canadian-news-media",
        source_kind="clear-web",
        disabled_by_default=True,
        description="Searches Canadian national broadcaster RSS feeds (CBC, CTV, Global News) for case coverage.",
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="Canadian News Media connector disabled by configuration.")

        name = (context.name or "").strip()
        if not name:
            return ConnectorRunResult(warning="No name available for Canadian news media search.")

        # Build query variants
        queries: list[str] = [
            f"{name} missing",
        ]
        if context.city:
            queries.append(f"{name} {context.city}")
        if context.province and context.province != context.city:
            queries.append(f"{name} {context.province}")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        factory = self.client_factory or (
            lambda timeout: httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        )

        async with factory(settings.connector_timeout_seconds) as client:
            for source in _SOURCES:
                for query in queries:
                    params = {source["param_key"]: query}
                    request_url = f"{source['search_url']}?{urlencode(params)}"

                    try:
                        response = await client.get(
                            request_url,
                            headers={
                                "User-Agent": "maat-intelligence/2.0",
                                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                            },
                        )
                        response.raise_for_status()
                        raw_xml = response.text
                    except Exception as exc:
                        query_logs.append({
                            "connector_name": self.metadata.name,
                            "source_kind": self.metadata.source_kind,
                            "query_used": f"{source['name']}: {query}",
                            "status": "failed",
                            "http_status": getattr(
                                getattr(exc, "response", None), "status_code", None
                            ),
                            "result_count": 0,
                            "notes": f"{source['name']} RSS failed: {exc}",
                        })
                        continue

                    await rate_limit_sleep()

                    try:
                        root = ElementTree.fromstring(raw_xml)
                    except ElementTree.ParseError:
                        query_logs.append({
                            "connector_name": self.metadata.name,
                            "source_kind": self.metadata.source_kind,
                            "query_used": f"{source['name']}: {query}",
                            "status": "failed",
                            "http_status": getattr(response, "status_code", None),
                            "result_count": 0,
                            "notes": f"Failed to parse {source['name']} RSS XML.",
                        })
                        continue

                    items = root.findall(".//item")[:8]
                    added = 0

                    for item in items:
                        title = (item.findtext("title") or "").strip()
                        link = (item.findtext("link") or "").strip()
                        pub_date_str = item.findtext("pubDate")
                        description = _strip_html(
                            item.findtext("description") or ""
                        )[:500]

                        if not link or link in seen_urls:
                            continue

                        # Relevance check: name must appear in title or description
                        name_lower = name.lower()
                        text_blob = f"{title} {description}".lower()
                        name_parts = name_lower.split()
                        if not any(part in text_blob for part in name_parts if len(part) > 2):
                            continue

                        seen_urls.add(link)
                        added += 1

                        published_at = _parse_pub_date(pub_date_str)

                        # Higher trust for stories that match missing-person keywords
                        missing_kw = {"missing", "disappeared", "last seen", "police", "search", "amber alert"}
                        has_missing_context = any(kw in text_blob for kw in missing_kw)
                        trust = 0.70 if has_missing_context else 0.55

                        leads.append(
                            NormalizedLead(
                                connector_name=self.metadata.name,
                                source_kind=self.metadata.source_kind,
                                lead_type="news-article",
                                category="news-monitoring",
                                source_name=source["name"],
                                source_url=link,
                                query_used=query,
                                found_at=datetime.now(timezone.utc),
                                title=title or f"News result for {name}",
                                summary=f"{source['name']} | {published_at.date().isoformat() if published_at else 'Unknown date'}",
                                content_excerpt=description or title,
                                published_at=published_at,
                                location_text=None,
                                source_trust=trust,
                                rationale=[
                                    f"Matched through {source['name']} RSS feed (Canadian national broadcaster).",
                                    f"Query: {query}",
                                    "Canadian broadcasters are primary sources for missing-person public appeals." if has_missing_context else "Name match in Canadian broadcast media.",
                                ],
                            )
                        )

                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": f"{source['name']}: {query}",
                        "status": "completed",
                        "http_status": getattr(response, "status_code", None),
                        "result_count": added,
                        "notes": f"{source['name']}: {len(items)} items, {added} relevant after filtering.",
                    })

        if not leads:
            return ConnectorRunResult(
                warning="No Canadian news media articles matched the case queries.",
                query_logs=query_logs,
            )

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
