from armapply.discovery import clean_url, extract_email


def test_clean_url_strips_tracking_params() -> None:
    raw = "https://www.linkedin.com/jobs/view/123?utm_source=foo&trk=public&keepme=1"
    cleaned = clean_url(raw)
    assert "utm_source" not in cleaned
    assert "trk" not in cleaned
    assert "keepme=1" in cleaned


def test_clean_url_handles_no_query() -> None:
    assert clean_url("https://staff.am/en/jobs/123/foo") == "https://staff.am/en/jobs/123/foo"


def test_clean_url_handles_empty() -> None:
    assert clean_url("") == ""


def test_extract_email_basic() -> None:
    assert extract_email("Contact us at hr@acme.com today") == "hr@acme.com"


def test_extract_email_none() -> None:
    assert extract_email("no email here") is None
    assert extract_email(None) is None
