"""Arquivo.pt full-text web archive search connector."""

from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import re
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext


def _plain_text(value: str | None) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def arquivo_result_relevant(item: dict[str, Any], name: str) -> bool:
    """Require the archived result to contain the known multi-part subject name."""

    parts = [part.casefold() for part in re.findall(r"[\w'-]{3,}", name, flags=re.UNICODE)]
    if not parts:
        return False
    blob = " ".join(
        str(item.get(field) or "")
        for field in ("title", "snippet", "originalURL")
    ).casefold()
    return sum(part in blob for part in parts) >= min(2, len(parts))


def _parse_capture_time(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class ArquivoPtConnector:
    """Search a second full-text web archive for deleted or changed public pages."""

    metadata = ConnectorMetadata(
        name="arquivo-pt",
        source_kind="web-archive",
        disabled_by_default=True,
        description="Full-text historical page search through Arquivo.pt.",
        timeout_seconds=60,
    )

    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_clear_web_connectors)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="Arquivo.pt disabled by clear-web configuration.")
        if not context.name.strip():
            return ConnectorRunResult(warning="No case name available for archive full-text search.")

        queries = [f'"{context.name}"']
        if context.city:
            queries.append(f'"{context.name}" "{context.city}"')

        factory = self.client_factory or httpx.AsyncClient
        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen: set[str] = set()

        async with factory(timeout=settings.arquivo_timeout_seconds, follow_redirects=True) as client:
            for query in queries:
                try:
                    response = await client.get(
                        settings.arquivo_textsearch_url,
                        params={
                            "q": query,
                            "maxItems": str(settings.arquivo_max_results),
                            "prettyPrint": "false",
                        },
                        headers={"User-Agent": "MAAT/2.0 lawful-public-source-research"},
                    )
                    response.raise_for_status()
                    items = response.json().get("response_items", [])
                except Exception as exc:
                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": query,
                        "status": "failed",
                        "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                        "result_count": 0,
                        "notes": f"Arquivo.pt query failed: {exc}"[:300],
                    })
                    continue

                added = 0
                for item in items:
                    archive_url = str(item.get("linkToArchive") or "")
                    original_url = str(item.get("originalURL") or "")
                    if (
                        archive_url in seen
                        or urlsplit(archive_url).scheme not in {"http", "https"}
                        or not arquivo_result_relevant(item, context.name)
                    ):
                        continue
                    seen.add(archive_url)
                    added += 1
                    captured_at = _parse_capture_time(item.get("tstamp"))
                    title = _plain_text(item.get("title")) or original_url or "Archived public page"
                    snippet = _plain_text(item.get("snippet"))
                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind=self.metadata.source_kind,
                            lead_type="archived-page",
                            category="archive-evidence",
                            source_name="Arquivo.pt",
                            source_url=archive_url,
                            query_used=query,
                            found_at=datetime.now(timezone.utc),
                            published_at=captured_at,
                            title=f"Archived page: {title[:180]}",
                            summary=snippet[:500] or f"Historical capture of {original_url}",
                            content_excerpt=f"Original URL: {original_url}",
                            source_trust=0.48,
                            rationale=[
                                "Found through Arquivo.pt's full-text historical web index.",
                                f"Archive capture: {item.get('tstamp') or 'unknown'}.",
                                "An archived mention is historical context, not current-location proof.",
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
                    "notes": f"Arquivo.pt returned {len(items)} captures; {added} passed exact-name relevance checks.",
                })

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
