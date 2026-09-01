"""Reverse image search connector.

Supports three modes controlled by REVERSE_IMAGE_PROVIDER_MODE:

  links  (default) — Emits pre-built analyst action links for Google Images,
                     Yandex, TinEye, and Bing Visual Search.  No API key
                     required; works on any hosted backend.

  mock             — Returns entries from the bundled fixture file for
                     unit tests and demos.

  custom           — POSTs to REVERSE_IMAGE_PROVIDER_URL and normalises
                     the JSON response (self-hosted or third-party service).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata, rate_limit_sleep
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext

# ── Analyst action link templates (no scraping, no API key) ───────
_LINK_PROVIDERS: list[dict[str, str]] = [
    {
        "name": "Google Reverse Image",
        "url_template": "https://www.google.com/searchbyimage?image_url={encoded_url}&safe=active",
        "rationale": "Google Images reverse search — finds pages and social posts that reuse this photo.",
    },
    {
        "name": "Yandex Reverse Image",
        "url_template": "https://yandex.com/images/search?rpt=imageview&url={encoded_url}",
        "rationale": "Yandex reverse image search — strong coverage of Eastern European and global social platforms.",
    },
    {
        "name": "TinEye",
        "url_template": "https://tineye.com/search?url={encoded_url}",
        "rationale": "TinEye indexes billions of web images and tracks photo re-use over time.",
    },
    {
        "name": "Bing Visual Search",
        "url_template": "https://www.bing.com/images/search?q=imgurl:{encoded_url}&view=detailv2&iss=sbi",
        "rationale": "Bing Visual Search — surfaces matching images and pages across the public web.",
    },
    {
        "name": "PimEyes OSINT",
        "url_template": "https://pimeyes.com/en",
        "rationale": "PimEyes facial recognition search engine — strong for finding faces across the open web. Upload case photo manually.",
    },
    {
        "name": "FaceCheck.ID",
        "url_template": "https://facecheck.id/",
        "rationale": "FaceCheck.ID — facial recognition search across social media, news, mugshots. Upload case photo manually.",
    },
    {
        "name": "Lenso.ai",
        "url_template": "https://lenso.ai/en",
        "rationale": "Lenso.ai — AI-powered reverse image search that finds people, places, and duplicates. Upload case photo manually.",
    },
    {
        "name": "Search4faces",
        "url_template": "https://search4faces.com/",
        "rationale": "Search4faces — searches VK and OK social network photos by face. Upload case photo manually.",
    },
]


class ReverseImageConnector:
    """Reverse-image search connector.

    In *links* mode (the default when no custom provider URL is set) this
    connector generates one analyst-action lead per provider per case photo,
    pointing directly to the reverse-image search result page.  This requires
    no API key and works on any hosted backend plan.

    In *mock* mode it returns fixture data for testing.

    In *custom* mode it delegates to a self-hosted or third-party REST service.
    """

    metadata = ConnectorMetadata(
        name="reverse-image-hook",
        source_kind="clear-web",
        disabled_by_default=True,
        description=(
            "Reverse-image search leads via Google Images, Yandex, TinEye, and Bing Visual Search. "
            "Default 'links' mode requires no API key — emits pre-built analyst action links."
        ),
    )

    def __init__(
        self,
        provider_mode: str | None = None,
        provider_url: str | None = None,
        mock_file: str | Path | None = None,
    ):
        self.provider_mode = (
            provider_mode if provider_mode is not None
            else (settings.reverse_image_provider_mode or "links")
        )
        self.provider_url = (
            provider_url if provider_url is not None
            else settings.reverse_image_provider_url
        )
        resolved_mock = mock_file if mock_file is not None else settings.reverse_image_mock_file
        self.mock_file = Path(resolved_mock)

    def enabled(self) -> bool:
        return bool(settings.enable_investigator_mode and settings.enable_reverse_image_hooks)

    # ── mode: links ───────────────────────────────────────────────
    def _build_link_leads(self, image_url: str, case_name: str, found_at: datetime) -> list[NormalizedLead]:
        encoded = quote(image_url, safe="")
        leads = []
        for provider in _LINK_PROVIDERS:
            template = provider["url_template"]
            if "{encoded_url}" in template:
                search_url = template.format(encoded_url=encoded)
                summary = (
                    f"Pre-built {provider['name']} reverse image search for this case photo. "
                    "Click the link to view matching pages and re-uses of the photo."
                )
            else:
                search_url = template
                summary = (
                    f"{provider['name']} — facial recognition / reverse image tool. "
                    f"Open the link and manually upload the case photo to search. Photo URL: {image_url}"
                )
            leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind=self.metadata.source_kind,
                    lead_type="reverse-image-link",
                    category="reverse-image",
                    source_name=provider["name"],
                    source_url=search_url,
                    query_used=image_url,
                    found_at=found_at,
                    title=f"Reverse image search — {provider['name']} ({case_name})",
                    summary=summary,
                    content_excerpt=f"Photo: {image_url}",
                    source_trust=0.35,
                    rationale=[
                        provider["rationale"],
                        "Analyst action lead — open the link to view live reverse image results.",
                        "Case photo URL captured for traceability.",
                    ],
                )
            )
        return leads

    # ── mode: mock ────────────────────────────────────────────────
    def _load_mock_matches(self) -> list[dict]:
        if not self.mock_file.exists():
            return []
        return json.loads(self.mock_file.read_text(encoding="utf-8-sig"))

    def _build_mock_leads(self, image_url: str, found_at: datetime) -> list[NormalizedLead]:
        # Return all mock entries as demonstration data regardless of image_url
        leads = []
        for match in self._load_mock_matches():
            leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind=self.metadata.source_kind,
                    lead_type="reverse-image-match",
                    category="reverse-image",
                    source_name=match.get("source_name", "Mock reverse image provider"),
                    source_url=match["source_url"],
                    query_used=image_url,
                    found_at=found_at,
                    title=match.get("title", "Reverse image match (demo)"),
                    summary=match.get("summary", "Demo match from bundled fixture."),
                    content_excerpt=f"[DEMO] Case image: {image_url}",
                    published_at=(
                        datetime.fromisoformat(match["published_at"].replace("Z", "+00:00"))
                        if match.get("published_at") else None
                    ),
                    location_text=match.get("location_text"),
                    source_trust=float(match.get("source_trust", 0.3)),
                    rationale=[
                        "Demo fixture result — not a real reverse image match.",
                        *match.get("rationale", []),
                    ],
                )
            )
        return leads

    # ── mode: custom ──────────────────────────────────────────────
    async def _fetch_custom_matches(self, image_url: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=settings.connector_timeout_seconds) as client:
            response = await client.get(self.provider_url, params={"image_url": image_url})
            response.raise_for_status()
            payload = response.json()
        await rate_limit_sleep()
        return payload.get("matches", [])

    def _build_custom_leads(self, matches: list[dict], image_url: str, found_at: datetime) -> list[NormalizedLead]:
        leads = []
        for match in matches:
            leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind=self.metadata.source_kind,
                    lead_type="reverse-image-match",
                    category="reverse-image",
                    source_name=match.get("source_name", "Custom reverse image provider"),
                    source_url=match["source_url"],
                    query_used=image_url,
                    found_at=found_at,
                    title=match.get("title", "Reverse image match"),
                    summary=match.get("summary", "Public image match returned by configured provider."),
                    content_excerpt=f"Matched case image URL: {image_url}",
                    published_at=(
                        datetime.fromisoformat(match["published_at"].replace("Z", "+00:00"))
                        if match.get("published_at") else None
                    ),
                    location_text=match.get("location_text"),
                    source_trust=float(match.get("source_trust", 0.4)),
                    rationale=[
                        "Result from explicitly configured reverse-image provider.",
                        "Source URL and image query captured for analyst review.",
                        *match.get("rationale", []),
                    ],
                )
            )
        return leads

    # ── main run ──────────────────────────────────────────────────
    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="Reverse-image hooks are disabled by feature flag.")
        if not context.image_urls:
            return ConnectorRunResult(warning="No case photos available for reverse-image lookup.")

        leads: list[NormalizedLead] = []
        query_logs: list[dict] = []
        found_at = datetime.now(timezone.utc)
        case_name = context.name or "Unknown"

        for image_url in context.image_urls:
            if self.provider_mode == "links":
                batch = self._build_link_leads(image_url, case_name, found_at)
                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": image_url,
                    "status": "completed",
                    "http_status": 200,
                    "result_count": len(batch),
                    "notes": (
                        f"Generated {len(batch)} reverse-image analyst action links "
                        f"(Google, Yandex, TinEye, Bing) for case photo."
                    ),
                })
                leads.extend(batch)

            elif self.provider_mode == "mock":
                batch = self._build_mock_leads(image_url, found_at)
                query_logs.append({
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": image_url,
                    "status": "completed",
                    "http_status": 200,
                    "result_count": len(batch),
                    "notes": "Mock mode — demo fixture results.",
                })
                leads.extend(batch)

            else:
                # custom provider
                if not self.provider_url:
                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": image_url,
                        "status": "failed",
                        "result_count": 0,
                        "notes": "Custom mode selected but REVERSE_IMAGE_PROVIDER_URL is not set.",
                    })
                    continue
                try:
                    matches = await self._fetch_custom_matches(image_url)
                    batch = self._build_custom_leads(matches, image_url, found_at)
                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": image_url,
                        "status": "completed",
                        "http_status": 200,
                        "result_count": len(batch),
                        "notes": f"Custom provider returned {len(matches)} matches.",
                    })
                    leads.extend(batch)
                except Exception as exc:
                    query_logs.append({
                        "connector_name": self.metadata.name,
                        "source_kind": self.metadata.source_kind,
                        "query_used": image_url,
                        "status": "failed",
                        "result_count": 0,
                        "notes": f"Custom provider error: {exc}",
                    })

        return ConnectorRunResult(leads=leads, query_logs=query_logs)

