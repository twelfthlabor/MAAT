"""Free DuckDuckGo search connector — uses the ddgs library.

Provides both web search and news search via DuckDuckGo, no API key required.
Falls back to the HTML lite endpoint if the library is unavailable.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext
from backend.osint.query_planner import build_public_query_plan, build_news_query_plan

try:
    from ddgs import DDGS
    _HAS_DDGS = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _HAS_DDGS = True
    except ImportError:
        _HAS_DDGS = False


def _name_relevant(context: QueryContext, title: str, body: str) -> bool:
    """Require at least part of the person's name in the result to avoid noise."""
    if not context.name:
        return True
    text_blob = f"{title} {body}".lower()
    name_parts = [p.lower() for p in context.name.split() if len(p) >= 3]
    if not name_parts:
        return True
    threshold = min(2, len(name_parts))
    return sum(1 for p in name_parts if p in text_blob) >= threshold


_ADULT_KEYWORDS = {
    "porn", "pornstar", "xxx", "onlyfans", "escort", "nylon-queens",
    "foxy reviews", "adult film", "webcam model", "stripper",
}


def _is_adult_content(title: str, body: str) -> bool:
    """Reject results containing explicit adult content keywords."""
    text_blob = f"{title} {body}".lower()
    return any(kw in text_blob for kw in _ADULT_KEYWORDS)


class DuckDuckGoHtmlConnector:
    """Free web + news search via DuckDuckGo (ddgs library)."""

    metadata = ConnectorMetadata(
        name="duckduckgo-html",
        source_kind="clear-web",
        disabled_by_default=True,
        description="Public web and news search through DuckDuckGo (ddgs library).",
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors and _HAS_DDGS)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            if not _HAS_DDGS:
                return ConnectorRunResult(
                    warning="DuckDuckGo connector requires 'ddgs' package. Install with: pip install ddgs"
                )
            return ConnectorRunResult(warning="DuckDuckGo connector disabled by configuration.")

        query_plan = build_public_query_plan(context, limit=6)
        news_plan = build_news_query_plan(context, limit=4)

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()

        ddgs = DDGS()

        # Web search
        for query in query_plan:
            try:
                results = ddgs.text(query, region="ca-en", max_results=12)
                added = 0
                for result in results:
                    source_url = result.get("href", "")
                    if not source_url or source_url in seen_urls:
                        continue
                    title = result.get("title", "Untitled result")
                    body = result.get("body", "")
                    if not _name_relevant(context, title, body):
                        continue
                    if _is_adult_content(title, body):
                        continue
                    seen_urls.add(source_url)
                    added += 1

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type="web-mention",
                            category="clear-web-search",
                            source_name="DuckDuckGo",
                            source_url=source_url,
                            query_used=query,
                            found_at=datetime.now(timezone.utc),
                            title=title,
                            summary=body or "Public web search result",
                            content_excerpt=body[:500],
                            location_text=None,
                            source_trust=0.45,
                            rationale=[
                                "Matched through DuckDuckGo web search (free, public).",
                                f"Query: {query}",
                            ],
                        )
                    )

                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": query,
                    "status": "completed",
                    "http_status": 200,
                    "result_count": added,
                    "notes": f"DuckDuckGo returned {len(results)} results, {added} new after dedupe.",
                })
            except Exception as exc:
                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": query,
                    "status": "failed",
                    "http_status": None,
                    "result_count": 0,
                    "notes": f"DuckDuckGo web search failed: {exc}",
                })

            await rate_limit_sleep()

        # News search
        for query in news_plan:
            try:
                news_results = ddgs.news(query, region="ca-en", max_results=8)
                added = 0
                for result in news_results:
                    source_url = result.get("url", "")
                    if not source_url or source_url in seen_urls:
                        continue
                    title = result.get("title", "Untitled news result")
                    body = result.get("body", "")
                    if not _name_relevant(context, title, body):
                        continue
                    if _is_adult_content(title, body):
                        continue
                    seen_urls.add(source_url)
                    added += 1

                    published_at = None
                    date_str = result.get("date", "")
                    if date_str:
                        try:
                            published_at = datetime.fromisoformat(
                                date_str.replace("Z", "+00:00")
                            )
                        except (ValueError, TypeError):
                            pass

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type="news-article",
                            category="news-monitoring",
                            source_name=result.get("source", "DuckDuckGo News"),
                            source_url=source_url,
                            query_used=query,
                            found_at=datetime.now(timezone.utc),
                            published_at=published_at,
                            title=title,
                            summary=body or "News article from DuckDuckGo",
                            content_excerpt=body[:500],
                            location_text=None,
                            source_trust=0.50,
                            rationale=[
                                "Matched through DuckDuckGo news search (free, public).",
                                f"Query: {query}",
                            ],
                        )
                    )

                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": f"[news] {query}",
                    "status": "completed",
                    "http_status": 200,
                    "result_count": added,
                    "notes": f"DuckDuckGo News returned {len(news_results)} results, {added} new.",
                })
            except Exception as exc:
                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": f"[news] {query}",
                    "status": "failed",
                    "http_status": None,
                    "result_count": 0,
                    "notes": f"DuckDuckGo news search failed: {exc}",
                })

            await rate_limit_sleep()

        if not leads:
            return ConnectorRunResult(
                warning="No DuckDuckGo results matched the case queries.",
                query_logs=query_logs,
            )

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
