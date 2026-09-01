from datetime import datetime, timezone

from backend.osint.normalization.models import NormalizedLead
from backend.osint.pivots import discover_usernames, username_from_url


def _lead(url: str) -> NormalizedLead:
    return NormalizedLead(
        connector_name="test",
        source_kind="clear-web",
        lead_type="profile",
        category="social",
        source_name="test",
        source_url=url,
        query_used="test",
        found_at=datetime.now(timezone.utc),
        title="test",
        summary="test",
    )


def test_username_pivots_only_accept_known_profile_shapes():
    assert username_from_url("https://github.com/example-user") == "example-user"
    assert username_from_url("https://reddit.com/user/example_user/") == "example_user"
    assert username_from_url("https://tiktok.com/@example.user") == "example.user"
    assert username_from_url("https://github.com/search?q=example") is None
    assert username_from_url("https://unknown.example/example") is None


def test_discover_usernames_deduplicates_case_insensitively():
    assert discover_usernames([
        _lead("https://github.com/ExampleUser"),
        _lead("https://x.com/exampleuser"),
    ]) == ["ExampleUser"]
