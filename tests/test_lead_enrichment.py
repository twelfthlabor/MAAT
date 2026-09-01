import asyncio
from datetime import datetime, timezone

from backend.enrichment.content_extraction import SightingDetail
from backend.enrichment.geocoding import GeocodeResult
from backend.enrichment.lead_enrichment import _subject_matches, enrich_location_leads
from backend.models.case import Case
from backend.osint.normalization.models import NormalizedLead


def _case() -> Case:
    return Case(
        id=99,
        slug="rosalie-naess-leclerc",
        name="Rosalie Naess-Leclerc",
        aliases=[],
        city="Levis",
        province="Quebec",
        age=17,
        status="vulnerable",
        case_status="open",
        source_feed="MCSC",
        is_active=True,
    )


def _lead() -> NormalizedLead:
    return NormalizedLead(
        connector_name="test-news",
        source_kind="clear-web",
        lead_type="news-article",
        category="news-monitoring",
        source_name="Test News",
        source_url="https://news.example.org/report",
        query_used='"Rosalie Naess-Leclerc" missing',
        found_at=datetime.now(timezone.utc),
        title="Search continues for Rosalie Naess-Leclerc",
        summary="Police continue the missing-person search.",
        source_trust=0.65,
    )


def test_subject_match_requires_name_and_missing_person_context():
    assert _subject_matches(
        "Police are searching for missing Rosalie Naess-Leclerc after a reported sighting.",
        "Rosalie Naess-Leclerc",
    )
    assert not _subject_matches(
        "Rosalie Naess-Leclerc published a university paper.",
        "Rosalie Naess-Leclerc",
    )
    assert not _subject_matches(
        "Police are searching for another missing person in Levis.",
        "Rosalie Naess-Leclerc",
    )


def test_enrichment_attaches_unverified_location_and_geocode(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return SightingDetail(
            sighting_location="Gatineau",
            sighting_date="April 22, 2026",
            key_facts=["Rosalie Naess-Leclerc may have been seen near the bus terminal in Gatineau."],
            direction_of_travel="walking toward the bus terminal",
            raw_text=(
                "Police are searching for missing Rosalie Naess-Leclerc. "
                "Rosalie Naess-Leclerc may have been seen near the bus terminal in Gatineau."
            ),
        )

    async def fake_geocode(*args, **kwargs):
        return GeocodeResult(
            latitude=45.4765,
            longitude=-75.7013,
            display_name="Gatineau, Quebec, Canada",
            precision="city",
        )

    monkeypatch.setattr("backend.enrichment.lead_enrichment.fetch_and_extract", fake_fetch)
    monkeypatch.setattr("backend.enrichment.lead_enrichment.geocode_public_place", fake_geocode)

    lead = _lead()
    enriched = asyncio.run(enrich_location_leads(_case(), [lead]))

    assert enriched == 1
    assert lead.location_text == "Gatineau"
    assert lead.latitude == 45.4765
    assert any("(unverified): Gatineau" in reason for reason in lead.rationale)
    assert not any("verified intelligence" in reason.lower() for reason in lead.rationale)


def test_enrichment_does_not_promote_case_origin_as_new_sighting(monkeypatch):
    async def fake_fetch(*args, **kwargs):
        return SightingDetail(
            sighting_location="Levis",
            key_facts=["Rosalie Naess-Leclerc was last seen in Levis."],
            raw_text="Police are searching for missing Rosalie Naess-Leclerc, who was last seen in Levis.",
        )

    async def fail_if_geocoded(*args, **kwargs):
        raise AssertionError("The already-known case origin should not be geocoded as a sighting")

    monkeypatch.setattr("backend.enrichment.lead_enrichment.fetch_and_extract", fake_fetch)
    monkeypatch.setattr("backend.enrichment.lead_enrichment.geocode_public_place", fail_if_geocoded)

    lead = _lead()
    enriched = asyncio.run(enrich_location_leads(_case(), [lead]))

    assert enriched == 1
    assert any("case-origin location: Levis" in reason for reason in lead.rationale)
    assert not any("sighting location (unverified)" in reason for reason in lead.rationale)
    assert lead.latitude is None
