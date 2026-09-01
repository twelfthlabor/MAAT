from datetime import datetime, timezone

from backend.osint.hypothesis import _analyze_lead_evidence, generate_hypothesis


def test_official_last_seen_baseline_is_not_a_new_sighting():
    signals = _analyze_lead_evidence([
        {
            "lead_type": "official-last-seen",
            "category": "official-last-seen",
            "source_name": "Victoria Police Department",
            "title": "Official last-seen location",
            "content_excerpt": "Last seen getting into a blue Honda on Kings Road.",
            "confidence": 0.9,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "rationale": [],
        }
    ], "Joshua", "Victoria", "British Columbia")

    assert signals["has_sightings"] is False
    assert signals["sighting_leads"] == []


def test_volume_without_location_candidates_cannot_narrow_geography():
    leads = [
        {
            "lead_type": "news-article",
            "category": "news-monitoring",
            "source_name": f"Synthetic Source {index}",
            "title": "Synthetic CTF subject mentioned in an awareness article",
            "content_excerpt": "Public awareness coverage without a new sighting.",
            "confidence": 0.8,
            "rationale": [],
        }
        for index in range(20)
    ]

    report = generate_hypothesis(
        case_id=999,
        case_name="Synthetic CTF Subject",
        case_age=30,
        case_city="Example City",
        case_province="Example Province",
        case_lat=43.0,
        case_lon=-79.0,
        missing_since=datetime.now(timezone.utc),
        leads=leads,
    )

    assert report.geographic_assessment.confidence == "low"
    assert "insufficient source-backed" in report.geographic_assessment.probable_zone.lower()
    assert "geographic conclusions are unsupported" in report.data_quality_note.lower()


def test_unlinked_spotted_headline_is_not_a_case_sighting():
    signals = _analyze_lead_evidence([
        {
            "lead_type": "news-article",
            "category": "news-monitoring",
            "source_name": "Bing News",
            "title": "Mother worries for missing Victoria teen spotted last month in northern B.C.",
            "content_excerpt": "A different Joshua was spotted in northern B.C.",
            "confidence": 0.55,
            "published_at": datetime.now(timezone.utc).isoformat(),
            "rationale": [],
        }
    ], "Joshua", "Victoria", "British Columbia")

    assert signals["has_sightings"] is False
    assert signals["sighting_leads"] == []
