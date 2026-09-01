"""RCMP National Centre for Missing Persons cross-reference connector.

Searches the RCMP's public missing persons database (canadasmissing.ca) and
the RCMP case search interface to cross-reference cases from MCSC with the
national police database. This provides:

  - Archived case pages from RCMP
  - Cross-linked authority case URLs
  - Official police case reference numbers
  - Additional details not in the MCSC feed (clothing, tattoos, vehicles)
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


_RCMP_SEARCH_URL = "https://www.services.rcmp-grc.gc.ca/missing-disparus/search-recherche.jsf"
_CANADA_MISSING_URL = "https://canadasmissing.ca"


class RcmpCrossRefConnector:
    """Cross-reference cases with RCMP national missing persons database."""

    metadata = ConnectorMetadata(
        name="rcmp-ncmpur-xref",
        source_kind="official",
        disabled_by_default=True,
        description=(
            "Cross-reference cases with RCMP's National Centre for Missing Persons "
            "and Unidentified Remains (NCMPUR) database at canadasmissing.ca."
        ),
    )

    def __init__(self, client_factory: Callable[[float], Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_investigator_mode)

    def _build_queries(self, context: QueryContext) -> list[dict]:
        """Build queries for RCMP database and related official sources."""
        name = context.name or ""
        city = context.city or ""
        province = context.province or ""
        queries = []
        seen = set()

        def _add(query: str, source: str, trust: float):
            key = query.lower().strip()
            if key not in seen:
                seen.add(key)
                queries.append({"query": query, "source": source, "trust": trust})

        # RCMP case search
        _add(
            f'site:services.rcmp-grc.gc.ca/missing-disparus "{name}"',
            "RCMP Missing Persons DB",
            0.95,
        )
        _add(
            f'site:canadasmissing.ca "{name}"',
            "Canada's Missing (RCMP)",
            0.95,
        )

        # Provincial police databases
        if province:
            prov_lower = province.lower()
            if "ontario" in prov_lower:
                _add(
                    f'site:opp.ca "{name}" missing',
                    "Ontario Provincial Police",
                    0.90,
                )
            elif "quebec" in prov_lower or "québec" in prov_lower:
                _add(
                    f'site:sq.gouv.qc.ca "{name}"',
                    "Sûreté du Québec",
                    0.90,
                )
            elif "british columbia" in prov_lower:
                _add(
                    f'site:bc.rcmp-grc.gc.ca "{name}" missing',
                    "BC RCMP",
                    0.90,
                )
            elif "alberta" in prov_lower:
                _add(
                    f'site:calgarypolice.ca OR site:edmontonpolice.ca "{name}" missing',
                    "Alberta Police Services",
                    0.90,
                )

        # CrimeStoppers
        _add(
            f'site:crimestoppers.ca OR site:canadiancrimestoppers.org "{name}"',
            "Crime Stoppers Canada",
            0.85,
        )

        # Missing children registries
        _add(
            f'site:missingkids.ca "{name}"',
            "Canadian Centre for Child Protection",
            0.90,
        )

        # Police press releases
        if city:
            _add(
                f'"{name}" missing police press release "{city}"',
                "Police Press Release Search",
                0.70,
            )

        # Court records / victim services (public)
        _add(
            f'"{name}" missing persons "case number" OR "file number" OR "incident"',
            "Case Reference Search",
            0.60,
        )

        return queries

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="RCMP cross-reference connector disabled by configuration.")

        if not _HAS_DDGS:
            return ConnectorRunResult(warning="RCMP cross-ref connector requires 'ddgs' package.")

        queries = self._build_queries(context)
        name = context.name or ""

        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        seen_urls: set[str] = set()

        # Also generate direct RCMP search link as an analyst action
        if name:
            name_slug = name.lower().replace(" ", "-")
            leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind="official",
                    lead_type="official-database-link",
                    category="official-anchor",
                    source_name="RCMP NCMPUR Search",
                    source_url=f"https://www.services.rcmp-grc.gc.ca/missing-disparus/search-recherche.jsf?lang=en",
                    query_used=name,
                    found_at=datetime.now(timezone.utc),
                    title=f"RCMP Missing Persons Database — search for {name}",
                    summary=(
                        f"Direct link to the RCMP's National Centre for Missing Persons search page. "
                        f"Search for '{name}' to find official case profiles, case numbers, and additional details."
                    ),
                    content_excerpt=f"Search RCMP NCMPUR for: {name}",
                    source_trust=0.95,
                    rationale=[
                        "RCMP's official national missing persons database — highest authority source in Canada.",
                        "May contain additional details (clothing description, tattoos, vehicles) not in MCSC feed.",
                        "Analyst action — search manually for case cross-reference.",
                    ],
                )
            )

        ddgs = DDGS()

        for entry in queries[:10]:
            query = entry["query"]
            source = entry["source"]
            trust = entry["trust"]
            try:
                results = ddgs.text(query, region="ca-en", max_results=8)
                added = 0
                for result in results:
                    source_url = result.get("href", "")
                    if not source_url or source_url in seen_urls:
                        continue
                    title = result.get("title", "")
                    body = result.get("body", "")
                    # For official sources, be less strict about name matching
                    text_blob = f"{title} {body}".lower()
                    if name:
                        name_parts = [p.lower() for p in name.split() if len(p) >= 3]
                        if name_parts and not any(p in text_blob for p in name_parts):
                            continue
                    seen_urls.add(source_url)
                    added += 1

                    leads.append(
                        NormalizedLead(
                            connector_name=self.metadata.name,
                            source_kind="official" if trust >= 0.80 else "clear-web",
                            lead_type="official-xref" if trust >= 0.80 else "police-mention",
                            category="official-anchor" if trust >= 0.80 else "law-enforcement",
                            source_name=source,
                            source_url=source_url,
                            query_used=query,
                            found_at=datetime.now(timezone.utc),
                            title=title,
                            summary=body or f"Result from {source}",
                            content_excerpt=body[:500],
                            location_text=None,
                            source_trust=trust,
                            rationale=[
                                f"Found via {source} cross-reference search.",
                                "Official law enforcement source — high trust for case verification.",
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
                    "notes": f"{source}: {len(results)} results, {added} matched.",
                })
            except Exception as exc:
                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": query,
                    "status": "failed",
                    "http_status": None,
                    "result_count": 0,
                    "notes": f"RCMP cross-ref search failed: {exc}",
                })

            await rate_limit_sleep()

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
