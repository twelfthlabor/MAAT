"""Trace Labs-inspired network mapping and behavioral analysis connector.

Searches for family/friends, employment, school connections, hangout locations,
and behavioral patterns that can help determine WHERE a missing person might go.

Trace Labs categories covered:
  - Family / Friends
  - Employment
  - Day Last Seen / Advancing The Timeline
  - Subject's Hangout Locations / Interests
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

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


class NetworkAnalysisConnector:
    """Trace Labs-style network mapping and behavioral analysis."""

    metadata = ConnectorMetadata(
        name="network-analysis",
        source_kind="clear-web",
        disabled_by_default=True,
        description="Family, friends, employment, school, and behavioral pattern mapping.",
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors and _HAS_DDGS)

    def _build_queries(self, context: QueryContext) -> list[dict]:
        """Build investigative queries across Trace Labs categories."""
        name = context.name or ""
        city = context.city or ""
        province = context.province or ""
        queries = []
        seen = set()

        def _add(query: str, category: str, rationale: str):
            key = query.lower().strip()
            if key not in seen:
                seen.add(key)
                queries.append({"query": query, "category": category, "rationale": rationale})

        # ── Family / Friends Network ──
        for relation in ("mother", "father", "mom", "dad", "sister", "brother", "family"):
            _add(
                f'"{name}" "{relation}"',
                "family-network",
                f"Searching for {relation} connection — family members may have posted appeals or updates.",
            )
            if len(queries) >= 6:
                break

        _add(
            f'"{name}" "GoFundMe" OR "fundraiser" OR "help find"',
            "family-network",
            "Crowdfunding or community appeals often reveal family contacts and additional details.",
        )

        # ── Employment / School ──
        if context.age is not None and context.age <= 19:
            # For minors/teens — focus on schools
            _add(
                f'"{name}" "school" "{city or province}"',
                "school-employment",
                "School affiliation can reveal peer networks and likely hangout areas.",
            )
            _add(
                f'"{name}" "high school" OR "secondary school" "{city or province}"',
                "school-employment",
                "Specific school identification narrows the social circle significantly.",
            )
            # Sports teams, clubs
            _add(
                f'"{name}" "team" OR "club" OR "league" "{city or province}"',
                "school-employment",
                "Sports/club involvement reveals routines, friends, and regular locations.",
            )
        else:
            # For adults — employment focus
            _add(
                f'site:linkedin.com "{name}" "{city or province}"',
                "school-employment",
                "LinkedIn profile reveals employer, profession, and professional network.",
            )
            _add(
                f'"{name}" "works at" OR "employed" OR "employee" "{city or province}"',
                "school-employment",
                "Employment connections can reveal daily routines and commute patterns.",
            )

        # ── Day Last Seen / Timeline Advancement ──
        if context.missing_since:
            ms = context.missing_since
            date_str = ms.strftime("%B %d").replace(" 0", " ")
            month_str = ms.strftime("%B %Y")

            _add(
                f'"{name}" "{date_str}" OR "{ms.strftime("%Y-%m-%d")}"',
                "timeline-advancement",
                "Posts from the day of disappearance may contain sightings or last-known activity.",
            )
            _add(
                f'"{name}" "last seen" "{city or province}" {month_str}',
                "timeline-advancement",
                "Combining disappearance date with location for timeline-specific intelligence.",
            )

        # ── Behavioral / Hangout Locations ──
        if city:
            _add(
                f'"{name}" "{city}" "park" OR "mall" OR "bus station" OR "shelter"',
                "hangout-locations",
                "Common hangout locations in the subject's city — teens often frequent malls, parks, transit hubs.",
            )
            _add(
                f'"{name}" "{city}" "seen" OR "spotted" OR "last"',
                "sighting-trace",
                "Public sighting reports from community members.",
            )

        # ── Community Appeals & Volunteer Searches ──
        _add(
            f'"{name}" "missing" "help" OR "please" OR "share" OR "retweet"',
            "community-appeal",
            "Community appeals reveal the social circle and may contain unreported sighting details.",
        )
        if city:
            _add(
                f'"{name}" "{city}" "search party" OR "volunteer" OR "missing person"',
                "community-appeal",
                "Organized search efforts indicate community engagement and may disclose search areas.",
            )

        # ── Indigenous community context (if applicable) ──
        # Saddle Lake, for example, is a First Nation reserve
        if city:
            _add(
                f'"{name}" "{city}" "First Nation" OR "reserve" OR "band" OR "nation"',
                "community-context",
                "Indigenous community connections may reveal governance contacts and local resources.",
            )

        # ── Transportation / Travel ──
        if city:
            _add(
                f'"{name}" "bus" OR "greyhound" OR "train" OR "ride" "{city or province}"',
                "travel-pattern",
                "Transportation traces can indicate direction of travel after disappearance.",
            )

        return queries[:20]  # Hard cap

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            if not _HAS_DDGS:
                return ConnectorRunResult(
                    warning="Network analysis requires 'ddgs' package."
                )
            return ConnectorRunResult(warning="Network analysis disabled by configuration.")

        queries = self._build_queries(context)
        if not queries:
            return ConnectorRunResult(warning="No network analysis queries could be built.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()

        ddgs = DDGS()

        for q in queries:
            try:
                results = ddgs.text(q["query"], region="ca-en", max_results=8)
                added = 0
                for result in results:
                    source_url = result.get("href", "")
                    if not source_url or source_url in seen_urls:
                        continue

                    title = result.get("title", "")
                    body = result.get("body", "")

                    if q["category"] == "sighting-trace":
                        sighting_blob = f"{title} {body}".lower()
                        explicit_sighting_terms = (
                            "reported seeing", "possibly spotted", "spotted", "sighted",
                            "may have been seen", "believed to be seen", "unconfirmed sighting",
                        )
                        if not any(term in sighting_blob for term in explicit_sighting_terms):
                            continue

                    if not _name_relevant(context.name or "", title, body):
                        continue
                    if _is_adult_content(title, body):
                        continue

                    seen_urls.add(source_url)
                    added += 1

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type=q["category"],
                            category="network-behavioral",
                            source_name="DuckDuckGo",
                            source_url=source_url,
                            query_used=q["query"],
                            found_at=datetime.now(timezone.utc),
                            title=title or "Network analysis result",
                            summary=q["rationale"],
                            content_excerpt=body[:500] if body else "",
                            location_text=None,
                            source_trust=0.40,
                            rationale=[
                                f"Trace Labs technique: {q['category'].replace('-', ' ')}.",
                                q["rationale"],
                                f"Query: {q['query']}",
                            ],
                        )
                    )

                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": q["query"],
                    "status": "completed",
                    "http_status": 200,
                    "result_count": added,
                    "notes": f"Network analysis [{q['category']}] returned {len(results)} results, {added} relevant.",
                })
            except Exception as exc:
                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": q["query"],
                    "status": "failed",
                    "http_status": None,
                    "result_count": 0,
                    "notes": f"Network analysis query failed: {exc}",
                })

            await rate_limit_sleep()

        if not leads:
            return ConnectorRunResult(
                warning="No network or behavioral traces found.",
                query_logs=query_logs,
            )

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
