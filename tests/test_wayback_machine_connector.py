from backend.osint.connectors.wayback_machine import _is_archivable_page


def test_wayback_accepts_public_pages_and_rejects_api_feeds():
    assert _is_archivable_page("https://police.example/cases/123")
    assert not _is_archivable_page("javascript:alert(1)")
    assert not _is_archivable_page(
        "https://services.arcgis.com/example/FeatureServer/0/query"
    )
