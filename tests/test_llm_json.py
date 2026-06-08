import pytest

from armapply.llm import LLMError, _parse_json


def test_parse_direct_json() -> None:
    assert _parse_json('{"score": 7}') == {"score": 7}


def test_parse_fenced_json() -> None:
    raw = "Here you go:\n```json\n{\"score\": 8, \"reason\": \"good\"}\n```"
    assert _parse_json(raw) == {"score": 8, "reason": "good"}


def test_parse_loose_json() -> None:
    raw = "Sure: {\"a\": 1}"
    assert _parse_json(raw) == {"a": 1}


def test_parse_garbage_raises() -> None:
    with pytest.raises(LLMError):
        _parse_json("absolutely not JSON")
