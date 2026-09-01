"""Wayback Machine CDX connector — checks Internet Archive for archived pages.

Uses the public CDX API to find archived snapshots of official missing-person
pages, news articles, and social media posts. No API key required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext


def _parse_wayback_timestamp(ts: str) -> datetime | None:
    """Parse a Wayback Machine timestamp (YYYYMMDDHHmmss) into datetime."""
    try:
        return datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_archivable_page(url: str | None) -> bool:
    """Accept public page URLs while excluding feeds and API endpoints."""

    if not url:
        return False
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    lowered = url.lower()
    return not any(
        marker in lowered
        for marker in ("arcgis.com", "featureserver", "/rest/services/", "/api/")
    )


class WaybackMachineConnector:
    """Check the Internet Archive for archived pages related to missing persons."""

    metadata = ConnectorMetadata(
        name="wayback-machine",
        source_kind="clear-web",
        disabled_by_default=True,
        description="Search the Internet Archive's Wayback Machine for archived evidence.",
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="Wayback Machine connector disabled by configuration.")

        # Build URL patterns to check
        urls_to_check: list[tuple[str, str]] = []

        # Build name-based domain searches (CDX works best with domain patterns)
        name = (context.name or "").strip()
        name_slug = name.lower().replace(" ", "").replace("-", "")
        name_hyphen = name.lower().replace(" ", "-")

        if name:
            # Search canadasmissing.ca and missingkids.ca for the person
            urls_to_check.append(
                (f"canadasmissing.ca/pubs/*{name_hyphen}*", "rcmp-missing-db")
            )
            urls_to_check.append(
                (f"missingkids.ca/*{name_hyphen}*", "cccp-missing-db")
            )

        # Check every source-backed case page, not only the authority URL. This
        # lets the archive sweep recover deleted public appeals and news pages.
        for url in [context.authority_case_url, *context.source_urls]:
            if _is_archivable_page(url):
                urls_to_check.append((url, "case-source-page"))

        # Preserve order while avoiding repeated CDX calls for the same source.
        urls_to_check = list(dict.fromkeys(urls_to_check))

        if not urls_to_check:
            return ConnectorRunResult(warning="No URLs to check against the Wayback Machine.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        factory = self.client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout, follow_redirects=True))

        async with factory(settings.connector_timeout_seconds) as client:
            for check_url, url_type in urls_to_check[:8]:
                try:
                    params: dict[str, str] = {
                        "url": check_url,
                        "output": "json",
                        "limit": "10",
                        "filter": "statuscode:200",
                        "fl": "timestamp,original,mimetype,statuscode",
                        "sort": "reverse",
                        "collapse": "digest",
                    }
                    response = await client.get(
                        "https://web.archive.org/cdx/search/cdx",
                        params=params,
                        headers={
                            "User-Agent": "maat-intelligence/2.0 (research)",
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                except Exception as exc:
                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": check_url,
                        "status": "failed",
                        "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                        "result_count": 0,
                        "notes": f"Wayback Machine CDX query failed: {exc}",
                    })
                    continue

                await rate_limit_sleep()

                # Skip header row
                rows = data[1:] if len(data) > 1 else []
                added = 0

                for row in rows:
                    if len(row) < 4:
                        continue
                    timestamp, original_url, mimetype, statuscode = row[:4]

                    wayback_url = f"https://web.archive.org/web/{timestamp}/{original_url}"
                    if wayback_url in seen_urls:
                        continue
                    seen_urls.add(wayback_url)
                    added += 1

                    published_at = _parse_wayback_timestamp(timestamp)

                    # Determine trust based on URL type
                    trust = 0.50
                    if url_type in {"official-case-page", "case-source-page"}:
                        trust = 0.70
                    elif url_type in ("rcmp-missing-db", "cccp-missing-db"):
                        trust = 0.65
                    elif url_type == "advocacy-site":
                        trust = 0.55

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type="archived-page",
                            category="archive-evidence",
                            source_name="Internet Archive",
                            source_url=wayback_url,
                            query_used=check_url,
                            found_at=datetime.now(timezone.utc),
                            published_at=published_at,
                            title=f"Archived snapshot of {url_type}: {original_url[:80]}",
                            summary=f"Wayback Machine snapshot from {timestamp[:8]} of {original_url}",
                            content_excerpt=f"Archived {mimetype} page captured on {timestamp[:8]}. "
                                          f"Original URL: {original_url}",
                            location_text=None,
                            source_trust=trust,
                            rationale=[
                                f"Archived evidence from Internet Archive ({url_type}).",
                                f"Snapshot captured: {timestamp[:8]}",
                                "Archived pages provide historical evidence even if originals are removed.",
                            ],
                        )
                    )

                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": check_url,
                    "status": "completed",
                    "http_status": response.status_code,
                    "result_count": added,
                    "notes": f"Wayback Machine found {len(rows)} snapshots, {added} new.",
                })

        if not leads:
            return ConnectorRunResult(
                warning="No archived pages found in the Wayback Machine for this case.",
                query_logs=query_logs,
            )

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
