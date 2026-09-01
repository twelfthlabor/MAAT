from datetime import datetime, timezone

from backend.osint.connectors.sherlock import build_username_candidates, parse_sherlock_csv
from backend.osint.normalization.models import QueryContext


def _context() -> QueryContext:
    return QueryContext(
        case_id=7,
        name="Zoë Example-Person",
        aliases=[],
        city="Toronto",
        province="Ontario",
        age=16,
        missing_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_build_username_candidates_is_bounded_and_normalized():
    assert build_username_candidates(_context()) == [
        "zoeexampleperson",
        "zoe.exampleperson",
        "zoe_exampleperson",
        "zexampleperson",
    ]


def test_parse_sherlock_csv_keeps_only_claimed_public_urls(tmp_path):
    report = tmp_path / "zoeexampleperson.csv"
    report.write_text(
        "username,name,url_main,url_user,exists,http_status,response_time_s\n"
        "zoeexampleperson,ExampleNet,https://example.net,https://example.net/zoeexampleperson,Claimed,200,0.2\n"
        "zoeexampleperson,MissingNet,https://missing.test,https://missing.test/zoeexampleperson,Available,404,0.1\n"
        "zoeexampleperson,BadUrl,https://bad.test,javascript:alert(1),Claimed,200,0.1\n",
        encoding="utf-8",
    )

    leads = parse_sherlock_csv(report, _context())

    assert len(leads) == 1
    assert leads[0].source_name == "ExampleNet"
    assert leads[0].location_text is None
    assert leads[0].source_trust == 0.25
    assert "does not establish identity" in leads[0].summary
