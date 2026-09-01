from backend.osint.connectors.network_analysis import NetworkAnalysisConnector
from backend.osint.normalization.models import QueryContext


def test_network_connector_has_explicit_sighting_category():
    context = QueryContext(
        case_id=8453,
        name="Joshua",
        aliases=[],
        city="Victoria",
        province="British Columbia",
        age=15,
        missing_since=None,
    )
    categories = {item["category"] for item in NetworkAnalysisConnector()._build_queries(context)}

    assert "sighting-trace" in categories
