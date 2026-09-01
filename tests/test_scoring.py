from datetime import datetime, timedelta, timezone

from backend.models.case import Case
from backend.osint.normalization.models import NormalizedLead
from backend.osint.scoring.lead_scoring import score_lead


def test_score_lead_returns_rationale_and_score():
    case = Case(
        id=1,
        slug="sample-case",
        name="Sample Case Toronto",
        aliases=["SCT"],
        city="Toronto",
        province="Ontario",
        age=14,
        status="vulnerable",
        case_status="open",
        latitude=43.65,
        longitude=-79.38,
        risk_flags=[],
        source_feed="MCSC",
        is_active=True,
        missing_since=datetime.now(timezone.utc) - timedelta(days=3),
    )
    lead = NormalizedLead(
        connector_name="mock-public-search",
        source_kind="clear-web",
        lead_type="web-mention",
        category="clear-web-search",
        source_name="Mock Search",
        source_url="https://example.org/result",
        query_used='"Sample Case Toronto" Toronto',
        found_at=datetime.now(timezone.utc),
        title="Sample Case Toronto seen near Toronto station",
        summary="Public post references Toronto station.",
        published_at=datetime.now(timezone.utc) - timedelta(days=1),
        location_text="Toronto",
        latitude=43.66,
        longitude=-79.37,
        source_trust=0.5,
        corroboration_count=2,
    )

    scored = score_lead(case, lead)

    assert scored.score > 0.4
    assert scored.rationale


def test_score_lead_prefers_actionable_detail_over_amplification_only():
    case = Case(
        id=8181,
        slug="rosalie-naess-leclerc",
        name="Rosalie Naess-Leclerc",
        aliases=[],
        city="Levis",
        province="Quebec",
        age=17,
        status="vulnerable",
        case_status="open",
        latitude=46.8,
        longitude=-71.17,
        risk_flags=[],
        source_feed="MCSC",
        is_active=True,
        missing_since=datetime.now(timezone.utc) - timedelta(hours=12),
    )

    actionable_lead = NormalizedLead(
        connector_name="duckduckgo-html",
        source_kind="clear-web",
        lead_type="web-mention",
        category="clear-web-search",
        source_name="DuckDuckGo",
        source_url="https://example.org/rosalie-detail",
        query_used='"Rosalie Naess-Leclerc" fugue "Levis"',
        found_at=datetime.now(timezone.utc),
        title="Rosalie Naess-Leclerc en fugue, derniere fois vue pres du boulevard Guillaume-Couture",
        summary="La police dit qu'elle pourrait se trouver dans le secteur Levis.",
        content_excerpt="Elle portait un manteau noir, un sac bleu et aurait ete vue pres du boulevard Guillaume-Couture.",
        published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        location_text="Levis",
        source_trust=0.45,
        corroboration_count=1,
    )

    amplification_lead = NormalizedLead(
        connector_name="duckduckgo-html",
        source_kind="clear-web",
        lead_type="web-mention",
        category="clear-web-search",
        source_name="DuckDuckGo",
        source_url="https://example.org/rosalie-share",
        query_used='"Rosalie Naess-Leclerc" Levis',
        found_at=datetime.now(timezone.utc),
        title="Please share: Rosalie Naess-Leclerc missing from Levis",
        summary="Please RT and share this alert widely.",
        content_excerpt="Read and RT. Join the conversation and support the search.",
        published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        location_text="Levis",
        source_trust=0.45,
        corroboration_count=1,
    )

    actionable = score_lead(case, actionable_lead)
    amplification = score_lead(case, amplification_lead)

    assert actionable.score > amplification.score
    assert any("location-specific detail" in reason.lower() for reason in actionable.rationale)
    assert any("amplification/share language" in reason.lower() for reason in amplification.rationale)


def test_score_lead_demotes_ambiguous_namesake_without_case_anchors():
    case = Case(
        id=8453,
        slug="joshua",
        name="Joshua",
        aliases=[],
        city="Victoria",
        province="British Columbia",
        age=15,
        status="vulnerable",
        case_status="open",
        missing_since=datetime(2026, 7, 10, tzinfo=timezone.utc),
        official_summary_html=(
            "Last seen getting into a blue Honda car in the 1100 block of Kings Road, Victoria, BC. "
            "Reference # 26-28591."
        ),
    )
    namesake = NormalizedLead(
        connector_name="bing-news-rss",
        source_kind="clear-web",
        lead_type="news-article",
        category="news-monitoring",
        source_name="Bing News",
        source_url="https://example.org/unrelated-joshua",
        query_used='"Joshua" missing',
        found_at=datetime.now(timezone.utc),
        title="Joshua 17 last seen in Immingham",
        summary="Police are searching for a missing teenager who may be in Grimsby.",
        content_excerpt="The teenager was last seen in England.",
        published_at=datetime.now(timezone.utc),
        source_trust=0.55,
    )

    scored = score_lead(case, namesake)

    assert scored.score < 0.6
    assert any("ambiguous single-name" in reason.lower() for reason in scored.rationale)


def test_score_lead_flags_expanded_given_name_as_possible_namesake():
    case = Case(
        id=8453,
        slug="joshua",
        name="Joshua",
        aliases=[],
        city="Victoria",
        province="British Columbia",
        age=15,
        status="vulnerable",
        case_status="open",
        missing_since=datetime(2026, 7, 10, tzinfo=timezone.utc),
        official_summary_html="Last seen getting into a blue Honda car in the 1100 block of Kings Road, Victoria, BC.",
    )
    namesake = NormalizedLead(
        connector_name="bing-news-rss",
        source_kind="clear-web",
        lead_type="news-article",
        category="news-monitoring",
        source_name="Bing News",
        source_url="https://example.org/joshua-gates",
        query_used='"Joshua" missing "Victoria"',
        found_at=datetime.now(timezone.utc),
        title="Mother worries for missing Victoria teen spotted last month in northern B.C.",
        summary="His mother says 16-year-old Joshua Gates has no family in northern B.C.",
        content_excerpt="Joshua Gates was spotted in northern B.C.",
        published_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        location_text="Victoria",
        source_trust=0.55,
        corroboration_count=2,
    )

    scored = score_lead(case, namesake)

    assert scored.score < 0.6
    assert any("possible namesake" in reason.lower() for reason in scored.rationale)
