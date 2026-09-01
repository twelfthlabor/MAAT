from datetime import datetime, timezone

from backend.osint.hypothesis import _analyze_lead_evidence


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
