from backend.osint.connectors.arquivo_pt import arquivo_result_relevant


def test_arquivo_requires_multi_part_name_relevance():
    assert arquivo_result_relevant(
        {"title": "Public appeal for Zoë Example-Person", "snippet": "Toronto update"},
        "Zoë Example-Person",
    )
    assert not arquivo_result_relevant(
        {"title": "An unrelated example", "snippet": "Toronto update"},
        "Zoë Example-Person",
    )
