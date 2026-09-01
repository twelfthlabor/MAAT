"""Websleuths and missing persons community forum connector.

Searches public discussion forums where citizen investigators discuss missing
persons cases. These are high-value sources because community members often
surface details, sightings, and theories not found in official reports.

Sources searched:
  - Websleuths.com (via DuckDuckGo site-scoped search)
  - r/UnresolvedMysteries, r/MissingPersons, r/RBI (via Reddit RSS)
  - True Crime discussion sites
  - Missing persons Facebook group posts (via search engine)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext

try:
    from ddgs import DDGS
    _HAS_DDGS = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _HAS_DDGS = True
    except ImportError:
        _HAS_DDGS = False

_ADULT_KEYWORDS = {
    "porn", "pornstar", "xxx", "onlyfans", "escort", "adult film",
    "webcam model", "foxy reviews", "nylon-queens",
}

# Community sources ordered by investigative value
_COMMUNITY_SITES = [
    {
        "domain": "websleuths.com",
        "label": "Websleuths",
        "trust": 0.45,
        "rationale": "Websleuths — large citizen investigation community with case-specific threads.",
    },
    {
        "domain": "reddit.com/r/UnresolvedMysteries",
        "label": "r/UnresolvedMysteries",
        "trust": 0.40,
        "rationale": "r/UnresolvedMysteries — crowdsourced analysis of missing persons and cold cases.",
    },
    {
        "domain": "reddit.com/r/MissingPersons",
        "label": "r/MissingPersons",
        "trust": 0.40,
        "rationale": "r/MissingPersons — community dedicated to sharing and discussing active cases.",
    },
    {
        "domain": "reddit.com/r/RBI",
        "label": "r/RBI (Reddit Bureau of Investigation)",
        "trust": 0.35,
        "rationale": "r/RBI — community investigations and information gathering.",
    },
    {
        "domain": "reddit.com/r/TrueCrime",
        "label": "r/TrueCrime",
        "trust": 0.35,
        "rationale": "r/TrueCrime — discussion of cases including missing persons and unresolved cases.",
    },
    {
        "domain": "charleyproject.org",
        "label": "The Charley Project",
        "trust": 0.55,
        "rationale": "The Charley Project — comprehensive database of cold missing persons cases.",
    },
    {
        "domain": "doenetwork.org",
        "label": "Doe Network",
        "trust": 0.55,
        "rationale": "Doe Network — international center for unidentified and missing persons.",
    },
]


def _is_adult_content(title: str, body: str) -> bool:
    text_blob = f"{title} {body}".lower()
    return any(kw in text_blob for kw in _ADULT_KEYWORDS)


def _name_relevant(name: str, title: str, body: str) -> bool:
    if not name:
        return True
    text_blob = f"{title} {body}".lower()
    name_parts = [p.lower() for p in name.split() if len(p) >= 3]
    if not name_parts:
        return True
    return sum(1 for p in name_parts if p in text_blob) >= min(2, len(name_parts))


class WebsleuthsConnector:
    """Search citizen investigation communities for case discussions."""

    metadata = ConnectorMetadata(
        name="community-forums",
        source_kind="clear-web",
        disabled_by_default=True,
        description=(
            "Search Websleuths, Reddit communities, Charley Project, and Doe Network "
            "for citizen investigations and case discussions."
        ),
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors and _HAS_DDGS)

    def _build_queries(self, context: QueryContext) -> list[dict]:
        """Build site-scoped queries for community sources."""
        name = context.name or ""
        city = context.city or ""
        province = context.province or ""
        queries = []
        seen = set()

        def _add(query: str, site: dict):
            key = query.lower().strip()
            if key not in seen:
                seen.add(key)
                queries.append({"query": query, "site": site})

        # Site-scoped searches for each community
        for site in _COMMUNITY_SITES:
            _add(f'site:{site["domain"]} "{name}"', site)
            if city:
                _add(f'site:{site["domain"]} "{name}" "{city}"', site)

        # Generic community searches (catches forums we haven't listed)
        _add(f'"{name}" missing forum discussion', {"domain": "generic", "label": "Forum Search", "trust": 0.35, "rationale": "General forum search for community discussions."})
        if city and province:
            _add(f'"{name}" missing "{city}" "{province}" community', {"domain": "generic", "label": "Community Search", "trust": 0.35, "rationale": "Community search anchored to case location."})

        return queries

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            if not _HAS_DDGS:
                return ConnectorRunResult(
                    warning="Community forums connector requires 'ddgs' package."
                )
            return ConnectorRunResult(warning="Community forums connector disabled by configuration.")

        queries = self._build_queries(context)
        name = context.name or ""

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()

        ddgs = DDGS()

        import asyncio

        for entry in queries[:8]:  # Cap queries to avoid hanging
            query = entry["query"]
            site = entry["site"]
            try:
                # Run synchronous DDGS in a thread with timeout
                loop = asyncio.get_event_loop()
                results = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda q=query: list(ddgs.text(q, region="ca-en", max_results=5))),
                    timeout=15.0,
                )
                added = 0
                for result in results:
                    source_url = result.get("href", "")
                    if not source_url or source_url in seen_urls:
                        continue
                    title = result.get("title", "")
                    body = result.get("body", "")
                    if not _name_relevant(name, title, body):
                        continue
                    if _is_adult_content(title, body):
                        continue
                    seen_urls.add(source_url)
                    added += 1

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type="community-discussion",
                            category="community-intelligence",
                            source_name=site["label"],
                            source_url=source_url,
                            query_used=query,
                            found_at=datetime.now(timezone.utc),
                            title=title,
                            summary=body or "Community discussion result",
                            content_excerpt=body[:500],
                            location_text=None,
                            source_trust=site["trust"],
                            rationale=[
                                site["rationale"],
                                "Community intelligence — citizen investigators often surface details not in official reports.",
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
                    "notes": f"{site['label']}: {len(results)} results, {added} new after dedupe.",
                })
            except (Exception, asyncio.TimeoutError) as exc:
                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": query,
                    "status": "failed",
                    "http_status": None,
                    "result_count": 0,
                    "notes": f"Community search failed: {exc}",
                })

            await rate_limit_sleep()

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
