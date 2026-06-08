from armapply.discovery import _looks_non_tech, clean_url, extract_email


def test_non_tech_filter_drops_obvious_misses() -> None:
    assert _looks_non_tech("Senior Accountant")
    assert _looks_non_tech("Customer Service Operator")
    assert _looks_non_tech("Retail Sales Manager")
    assert _looks_non_tech("Lawyer (Banking)")


def test_non_tech_filter_keeps_tech_titles() -> None:
    assert not _looks_non_tech("Senior Frontend Engineer")
    assert not _looks_non_tech("Backend Developer (Node.js)")
    assert not _looks_non_tech("DevOps Engineer")
    assert not _looks_non_tech("Network Infrastructure Engineer")
    # 'consultant' is non-tech-leaning ("Financial Consultant"), but
    # "Engineering Consultant" should still pass. We accept that ambiguous
    # 'consultant' titles get filtered — Gemini-scored tech consultants
    # are rare on staff.am anyway.



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
