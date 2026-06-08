from urllib.parse import parse_qs, urlparse

from armapply.apply import gmail_compose_url


def test_gmail_url_includes_subject_and_body() -> None:
    url = gmail_compose_url(to="hr@acme.com", subject="Application", body="Hello there")
    parts = urlparse(url)
    assert parts.netloc == "mail.google.com"
    qs = parse_qs(parts.query)
    assert qs["to"] == ["hr@acme.com"]
    assert qs["su"] == ["Application"]
    assert qs["body"] == ["Hello there"]
    assert qs["view"] == ["cm"]


def test_gmail_url_omits_recipient_when_missing() -> None:
    url = gmail_compose_url(to=None, subject="S", body="B")
    qs = parse_qs(urlparse(url).query)
    assert "to" not in qs
    assert qs["su"] == ["S"]


def test_gmail_url_clips_oversize_body() -> None:
    huge = "x" * 10_000
    url = gmail_compose_url(to=None, subject="S", body=huge)
    body = parse_qs(urlparse(url).query)["body"][0]
    assert len(body) == 6000
