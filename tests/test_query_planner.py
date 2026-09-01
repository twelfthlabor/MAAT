from datetime import datetime, timezone

from backend.osint.normalization.models import QueryContext
from backend.osint.query_planner import (
    build_investigator_query_plan,
    build_news_query_plan,
    build_public_query_plan,
    build_trace_labs_query_groups,
)


def test_build_public_query_plan_is_bounded_and_deduplicated():
    context = QueryContext(
        case_id=1,
        name="Sample Case Toronto",
        aliases=["SCT", "Sample Case Toronto"],
        city="Toronto",
        province="Ontario",
        age=14,
        missing_since=datetime.now(timezone.utc),
        location_text="Wynford Dr & Concorde Pl, Toronto, ON",
    )

    queries = build_public_query_plan(context, limit=10)

    assert queries[0] == '"Sample Case Toronto"'
    assert any('"Wynford Dr & Concorde Pl, Toronto, ON"' in query for query in queries)
    assert any('"Toronto"' in query for query in queries)
    assert any('"Ontario"' in query for query in queries)
    assert any('"SCT"' in query for query in queries)
    assert len(queries) <= 10
    assert len(set(queries)) == len(queries)


def test_build_trace_labs_query_groups_cover_social_employment_and_timeline():
    context = QueryContext(
        case_id=1,
        name="Sample Case Toronto",
        aliases=["SCT", "CaseTO"],
        city="Toronto",
        province="Ontario",
        age=14,
        missing_since=datetime(2026, 3, 14, tzinfo=timezone.utc),
        location_text="Wynford Dr & Concorde Pl, Toronto, ON",
    )

    groups = build_trace_labs_query_groups(context)
    slugs = {group["slug"] for group in groups}

    assert "general-sweep" in slugs
    assert "social-profile-sweep" in slugs
    assert "employment-school" in slugs
    assert "timeline-advancement" in slugs

    social_group = next(group for group in groups if group["slug"] == "social-profile-sweep")
    assert any("site:instagram.com" in query for query in social_group["queries"])
    assert any('"Toronto"' in query for query in social_group["queries"])
    timeline_group = next(group for group in groups if group["slug"] == "timeline-advancement")
    assert any('"Wynford Dr & Concorde Pl, Toronto, ON"' in query for query in timeline_group["queries"])


def test_build_investigator_query_plan_is_bounded_and_includes_trace_labs_style_pivots():
    context = QueryContext(
        case_id=1,
        name="Sample Case Toronto",
        aliases=["SCT"],
        city="Toronto",
        province="Ontario",
        age=14,
        missing_since=datetime(2026, 3, 14, tzinfo=timezone.utc),
        location_text="Wynford Dr & Concorde Pl, Toronto, ON",
    )

    queries = build_investigator_query_plan(context, limit=10)

    assert len(queries) <= 10
    assert len(set(queries)) == len(queries)
    assert any("site:instagram.com" in query or "site:tiktok.com" in query for query in queries)
    assert any('"2026"' in query for query in queries)


def test_build_news_query_plan_stays_news_focused_and_bounded():
    context = QueryContext(
        case_id=1,
        name="Sample Case Toronto",
        aliases=["SCT"],
        city="Toronto",
        province="Ontario",
        age=14,
        missing_since=datetime(2026, 3, 14, tzinfo=timezone.utc),
        location_text="Wynford Dr & Concorde Pl, Toronto, ON",
    )

    queries = build_news_query_plan(context, limit=6)

    assert len(queries) <= 6
    assert len(set(queries)) == len(queries)
    assert all("site:" not in query for query in queries)
    assert any("missing" in query or "last seen" in query for query in queries)
    assert any('"Wynford Dr & Concorde Pl, Toronto, ON"' in query for query in queries)
    assert any('"2026"' in query for query in queries)


def test_quebec_query_plans_add_localized_terms_and_authority_pivots():
    context = QueryContext(
        case_id=8181,
        name="Rosalie Naess-Leclerc",
        aliases=[],
        city="Levis",
        province="Quebec",
        age=17,
        missing_since=datetime(2026, 4, 21, tzinfo=timezone.utc),
        location_text="Levis, QC",
        authority_name="Levis City Police Service",
    )

    public_queries = build_public_query_plan(context, limit=12)
    news_queries = build_news_query_plan(context, limit=8)

    assert any("fugue" in query or "disparition" in query for query in public_queries)
    assert any('"Levis City Police Service"' in query for query in public_queries)
    assert any("fugue" in query or "disparition" in query for query in news_queries)
    assert any('"Levis City Police Service"' in query for query in news_queries)


def test_single_name_queries_are_case_anchored_before_generic_sweeps():
    context = QueryContext(
        case_id=8453,
        name="Joshua",
        aliases=[],
        city="Victoria",
        province="British Columbia",
        age=15,
        missing_since=datetime(2026, 7, 10, tzinfo=timezone.utc),
        location_text="Kings Road, Victoria, BC",
        authority_name="Victoria Police Department",
    )

    public_queries = build_public_query_plan(context, limit=6)
    news_queries = build_news_query_plan(context, limit=6)

    assert public_queries
    assert news_queries
    assert all("Joshua" in query for query in public_queries[:3])
    assert any('"Victoria"' in query or '"Kings Road, Victoria, BC"' in query for query in public_queries[:3])
    assert any('"Victoria"' in query or '"Kings Road, Victoria, BC"' in query for query in news_queries[:3])
