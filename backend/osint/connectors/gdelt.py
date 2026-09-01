"""Passive news/timeline connector backed by GDELT DOC 2.0."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext
from backend.osint.query_planner import build_news_query_plan


def _parse_gdelt_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _exception_note(prefix: str, exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return f"{prefix}: {detail}"


class GdeltDocConnector:
    """Public news connector using the GDELT DOC 2.0 ArtList endpoint."""

    metadata = ConnectorMetadata(
        name="gdelt-doc",
        source_kind="clear-web",
        disabled_by_default=True,
        description="Passive news/timeline searches through the GDELT DOC 2.0 article API.",
    )

    def __init__(
        self,
        api_url: str | None = None,
        client_factory: Callable[[float], Any] | None = None,
    ) -> None:
        self.api_url = api_url if api_url is not None else settings.gdelt_doc_api_url
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors and self.api_url)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="GDELT DOC connector disabled by configuration.")

        # Build quote-wrapped queries for better precision
        query_plan = []
        name = context.name or ""
        if name:
            query_plan.append(f'"{name}" missing')
            if context.city:
                query_plan.append(f'"{name}" {context.city}')
            if context.province:
                query_plan.append(f'"{name}" {context.province}')
        if not query_plan:
            query_plan = build_news_query_plan(context, limit=3)
        if not query_plan:
            return ConnectorRunResult(warning="No reviewable news/timeline queries could be built from the case facts.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        connector_warning: str | None = None
        factory = self.client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout))

        async with factory(settings.connector_timeout_seconds) as client:
            for query in query_plan:
                params = {
                    "query": query,
                    "mode": "ArtList",
                    "format": "json",
                    "maxrecords": "10",
                    "sort": "DateDesc",
                    "timespan": "5years",
                }
                request_url = f"{self.api_url}?{urlencode(params)}"

                try:
                    response = await client.get(
                        self.api_url,
                        params=params,
                        headers={
                            "Accept": "application/json",
                            "User-Agent": "maat-intelligence/2.0",
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPStatusError as exc:
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    query_logs.append(
                        {
                            "connector_name": self.metadata.name,
                            "source_kind": self.metadata.source_kind,
                            "query_used": query,
                            "status": "failed",
                            "http_status": status_code,
                            "result_count": 0,
                            "notes": _exception_note("GDELT query failed", exc),
                        }
                    )
                    if status_code == 429:
                        connector_warning = "GDELT DOC rate-limited the connector. Use the dashboard's manual news launchers for this case."
                        break
                    continue
                except ValueError as exc:
                    query_logs.append(
                        {
                            "connector_name": self.metadata.name,
                            "source_kind": self.metadata.source_kind,
                            "query_used": query,
                            "status": "failed",
                            "http_status": response.status_code if "response" in locals() else None,
                            "result_count": 0,
                            "notes": _exception_note("GDELT returned a non-JSON response", exc),
                        }
                    )
                    continue
                except Exception as exc:
                    query_logs.append(
                        {
                            "connector_name": self.metadata.name,
                            "source_kind": self.metadata.source_kind,
                            "query_used": query,
                            "status": "failed",
                            "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                            "result_count": 0,
                            "notes": _exception_note("GDELT query failed", exc),
                        }
                    )
                    continue

                await rate_limit_sleep()
                raw_articles = payload.get("articles", [])[:10]
                added = 0
                for article in raw_articles:
                    source_url = str(article.get("url") or "").strip()
                    if not source_url or source_url in seen_urls:
                        continue

                    seen_urls.add(source_url)
                    added += 1
                    published_at = _parse_gdelt_datetime(article.get("seendate") or article.get("date"))
                    # GDELT's source country is not the subject's location.
                    # Do not present it (or the case city) as a sighting.
                    location_text = None
                    source_name = article.get("domain") or article.get("sourcecollection") or "GDELT DOC 2.0"
                    summary_bits = [
                        article.get("domain"),
                        article.get("language"),
                        f"Seen {published_at.date().isoformat()}" if published_at else None,
                    ]
                    summary = " | ".join(bit for bit in summary_bits if bit) or "Public news match surfaced through GDELT DOC 2.0."

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type="news-article",
                            category="news-monitoring",
                            source_name=source_name,
                            source_url=source_url,
                            query_used=query,
                            found_at=datetime.now(timezone.utc),
                            title=article.get("title") or "Untitled news result",
                            summary=summary,
                            content_excerpt=article.get("title") or "",
                            published_at=published_at,
                            location_text=location_text,
                            source_trust=0.55,
                            rationale=[
                                "Matched through the GDELT DOC 2.0 article API.",
                                "News queries are bounded to case facts and timeline pivots.",
                                f"Review the source URL directly before treating the article as corroboration: {request_url}",
                            ],
                        )
                    )

                query_logs.append(
                    {
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": query,
                        "status": "completed",
                        "http_status": response.status_code,
                        "result_count": added,
                        "notes": f"GDELT ArtList returned {len(raw_articles)} raw article(s) before dedupe.",
                    }
                )

        if connector_warning is None and not leads:
            connector_warning = "No GDELT news articles matched the current bounded queries for this case."

        return ConnectorRunResult(leads=leads, query_logs=query_logs, warning=connector_warning)
