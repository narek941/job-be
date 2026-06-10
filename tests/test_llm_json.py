"""Tests for the LLM client — focused on the response-decoding logic, with
the HTTP call mocked out."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from armapply import llm


def _fake_settings():
    from armapply.config import Settings
    return Settings(
        database_url="postgresql://u:p@h:5432/d",
        gemini_api_key="x",
        gemini_model="gemini-2.0-flash",
        telegram_bot_token="x",
        telegram_webhook_secret="",
        pipeline_secret="s",
        gmail_address="",
        gmail_app_password="",
        google_client_id="",
        google_client_secret="",
        app_url="",
        worldwide_ratio_default=0.1,
        min_score_notify_default=6,
        min_score_auto_apply_default=8,
    )


def _mock_response(status_code: int, body: dict) -> httpx.Response:
    return httpx.Response(status_code, json=body, request=httpx.Request("POST", "http://x"))


def test_complete_json_happy_path() -> None:
    response_body = {
        "candidates": [{"content": {"parts": [{"text": '{"score": 7, "reason": "ok"}'}]}}],
    }
    with patch("armapply.llm.settings", return_value=_fake_settings()), \
         patch("httpx.Client.post", return_value=_mock_response(200, response_body)):
        out = llm.complete_json(system="s", user="u")
    assert out == {"score": 7, "reason": "ok"}


def test_complete_text_happy_path() -> None:
    response_body = {
        "candidates": [{"content": {"parts": [{"text": "hello world"}]}}],
    }
    with patch("armapply.llm.settings", return_value=_fake_settings()), \
         patch("httpx.Client.post", return_value=_mock_response(200, response_body)):
        out = llm.complete(system="s", user="u")
    assert out == "hello world"


def test_http_error_raises_llm_error() -> None:
    with patch("armapply.llm.settings", return_value=_fake_settings()), \
         patch("httpx.Client.post", return_value=_mock_response(404, {"error": "nope"})):
        with pytest.raises(llm.LLMError):
            llm.complete(system="s", user="u")


def test_safety_block_raises_llm_error() -> None:
    body = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
    with patch("armapply.llm.settings", return_value=_fake_settings()), \
         patch("httpx.Client.post", return_value=_mock_response(200, body)):
        with pytest.raises(llm.LLMError, match="SAFETY"):
            llm.complete(system="s", user="u")


def test_retries_on_503_then_succeeds() -> None:
    success_body = {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
    responses = [
        _mock_response(503, {"error": "busy"}),
        _mock_response(503, {"error": "still busy"}),
        _mock_response(200, success_body),
    ]
    with patch("armapply.llm.settings", return_value=_fake_settings()), \
         patch("armapply.llm.time.sleep"), \
         patch("httpx.Client.post", side_effect=responses):
        assert llm.complete(system="s", user="u") == "OK"


def test_gives_up_after_max_attempts() -> None:
    busy = _mock_response(503, {"error": "busy"})
    with patch("armapply.llm.settings", return_value=_fake_settings()), \
         patch("armapply.llm.time.sleep"), \
         patch("httpx.Client.post", return_value=busy):
        with pytest.raises(llm.LLMError, match="503"):
            llm.complete(system="s", user="u")
