import base64
from datetime import datetime, timezone

from jobfox import reply_tracking


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_gmail_search_query() -> None:
    applied = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    q = reply_tracking.gmail_search_query("hr@acme.am", applied)
    assert q == f"from:hr@acme.am after:{int(applied.timestamp())}"


def test_message_text_plain() -> None:
    payload = {"mimeType": "text/plain", "body": {"data": _b64("Hello Narek")}}
    assert reply_tracking.message_text(payload) == "Hello Narek"


def test_message_text_multipart_prefers_plain() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<b>Hi html</b>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("Hi plain")}},
        ],
    }
    assert reply_tracking.message_text(payload) == "Hi plain"


def test_message_text_html_fallback_strips_tags() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>We would <b>love</b> to talk</p>")}},
        ],
    }
    text = reply_tracking.message_text(payload)
    assert "love" in text and "<" not in text


def test_message_text_handles_garbage() -> None:
    assert reply_tracking.message_text({}) == ""
    assert reply_tracking.message_text({"mimeType": "text/plain", "body": {"data": "!!!"}}) == ""


def test_classify_reply_falls_back_on_llm_failure(monkeypatch) -> None:
    from jobfox import llm

    def boom(**kwargs):
        raise llm.LLMError("down")

    monkeypatch.setattr(reply_tracking.llm, "complete_json", boom)
    out = reply_tracking.classify_reply("Some recruiter text")
    assert out == {"type": "reply", "interview_datetime": None, "summary": ""}


def test_classify_reply_normalizes(monkeypatch) -> None:
    monkeypatch.setattr(
        reply_tracking.llm,
        "complete_json",
        lambda **kwargs: {
            "type": "INTERVIEW",
            "interview_datetime": "2026-06-20T15:00:00+04:00",
            "summary": "They propose a technical interview on Friday.",
        },
    )
    out = reply_tracking.classify_reply("…")
    # type comes back lowercased-but-unknown ("INTERVIEW".lower() == "interview")
    assert out["type"] == "interview"
    assert out["interview_datetime"] == "2026-06-20T15:00:00+04:00"


def test_type_actions_cover_terminal_states() -> None:
    assert reply_tracking._TYPE_ACTIONS["interview"] == ("interview", "interview")
    assert reply_tracking._TYPE_ACTIONS["offer"] == ("offer", "offer")
    assert reply_tracking._TYPE_ACTIONS["rejection"] == ("rejected", "rejected")
    assert "reply" not in reply_tracking._TYPE_ACTIONS
    assert "other" not in reply_tracking._TYPE_ACTIONS


def test_notification_wording() -> None:
    n = reply_tracking._notification(
        "Acme", {"type": "interview", "interview_datetime": "2026-06-20", "summary": "Tech round."}
    )
    assert "Acme" in n and "interview" in n.lower() and "2026-06-20" in n
    n2 = reply_tracking._notification("Acme", {"type": "offer", "interview_datetime": None, "summary": ""})
    assert "offer" in n2.lower()
