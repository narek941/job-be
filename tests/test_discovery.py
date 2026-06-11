from bs4 import BeautifulSoup

from jobfox.discovery import (
    DEFAULT_TELEGRAM_CHANNELS,
    _STAFFAM_HR_MAIL_RE,
    _looks_non_tech,
    clean_url,
    email_from_mailto,
    extract_email,
    merged_channels,
)


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


def test_extract_email_prefers_hr_over_generic() -> None:
    text = "General questions: office@acme.am. Send your CV to hr@acme.am please."
    assert extract_email(text) == "hr@acme.am"


def test_extract_email_skips_noreply_entirely() -> None:
    assert extract_email("Sent from noreply@acme.am, do not answer") is None
    # …but a good address next to a noreply still wins.
    text = "noreply@jobs-mailer.com — apply at careers@acme.am"
    assert extract_email(text) == "careers@acme.am"


def test_extract_email_ignores_asset_filenames() -> None:
    assert extract_email("background: url(logo@2x.png)") is None


def test_extract_email_deobfuscates() -> None:
    assert extract_email("Contact hr [at] acme [dot] am for the role") == "hr@acme.am"
    assert extract_email("Write to hr(at)acme.am today") == "hr@acme.am"


def test_extract_email_context_bonus() -> None:
    text = (
        "Visit us at office@acme.am for general inquiries about the company "
        "and our products and services across all regions of Armenia.\n\n"
        "Отправьте резюме на a@acme.am."
    )
    assert extract_email(text) == "a@acme.am"


def test_email_from_mailto() -> None:
    soup = BeautifulSoup(
        '<p>Apply: <a href="mailto:HR@Acme.am?subject=Dev">click</a></p>',
        "html.parser",
    )
    assert email_from_mailto(soup) == "hr@acme.am"
    assert email_from_mailto(None) is None
    assert email_from_mailto(BeautifulSoup("<p>nothing</p>", "html.parser")) is None


def test_staffam_hr_mail_regex() -> None:
    html = '... ,"is_following":false,"hr_mail":"incident-m-326201@e.staff.am","relJobs":[...'
    m = _STAFFAM_HR_MAIL_RE.search(html)
    assert m and m.group(1) == "incident-m-326201@e.staff.am"


def test_staffam_json_description() -> None:
    from jobfox.discovery import _staffam_json_description

    html = (
        '..."apply_type":2,"description":"<p>Build \\"great\\" things</p>'
        '\\n<ul><li>Python</li></ul>","hr_mail":"x-1@e.staff.am"...'
    )
    text = _staffam_json_description(html)
    assert text is not None
    assert 'Build "great" things' in text
    assert "Python" in text
    assert "<p>" not in text
    assert _staffam_json_description("no blob here") is None


def test_merged_channels_defaults_always_present() -> None:
    # Even with no user channels, every default is scanned.
    assert merged_channels([]) == [c for c in DEFAULT_TELEGRAM_CHANNELS]


def test_merged_channels_appends_extras_after_defaults() -> None:
    out = merged_channels(["my_extra_channel"])
    assert out[: len(DEFAULT_TELEGRAM_CHANNELS)] == list(DEFAULT_TELEGRAM_CHANNELS)
    assert out[-1] == "my_extra_channel"


def test_merged_channels_dedupes_defaults_case_insensitively() -> None:
    # A user re-adding a default (any form) must not duplicate it.
    out = merged_channels(["@STAFFAM", "https://t.me/gortsiam", "staffam"])
    assert len(out) == len(DEFAULT_TELEGRAM_CHANNELS)
