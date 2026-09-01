from backend.osint.synthesis import _detect_geographic_patterns, _infer_theme


def test_last_seen_baseline_is_not_classified_as_new_sighting():
    leads = [{
        "title": "Official last-seen location for Joshua",
        "summary": "Last seen getting into a blue Honda car in Victoria.",
        "category": "official-last-seen",
    }]

    assert _infer_theme(leads, "official-last-seen") == "official-update"


def test_explicit_sighting_language_creates_sighting_theme():
    leads = [{
        "title": "Joshua possibly spotted in Terrace",
        "summary": "A witness reported seeing him near the station.",
        "category": "clear-web-search",
    }]

    assert _infer_theme(leads, "clear-web-search") == "potential-sighting"


def test_geographic_patterns_ignore_tool_and_baseline_coordinates():
    leads = [
        {
            "lead_type": "analyst-action",
            "source_kind": "tooling",
            "title": "People search",
            "location_text": "Example City",
            "latitude": 43.1,
            "longitude": -79.1,
        },
        {
            "lead_type": "official-last-seen",
            "source_kind": "official",
            "title": "Official last-known location",
            "location_text": "Example City",
            "latitude": 43.1,
            "longitude": -79.1,
        },
    ]

    assert _detect_geographic_patterns(leads, 43.1, -79.1) == []


def test_geographic_patterns_accept_reported_sighting_candidates():
    leads = [
        {
            "lead_type": "sighting-trace",
            "source_kind": "clear-web",
            "title": f"Synthetic subject reportedly spotted near station {index}",
            "location_text": "Example Station",
            "latitude": 43.2 + index * 0.001,
            "longitude": -79.2,
            "analysis": {
                "evidence_type": "reported_sighting",
                "is_location_candidate": True,
            },
        }
        for index in range(2)
    ]

    patterns = _detect_geographic_patterns(leads, 43.1, -79.1)

    assert any(pattern["type"] == "geographic-cluster" for pattern in patterns)
