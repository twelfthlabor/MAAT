"""Bounded live checks using WebBreacher's upstream WhatsMyName database."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import re
from typing import Any, Callable
from urllib.parse import quote, urlsplit

import httpx

from backend.core.config import settings
from backend.osint.connectors.base import ConnectorMetadata
from backend.osint.connectors.sherlock import build_username_candidates
from backend.osint.normalization.models import ConnectorRunResult, NormalizedLead, QueryContext


_BLOCKED_CATEGORIES = {"adult", "dating", "porn", "xxx"}
_BLOCKED_PROTECTIONS = {"captcha", "cloudflare", "multiple"}
_PREFERRED_CATEGORIES = {"social", "tech", "gaming", "music", "video", "photo", "blog"}


def select_checkable_sites(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Choose high-signal checks with positive fingerprints and no known challenge."""

    selected: list[dict[str, Any]] = []
    for site in payload.get("sites", []):
        template = str(site.get("uri_check") or "")
        category = str(site.get("cat") or "").lower()
        protections = site.get("protection") or []
        if isinstance(protections, str):
            protections = [protections]
        protection_set = {str(item).lower() for item in protections}
        if (
            "{account}" not in template
            or not template.startswith(("http://", "https://"))
            or not site.get("e_string")
            or category in _BLOCKED_CATEGORIES
            or protection_set & _BLOCKED_PROTECTIONS
        ):
            continue
        selected.append(site)

    selected.sort(
        key=lambda site: (
            str(site.get("cat") or "").lower() not in _PREFERRED_CATEGORIES,
            str(site.get("name") or "").lower(),
        )
    )
    return selected[: max(1, limit)]


def response_claims_username(site: dict[str, Any], status_code: int, body: str) -> bool:
    """Apply the upstream positive status and content fingerprint."""

    try:
        expected_code = int(site.get("e_code"))
    except (TypeError, ValueError):
        return False
    expected = str(site.get("e_string") or "")
    if not expected or status_code != expected_code:
        return False
    return expected.casefold() in body.casefold()


class WhatsMyNameConnector:
    """Check URL-backed or bounded username hypotheses against live profile pages."""

    metadata = ConnectorMetadata(
        name="whatsmyname",
        source_kind="public-profile",
        disabled_by_default=True,
        description="Live username checks using WhatsMyName's maintained site fingerprints.",
        timeout_seconds=65,
    )

    def __init__(self, client_factory: Callable[..., Any] | None = None) -> None:
        self.client_factory = client_factory

    def enabled(self) -> bool:
        return bool(settings.enable_public_profile_checks)

    async def run(self, context: QueryContext) -> ConnectorRunResult:
        if not self.enabled():
            return ConnectorRunResult(warning="WhatsMyName disabled by public-profile configuration.")

        usernames = build_username_candidates(context)[: settings.whatsmyname_max_usernames]
        if not usernames:
            return ConnectorRunResult(warning="No bounded usernames available for WhatsMyName checks.")

        factory = self.client_factory or httpx.AsyncClient
        leads: list[NormalizedLead] = []
        query_logs: list[dict[str, object]] = []
        headers = {"User-Agent": "MAAT/2.0 lawful-public-source-research"}
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

        try:
            async with factory(
                timeout=settings.whatsmyname_timeout_seconds,
                follow_redirects=True,
                headers=headers,
                limits=limits,
            ) as client:
                database_response = await client.get(settings.whatsmyname_data_url)
                database_response.raise_for_status()
                sites = select_checkable_sites(
                    database_response.json(),
                    settings.whatsmyname_max_sites,
                )
                semaphore = asyncio.Semaphore(8)

                async def check(site: dict[str, Any], username: str) -> tuple[str, dict[str, Any], str, int | None, bool]:
                    url = str(site["uri_check"]).replace("{account}", quote(username, safe=""))
                    async with semaphore:
                        try:
                            response = await client.get(url)
                            claimed = response_claims_username(site, response.status_code, response.text)
                            return username, site, url, response.status_code, claimed
                        except Exception:
                            return username, site, url, None, False

                results = await asyncio.gather(
                    *(check(site, username) for username in usernames for site in sites)
                )
        except Exception as exc:
            return ConnectorRunResult(
                warning=f"WhatsMyName database or live checks failed: {exc}",
                query_logs=[{
                    "connector_name": self.metadata.name,
                    "source_kind": self.metadata.source_kind,
                    "query_used": ", ".join(usernames),
                    "status": "failed",
                    "http_status": None,
                    "result_count": 0,
                    "notes": str(exc)[:300],
                }],
            )

        counts = {username: 0 for username in usernames}
        seen_urls: set[str] = set()
        for username, site, url, _, claimed in results:
            if not claimed or url in seen_urls:
                continue
            seen_urls.add(url)
            counts[username] += 1
            site_name = str(site.get("name") or urlsplit(url).netloc)
            leads.append(
                NormalizedLead(
                    connector_name=self.metadata.name,
                    source_kind=self.metadata.source_kind,
                    lead_type="username-account-candidate",
                    category="username-enumeration",
                    source_name=site_name,
                    source_url=url,
                    query_used=username,
                    found_at=datetime.now(timezone.utc),
                    title=f"Unverified @{username} profile candidate on {site_name}",
                    summary=(
                        f"WhatsMyName's live positive fingerprint matched @{username} on {site_name}. "
                        "A matching handle is a pivot, not proof that the account belongs to the subject."
                    ),
                    source_trust=0.28,
                    rationale=[
                        "Checked live using WebBreacher's maintained WhatsMyName fingerprint database.",
                        "The upstream positive status and page-content fingerprint both matched.",
                        "Manually compare public profile facts before treating this as the same person.",
                    ],
                )
            )

        for username in usernames:
            query_logs.append({
                "connector_name": self.metadata.name,
                "source_kind": self.metadata.source_kind,
                "query_used": username,
                "status": "completed",
                "http_status": 200,
                "result_count": counts[username],
                "notes": f"Bounded live checks against {len(sites)} high-signal WhatsMyName site fingerprints.",
            })

        return ConnectorRunResult(leads=leads, query_logs=query_logs)
