from datetime import datetime, timezone

from backend.osint.lead_analysis import assess_lead


def test_reported_sighting_is_distinct_from_relevance_and_remains_unverified():
    lead = {
        "lead_type": "news-article",
        "source_kind": "clear-web",
        "title": "Missing teen reportedly spotted near Central Station",
        "summary": "A witness report describes the station area.",
        "location_text": "Central Station, Toronto",
        "latitude": 43.645,
        "longitude": -79.38,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "review_status": "unreviewed",
        "rationale": [
            "Machine-extracted sighting location (unverified): Central Station, Toronto",
            "Geocode precision: neighbourhood",
            "Direction: walking toward the bus terminal",
        ],
    }

    assessment = assess_lead(lead)

    assert assessment["evidence_type"] == "reported_sighting"
    assert assessment["verification_state"] == "unverified"
    assert assessment["location_precision"] == "neighbourhood"
    assert assessment["actionability_score"] >= 65
    assert assessment["is_location_candidate"] is True


def test_research_tool_is_not_misrepresented_as_evidence():
    assessment = assess_lead(
        {
            "lead_type": "analyst-action",
            "source_kind": "tooling",
            "title": "Open a public people-search query",
            "review_status": "unreviewed",
            "rationale": [],
        }
    )

    assert assessment["evidence_type"] == "research_tool"
    assert assessment["is_location_candidate"] is False
    assert "not evidence" in assessment["next_step"].lower()


def test_research_tool_with_prefilled_city_is_still_not_location_evidence():
    assessment = assess_lead(
        {
            "lead_type": "analyst-action",
            "source_kind": "tooling",
            "title": "Open a public people-search query",
            "location_text": "Example City",
            "latitude": 43.1,
            "longitude": -79.1,
            "rationale": [],
        }
    )

    assert assessment["evidence_type"] == "research_tool"
    assert assessment["is_location_candidate"] is False


def test_manual_review_changes_state_without_claiming_source_verification():
    assessment = assess_lead(
        {
            "lead_type": "web-mention",
            "source_kind": "clear-web",
            "title": "Report says the subject was sighted",
            "location_text": "Levis",
            "review_status": "credible",
            "rationale": [],
        }
    )

    assert assessment["verification_state"] == "analyst_reviewed"
    assert assessment["verification_label"] == "Analyst reviewed"


def test_low_relevance_result_cannot_rank_high_on_generic_case_city():
    assessment = assess_lead(
        {
            "lead_type": "web-mention",
            "source_kind": "clear-web",
            "title": "Unrelated school yearbook",
            "location_text": "Red Deer",
            "confidence": 0.12,
            "review_status": "unreviewed",
            "rationale": [],
        }
    )

    assert assessment["evidence_type"] == "location_mention"
    assert assessment["actionability_score"] <= 15


def test_possible_namesake_cannot_become_a_location_candidate():
    assessment = assess_lead(
        {
            "lead_type": "news-article",
            "source_kind": "clear-web",
            "title": "Synthetic namesake reportedly spotted near a station",
            "location_text": "Different City",
            "confidence": 0.7,
            "rationale": [
                "Ambiguous single-name result lacks case geography, authority, or two distinctive official anchors."
            ],
        }
    )

    assert assessment["evidence_type"] == "location_mention"
    assert assessment["evidence_label"] == "Possible namesake location mention"
    assert assessment["is_location_candidate"] is False
    assert assessment["actionability_score"] <= 20
