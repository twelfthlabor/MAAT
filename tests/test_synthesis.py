from backend.osint.synthesis import _infer_theme


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
