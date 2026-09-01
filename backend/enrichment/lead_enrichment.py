"""Turn promising search results into source-backed location intelligence."""

from __future__ import annotations

import asyncio
import unicodedata
from urllib.parse import urlsplit

from backend.core.config import settings
from backend.enrichment.content_extraction import fetch_and_extract, resolve_google_news_url
from backend.enrichment.geocoding import geocode_public_place
from backend.models.case import Case
from backend.osint.normalization.models import NormalizedLead
from backend.osint.scoring.lead_scoring import score_lead


_ARTICLE_TYPES = {"news-article", "web-mention", "archived-page", "sighting-trace", "community-discussion"}
_MISSING_CONTEXT = (
    "missing",
    "disappeared",
    "last seen",
    "search for",
    "help find",
    "sighting",
    "disparu",
    "disparue",
    "disparition",
    "fugue",
    "derniere fois vue",
    "recherche",
)
_SKIP_HOSTS = {
    "amazon.com",
    "ebay.com",
    "etsy.com",
    "goodreads.com",
    "imdb.com",
    "linkedin.com",
    "pinterest.com",
    "researchgate.net",
    "wikipedia.org",
}


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return " ".join("".join(ch for ch in normalized if not unicodedata.combining(ch)).lower().split())


def _subject_matches(text: str, subject_name: str) -> bool:
    folded_text = _fold(text)
    parts = [part for part in _fold(subject_name).replace("-", " ").split() if len(part) >= 2]
    if not parts:
        return False
    name_match = all(part in folded_text for part in parts)
    return name_match and any(term in folded_text for term in _MISSING_CONTEXT)


def _candidate(lead: NormalizedLead) -> bool:
    if lead.lead_type not in _ARTICLE_TYPES or not lead.source_url:
        return False
    try:
        host = urlsplit(lead.source_url).hostname or ""
    except ValueError:
        return False
    host = host.lower().removeprefix("www.")
    return not any(host == skipped or host.endswith(f".{skipped}") for skipped in _SKIP_HOSTS)


def _append_unique(lead: NormalizedLead, reason: str) -> None:
    if reason and reason not in lead.rationale:
        lead.rationale.append(reason)


async def _enrich_one(case: Case, lead: NormalizedLead) -> bool:
    if lead.source_url.startswith("https://news.google.com/rss/"):
        resolved = await resolve_google_news_url(lead.title, subject_name=case.name or "")
        if resolved:
            lead.source_url = resolved
        else:
            return False

    detail = await fetch_and_extract(
        lead.source_url,
        subject_name=case.name or "",
        timeout=settings.article_fetch_timeout_seconds,
    )
    if detail is None or not _subject_matches(detail.raw_text, case.name or ""):
        return False

    _append_unique(lead, "Article content matched the subject name and missing-person context.")
    if detail.key_facts:
        lead.content_excerpt = detail.key_facts[0][:700]
    is_case_origin = bool(
        detail.sighting_location
        and case.city
        and _fold(detail.sighting_location) == _fold(case.city)
    )
    if detail.sighting_location:
        lead.location_text = detail.sighting_location
        if is_case_origin:
            _append_unique(lead, f"Machine-extracted case-origin location: {detail.sighting_location}")
        else:
            _append_unique(lead, f"Machine-extracted sighting location (unverified): {detail.sighting_location}")
    if detail.sighting_date:
        _append_unique(lead, f"Machine-extracted date mention (unverified): {detail.sighting_date}")
    if detail.mentioned_locations:
        locations = ", ".join(detail.mentioned_locations[:5])
        _append_unique(lead, f"Machine-extracted location mentions (unverified): {locations}")
    if detail.physical_description:
        _append_unique(lead, f"Physical description: {'; '.join(detail.physical_description[:3])}")
    if detail.clothing_description:
        _append_unique(lead, f"Clothing: {detail.clothing_description[0][:180]}")
    if detail.direction_of_travel:
        _append_unique(lead, f"Direction: {detail.direction_of_travel[:180]}")
    if detail.vehicle_description:
        _append_unique(lead, f"Vehicle: {detail.vehicle_description}")
    if detail.licence_plate:
        _append_unique(lead, f"Licence plate: {detail.licence_plate}")

    if detail.sighting_location and not is_case_origin:
        geocode = await geocode_public_place(detail.sighting_location, case.province)
        if geocode:
            lead.latitude = geocode.latitude
            lead.longitude = geocode.longitude
            _append_unique(lead, f"Geocode precision: {geocode.precision}")
            _append_unique(lead, f"Geocoded public place: {geocode.display_name} ({geocode.provider})")

    return True


async def enrich_location_leads(case: Case, leads: list[NormalizedLead]) -> int:
    """Enrich the highest-relevance article leads and return the success count."""

    candidates = [lead for lead in leads if _candidate(lead)]
    candidates.sort(key=lambda lead: score_lead(case, lead).score, reverse=True)
    candidates = candidates[: settings.max_enrichment_articles]
    semaphore = asyncio.Semaphore(max(1, settings.enrichment_concurrency))

    async def bounded(lead: NormalizedLead) -> bool:
        async with semaphore:
            try:
                return await _enrich_one(case, lead)
            except Exception:
                return False

    if not candidates:
        return 0
    return sum(await asyncio.gather(*(bounded(lead) for lead in candidates)))
