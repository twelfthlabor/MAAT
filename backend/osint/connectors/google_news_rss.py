"""Free Google News RSS connector — no API key required."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import quote_plus, urlencode
from xml.etree import ElementTree

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext
from backend.osint.query_planner import build_news_query_plan


GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"


def _parse_rfc2822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except Exception:
        return None


class GoogleNewsRssConnector:
    """Public news connector using Google News RSS feeds (free, no API key)."""

    metadata = ConnectorMetadata(
        name="google-news-rss",
        source_kind="clear-web",
        disabled_by_default=True,
        description="Passive news monitoring through the free Google News RSS feed.",
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="Google News RSS connector disabled by configuration.")

        query_plan = build_news_query_plan(context, limit=6)
        if not query_plan:
            return ConnectorRunResult(warning="No news queries could be built from the case facts.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        factory = self.client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout))

        async with factory(settings.connector_timeout_seconds) as client:
            for query in query_plan:
                params = {
                    "q": query,
                    "hl": "en-CA",
                    "gl": "CA",
                    "ceid": "CA:en",
                }
                request_url = f"{GOOGLE_NEWS_RSS_BASE}?{urlencode(params)}"

                try:
                    response = await client.get(
                        request_url,
                        headers={
                            "User-Agent": "maat-intelligence/2.0",
                            "Accept": "application/rss+xml, application/xml, text/xml",
                        },
                    )
                    response.raise_for_status()
                    raw_xml = response.text
                except Exception as exc:
                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": query,
                        "status": "failed",
                        "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                        "result_count": 0,
                        "notes": f"Google News RSS query failed: {exc}",
                    })
                    continue

                await rate_limit_sleep()

                try:
                    root = ElementTree.fromstring(raw_xml)
                except ElementTree.ParseError:
                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": query,
                        "status": "failed",
                        "http_status": response.status_code,
                        "result_count": 0,
                        "notes": "Failed to parse RSS XML response.",
                    })
                    continue

                items = root.findall(".//item")[:10]
                added = 0

                for item in items:
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    pub_date_str = item.findtext("pubDate")
                    source_el = item.find("source")
                    source_name = source_el.text.strip() if source_el is not None and source_el.text else "Google News"
                    description = (item.findtext("description") or "").strip()

                    if not link or link in seen_urls:
                        continue
                    seen_urls.add(link)
                    added += 1

                    published_at = _parse_rfc2822(pub_date_str)

                    import re
                    clean_desc = re.sub(r"<[^>]+>", "", description)[:500]
                    text_blob = f"{title} {clean_desc}".lower()
                    name_parts = [part.lower() for part in (context.name or "").split() if len(part) >= 3]
                    if name_parts and sum(part in text_blob for part in name_parts) < min(2, len(name_parts)):
                        continue

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type="news-article",
                            category="news-monitoring",
                            source_name=source_name,
                            source_url=link,
                            query_used=query,
                            found_at=datetime.now(timezone.utc),
                            title=title or "Untitled news result",
                            summary=f"{source_name} | {published_at.date().isoformat() if published_at else 'Unknown date'}",
                            content_excerpt=clean_desc or title,
                            published_at=published_at,
                            location_text=None,
                            source_trust=0.6,
                            rationale=[
                                "Matched through Google News RSS feed (free, public, no API key).",
                                f"Query: {query}",
                                f"Source: {source_name}",
                            ],
                        )
                    )

                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": query,
                    "status": "completed",
                    "http_status": response.status_code,
                    "result_count": added,
                    "notes": f"Google News RSS returned {len(items)} items, {added} new after dedupe.",
                })

        if not leads:
            return ConnectorRunResult(
                warning="No Google News RSS articles matched the case queries.",
                query_logs=query_logs,
            )

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
