"""Trace Labs-inspired social media profile discovery connector.

Uses site-scoped DuckDuckGo searches to locate public social media profiles,
usernames, and digital footprints across major platforms. This mirrors the
"Basic Subject Info" and "Social Media" categories in Trace Labs CTFs.

Techniques:
  - Platform-scoped name searches (site:instagram.com "Name")
  - Username enumeration from discovered handles
  - Cross-platform pivot (handle found on one platform searched on others)
  - Location-anchored social searches ("Name" "City" site:platform)
  - Age-contextual searches (school, grade, graduation year)
"""

from __future__ import annotations

import re
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

# Platforms ordered by investigative value for missing persons
_PLATFORMS = [
    {"domain": "instagram.com", "label": "Instagram", "handle_pattern": r"instagram\.com/([A-Za-z0-9_.]+)"},
    {"domain": "tiktok.com", "label": "TikTok", "handle_pattern": r"tiktok\.com/@([A-Za-z0-9_.]+)"},
    {"domain": "facebook.com", "label": "Facebook", "handle_pattern": r"facebook\.com/([A-Za-z0-9_.]+)"},
    {"domain": "snapchat.com", "label": "Snapchat", "handle_pattern": r"snapchat\.com/add/([A-Za-z0-9_.]+)"},
    {"domain": "twitter.com", "label": "Twitter/X", "handle_pattern": r"(?:twitter|x)\.com/([A-Za-z0-9_]+)"},
    {"domain": "youtube.com", "label": "YouTube", "handle_pattern": r"youtube\.com/(?:@|channel/|user/)([A-Za-z0-9_.-]+)"},
    {"domain": "linkedin.com", "label": "LinkedIn", "handle_pattern": r"linkedin\.com/in/([A-Za-z0-9_-]+)"},
]

_ADULT_KEYWORDS = {
    "porn", "pornstar", "xxx", "onlyfans", "escort", "adult film",
    "webcam model", "foxy reviews", "nylon-queens",
}


def _is_adult_content(title: str, body: str) -> bool:
    text_blob = f"{title} {body}".lower()
    return any(kw in text_blob for kw in _ADULT_KEYWORDS)


def _name_relevant(name: str, title: str, body: str) -> bool:
    """Require at least one substantial name part in the result."""
    if not name:
        return True
    text_blob = f"{title} {body}".lower()
    name_parts = [p.lower() for p in name.split() if len(p) >= 3]
    if not name_parts:
        return True
    return sum(1 for p in name_parts if p in text_blob) >= min(2, len(name_parts))


def _extract_handles(url: str, title: str, body: str) -> list[str]:
    """Extract social media handles/usernames from URL and text."""
    handles = []
    combined = f"{url} {title} {body}"
    for platform in _PLATFORMS:
        for match in re.finditer(platform["handle_pattern"], combined, re.IGNORECASE):
            handle = match.group(1).strip("./")
            if handle and len(handle) > 1 and handle.lower() not in {
                "p", "reel", "stories", "explore", "watch", "channel", "user",
                "story.php", "photo.php", "permalink.php", "groups", "pages",
                "marketplace", "events", "profile.php", "hashtag", "login",
                "share", "sharer", "sharer.php", "dialog", "plugins",
            }:
                handles.append(handle)
    return list(set(handles))


