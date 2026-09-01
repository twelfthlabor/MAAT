from scripts.validate_ctf_fixture import validate_fixture


def test_synthetic_ctf_fixture_locates_only_after_source_convergence():
    result = validate_fixture()

    assert result["passed"] is True
    assert result["location_evidence"]["sufficient"] is True
    assert result["flag"] == "MAAT{source_backed_location_convergence}"
