"""Connector that checks public Canadian missing-person databases and police feeds."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext


CANADA_MISSING_SEARCH = "https://www.canadasmissing.ca/pubs/search-recherche.asp"
RCMP_MISSING_URL = "https://www.rcmp-grc.gc.ca/en/missing-persons"


class CanadaMissingConnector:
    """Cross-check against Canada's Missing and RCMP public pages via Google News RSS."""

    metadata = ConnectorMetadata(
        name="canada-missing-xref",
        source_kind="official",
        disabled_by_default=False,
        description="Cross-references the case against official Canadian missing-person portals and police social media.",
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_investigator_mode)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="Canada Missing cross-reference connector disabled.")
        if not context.name:
            return ConnectorRunResult(warning="No name available for cross-reference.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        factory = self.client_factory or (lambda timeout: httpx.AsyncClient(timeout=timeout, follow_redirects=True))
        found_at = datetime.now(timezone.utc)

        # Build cross-reference queries using Google News RSS for official sites
        xref_queries = [
            (f'site:canadasmissing.ca "{context.name}"', "Canada's Missing (RCMP)"),
            (f'site:missingkids.ca "{context.name}"', "Canadian Centre for Child Protection"),
            (f'site:facebook.com RCMP "{context.name}" missing', "RCMP Facebook"),
        ]
        if context.city:
            xref_queries.append(
                (f'"{context.name}" missing "{context.city}" site:cbc.ca OR site:globalnews.ca OR site:ctv.ca', "Canadian News Media")
            )
        if context.authority_name:
            xref_queries.append(
                (f'"{context.name}" "{context.authority_name}" missing', "Authority Media Coverage")
            )

        async with factory(settings.connector_timeout_seconds) as client:
            for query, source_label in xref_queries:
                rss_params = {
                    "q": query,
                    "hl": "en-CA",
                    "gl": "CA",
                    "ceid": "CA:en",
                }
                rss_url = f"https://news.google.com/rss/search?{urlencode(rss_params)}"

                try:
                    response = await client.get(
                        rss_url,
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
                        "notes": f"Cross-reference query failed: {exc}",
                    })
                    continue

                await rate_limit_sleep()

                try:
                    from xml.etree import ElementTree
                    from email.utils import parsedate_to_datetime

                    root = ElementTree.fromstring(raw_xml)
                    items = root.findall(".//item")[:5]
                except Exception:
                    items = []

                added = 0
                seen_urls: set[str] = set()
                for item in items:
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    pub_date_str = item.findtext("pubDate")

                    if not link or link in seen_urls:
                        continue
                    seen_urls.add(link)
                    added += 1

                    published_at = None
                    if pub_date_str:
                        try:
                            from email.utils import parsedate_to_datetime
                            published_at = parsedate_to_datetime(pub_date_str).astimezone(timezone.utc)
                        except Exception:
                            pass

                    # Determine if it's an official source
                    is_official = any(
                        domain in link.lower()
                        for domain in ("canadasmissing.ca", "missingkids.ca", "rcmp", "police", "grc.gc.ca")
                    )
                    trust = 0.9 if is_official else 0.65

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind="official" if is_official else "clear-web",
                            lead_type="official-cross-reference" if is_official else "news-article",
                            category="official-cross-check" if is_official else "news-monitoring",
                            source_name=source_label,
                            source_url=link,
                            query_used=query,
                            found_at=found_at,
                            title=title or f"Cross-reference result for {context.name}",
                            summary=f"Cross-reference from {source_label}",
                            content_excerpt=title,
                            published_at=published_at,
                            location_text=None,
                            source_trust=trust,
                            rationale=[
                                f"Found through cross-referencing {source_label}.",
                                f"Query: {query}",
                                "Official or media cross-reference for corroboration." if is_official else "Media coverage cross-reference.",
                            ],
                        )
                    )

                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": query,
                    "status": "completed",
                    "http_status": response.status_code if "response" in dir() else None,
                    "result_count": added,
                    "notes": f"{source_label}: {added} results found.",
                })

        # Also generate direct portal links as leads
        portal_leads = [
            NormalizedLead(
                connector_name=self.metadata.name,
                source_kind="official",
                lead_type="portal-link",
                category="official-cross-check",
                source_name="Canada's Missing",
                source_url=f"https://www.canadasmissing.ca/pubs/search-recherche.asp",
                query_used=context.name,
                found_at=found_at,
                title=f"Search Canada's Missing for {context.name}",
                summary="Direct link to the RCMP-managed Canada's Missing portal.",
                content_excerpt=f"Search for {context.name} on Canada's Missing portal",
                location_text=context.province,
                source_trust=1.0,
                rationale=["Direct official RCMP-managed national missing persons database."],
            ),
        ]

        if context.authority_case_url:
            portal_leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind="official",
                    lead_type="authority-post",
                    category="official-anchor",
                    source_name=context.authority_name or "Police Authority",
                    source_url=context.authority_case_url,
                    query_used=context.authority_case_url,
                    found_at=found_at,
                    title=f"Official authority post for {context.name}",
                    summary=f"Direct link to the authority's public case posting ({context.authority_name}).",
                    content_excerpt=context.authority_case_url,
                    published_at=context.missing_since,
                    location_text=None,
                    source_trust=1.0,
                    rationale=["Direct authority-managed case URL from MCSC data."],
                )
            )

        leads.extend(portal_leads)

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
