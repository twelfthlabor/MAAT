from backend.osint.location_evidence import evaluate_location_evidence


def _lead(domain: str, location: str = "Aurora Transit Terminal", *, credible: bool = False):
    return {
        "lead_type": "sighting-trace",
        "source_kind": "clear-web",
        "source_name": domain,
        "source_url": f"https://{domain}/report",
        "title": "Synthetic CTF Subject from Origin City was reportedly spotted near Aurora Transit Terminal",
        "content_excerpt": "A public report describes the subject at the terminal after the challenge date.",
        "location_text": location,
        "latitude": 43.65,
        "longitude": -79.38,
        "confidence": 0.84,
        "review_status": "credible" if credible else "unreviewed",
    }


def test_two_independent_sources_with_review_converge():
    report = evaluate_location_evidence([
        _lead("archive.example", credible=True),
        _lead("news.example"),
    ])

    assert report["sufficient"] is True
    assert report["confidence"] == "medium"
    assert report["best_candidate"]["independent_source_count"] == 2


def test_unreviewed_or_duplicate_sources_are_not_enough():
    unreviewed = evaluate_location_evidence([
        _lead("archive.example"),
        _lead("news.example"),
    ])
    duplicate = evaluate_location_evidence([
        _lead("archive.example", credible=True),
        {**_lead("archive.example", credible=True), "source_url": "https://archive.example/second"},
    ])

    assert unreviewed["sufficient"] is False
    assert duplicate["sufficient"] is False


def test_conflicting_corroborated_locations_fail_closed():
    report = evaluate_location_evidence([
        _lead("one.example", credible=True),
        _lead("two.example"),
        {**_lead("three.example", "Different Terminal", credible=True), "latitude": 45.5, "longitude": -73.6},
        {**_lead("four.example", "Different Terminal"), "latitude": 45.501, "longitude": -73.6},
    ])

    assert report["conflicting"] is True
    assert report["sufficient"] is False
