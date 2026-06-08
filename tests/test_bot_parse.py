from armapply.bot import IncomingCallback, IncomingMessage, parse_update


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