class SocialProfilerConnector:
    """Trace Labs-style social media profile discovery via DuckDuckGo."""

    metadata = ConnectorMetadata(
        name="social-profiler",
        source_kind="clear-web",
        disabled_by_default=True,
        description="Social media profile enumeration using site-scoped searches (Trace Labs technique).",
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors and _HAS_DDGS)

    def _build_queries(self, context: QueryContext) -> list[dict]:
        """Build platform-scoped queries following Trace Labs methodology."""
        name = context.name or ""
        city = context.city or ""
        province = context.province or ""
        queries = []
        seen = set()

        def _add(query: str, category: str, platform: str):
            key = query.lower().strip()
            if key not in seen:
                seen.add(key)
                queries.append({"query": query, "category": category, "platform": platform})

        # Phase 1: Direct platform profile searches
        for plat in _PLATFORMS[:5]:  # Top 5 platforms
            _add(
                f'site:{plat["domain"]} "{name}"',
                "profile-discovery",
                plat["label"],
            )
            if city:
                _add(
                    f'site:{plat["domain"]} "{name}" "{city}"',
                    "profile-discovery",
                    plat["label"],
                )

        # Phase 2: Username-style queries (first.last, firstlast, first_last)
        name_parts = [p.lower() for p in name.split() if len(p) >= 2]
        if len(name_parts) >= 2:
            username_variants = [
                f"{name_parts[0]}{name_parts[-1]}",
                f"{name_parts[0]}.{name_parts[-1]}",
                f"{name_parts[0]}_{name_parts[-1]}",
                f"{name_parts[0]}{name_parts[-1][0]}",  # kaisa.m
            ]
            for variant in username_variants[:3]:
                _add(
                    f'"{variant}" site:instagram.com OR site:tiktok.com',
                    "username-enumeration",
                    "multi-platform",
                )

        # Phase 3: Network mapping — friends, tags, mentions
        for plat in _PLATFORMS[:3]:
            if city:
                _add(
                    f'site:{plat["domain"]} "{name}" "tagged" OR "with" OR "@"',
                    "network-mapping",
                    plat["label"],
                )

        # Phase 4: Age-contextual — school, grade
        if context.age is not None:
            # Estimate graduation year for school searches
            grad_year = None
            if 13 <= context.age <= 18:
                years_to_grad = 18 - context.age
                grad_year = datetime.now().year + years_to_grad

            if grad_year:
                _add(
                    f'"{name}" "class of {grad_year}" OR "grad {grad_year}"',
                    "school-trace",
                    "web",
                )
            _add(
                f'"{name}" "school" "{city or province}"',
                "school-trace",
                "web",
            )

        return queries[:25]  # Hard cap

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            if not _HAS_DDGS:
                return ConnectorRunResult(
                    warning="Social profiler requires 'ddgs' package."
                )
            return ConnectorRunResult(warning="Social profiler disabled by configuration.")

        queries = self._build_queries(context)
        if not queries:
            return ConnectorRunResult(warning="No social profile queries could be built.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        discovered_handles: set[str] = set()

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

                    if not _name_relevant(context.name or "", title, body):
                        continue
                    if _is_adult_content(title, body):
                        continue

                    seen_urls.add(source_url)
                    added += 1

                    # Extract handles for cross-platform pivot
                    handles = _extract_handles(source_url, title, body)
                    discovered_handles.update(handles)

                    # Determine lead type based on query category
                    lead_type = "social-profile"
                    if q["category"] == "username-enumeration":
                        lead_type = "username-match"
                    elif q["category"] == "network-mapping":
                        lead_type = "network-connection"
                    elif q["category"] == "school-trace":
                        lead_type = "school-connection"

                    rationale = [
                        f"Trace Labs technique: {q['category'].replace('-', ' ')}.",
                        f"Platform: {q['platform']}.",
                        f"Query: {q['query']}",
                    ]
                    if handles:
                        rationale.append(f"Discovered handle(s): {', '.join(handles[:3])}")

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type=lead_type,
                            category="social-media-trace",
                            source_name=q["platform"],
                            source_url=source_url,
                            query_used=q["query"],
                            found_at=datetime.now(timezone.utc),
                            title=title or "Social media result",
                            summary=f"{q['platform']} profile/mention — {q['category']}",
                            content_excerpt=body[:500] if body else "",
                            location_text=None,
                            source_trust=0.40,
                            rationale=rationale,
                        )
                    )

                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": q["query"],
                    "status": "completed",
                    "http_status": 200,
                    "result_count": added,
                    "notes": f"Social profiler [{q['category']}] returned {len(results)} results, {added} relevant.",
                })
            except Exception as exc:
                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": q["query"],
                    "status": "failed",
                    "http_status": None,
                    "result_count": 0,
                    "notes": f"Social profiler query failed: {exc}",
                })

            await rate_limit_sleep()

        # Phase 5: Cross-platform pivot — search discovered handles on other platforms
        if discovered_handles:
            pivot_count = 0
            for handle in list(discovered_handles)[:3]:
                for plat in _PLATFORMS[:3]:
                    if pivot_count >= 6:
                        break
                    pivot_query = f'site:{plat["domain"]} "{handle}"'
                    try:
                        results = ddgs.text(pivot_query, region="ca-en", max_results=5)
                        for result in results:
                            source_url = result.get("href", "")
                            if not source_url or source_url in seen_urls:
                                continue
                            title = result.get("title", "")
                            body = result.get("body", "")
                            if _is_adult_content(title, body):
                                continue

                            seen_urls.add(source_url)
                            pivot_count += 1

                            leads.append(
                                NormalizedLead(
                                    connector_name=self.metadata.name,
                                    source_kind=self.metadata.source_kind,
                                    lead_type="cross-platform-pivot",
                                    category="social-media-trace",
                                    source_name=plat["label"],
                                    source_url=source_url,
                                    query_used=pivot_query,
                                    found_at=datetime.now(timezone.utc),
                                    title=title or "Cross-platform handle match",
                                    summary=f"Handle @{handle} found on {plat['label']}",
                                    content_excerpt=body[:500] if body else "",
                                    location_text=None,
                                    source_trust=0.35,
                                    rationale=[
                                        f"Cross-platform pivot: handle @{handle} discovered on {plat['label']}.",
                                        f"Original handle found during social profiling phase.",
                                        f"Query: {pivot_query}",
                                    ],
                                )
                            )

                        query_logs.append({
                            "connector_name": self.metadata.name,
                            "source_kind": self.metadata.source_kind,
                            "query_used": pivot_query,
                            "status": "completed",
                            "http_status": 200,
                            "result_count": pivot_count,
                            "notes": f"Cross-platform pivot for @{handle} on {plat['label']}.",
                        })
                    except Exception:
                        pass

                    await rate_limit_sleep()

        if not leads:
            return ConnectorRunResult(
                warning="No social media profiles or traces found.",
                query_logs=query_logs,
            )

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
