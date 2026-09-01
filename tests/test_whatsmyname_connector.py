from backend.osint.connectors.whatsmyname import response_claims_username, select_checkable_sites


def test_whatsmyname_selection_skips_challenges_and_sensitive_categories():
    payload = {"sites": [
        {"name": "SafeSocial", "cat": "social", "uri_check": "https://safe.test/{account}", "e_code": 200, "e_string": "profile-found"},
        {"name": "CaptchaSite", "cat": "social", "uri_check": "https://captcha.test/{account}", "e_code": 200, "e_string": "found", "protection": ["captcha"]},
        {"name": "AdultSite", "cat": "adult", "uri_check": "https://adult.test/{account}", "e_code": 200, "e_string": "found"},
        {"name": "WeakSite", "cat": "tech", "uri_check": "https://weak.test/{account}", "e_code": 200, "e_string": ""},
    ]}

    assert [site["name"] for site in select_checkable_sites(payload, 10)] == ["SafeSocial"]


def test_whatsmyname_positive_requires_status_and_content_fingerprint():
    site = {"e_code": 200, "e_string": "profile-found"}
    assert response_claims_username(site, 200, "<div>PROFILE-FOUND</div>")
    assert not response_claims_username(site, 404, "profile-found")
    assert not response_claims_username(site, 200, "generic profile page")
