from jobfox.bot import (
    _CALLBACKS,
    _OUTCOMES,
    IncomingCallback,
    IncomingMessage,
    _outcome_row,
    parse_update,
)


def test_parse_text_message() -> None:
    upd = {"message": {"chat": {"id": 42}, "text": "/start"}}
    parsed = parse_update(upd)
    assert isinstance(parsed, IncomingMessage)
    assert parsed.chat_id == 42
    assert parsed.text == "/start"
    assert parsed.document_file_id is None


def test_parse_document_message() -> None:
    upd = {
        "message": {
            "chat": {"id": 42},
            "document": {"file_id": "abc", "file_name": "cv.pdf"},
        }
    }
    parsed = parse_update(upd)
    assert isinstance(parsed, IncomingMessage)
    assert parsed.document_file_id == "abc"
    assert parsed.document_filename == "cv.pdf"


def test_parse_callback() -> None:
    upd = {
        "callback_query": {
            "id": "cb1",
            "data": "apply:7",
            "message": {"message_id": 99, "chat": {"id": 42}},
        }
    }
    parsed = parse_update(upd)
    assert isinstance(parsed, IncomingCallback)
    assert parsed.callback_id == "cb1"
    assert parsed.data == "apply:7"
    assert parsed.chat_id == 42
    assert parsed.message_id == 99


def test_parse_unknown_update() -> None:
    assert parse_update({"edited_message": {}}) is None


def test_outcome_row_callback_data() -> None:
    row = _outcome_row(7)
    assert [b["callback_data"] for b in row] == ["interview:7", "rejected:7", "offer:7"]


def test_outcome_callbacks_registered() -> None:
    # Every manual outcome has a registered callback handler, and the
    # callback action names match the _OUTCOMES keys used for rendering.
    for outcome in ("interview", "rejected", "offer"):
        assert outcome in _CALLBACKS
        assert outcome in _OUTCOMES
