"""Thin HTTP wrapper around the Telegram Bot API.

Just the calls we need: sendMessage, editMessageText, sendDocument, getFile,
downloadFile, answerCallbackQuery. Returns parsed JSON on success or raises
TelegramError. Pure transport — no business logic.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from jobfox.config import settings

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class TelegramError(RuntimeError):
    pass


def _base() -> str:
    return f"https://api.telegram.org/bot{settings().telegram_bot_token}"


def _post(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.post(f"{_base()}/{method}", json=payload)
    try:
        data = r.json()
    except Exception as e:
        raise TelegramError(f"telegram {method}: non-JSON response (status={r.status_code})") from e
    if not data.get("ok"):
        raise TelegramError(f"telegram {method}: {data.get('description', data)}")
    return data["result"]


def send_message(
    chat_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = None,
    disable_web_page_preview: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4096],
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post("sendMessage", payload)


def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post("editMessageText", payload)


def answer_callback(callback_id: str, text: str = "", show_alert: bool = False) -> None:
    _post("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text[:200],
        "show_alert": show_alert,
    })


def send_document(
    chat_id: int,
    *,
    filename: str,
    content: bytes,
    caption: str = "",
    mime_type: str = "application/pdf",
) -> dict[str, Any]:
    """Upload a file as a Telegram document message."""
    files = {"document": (filename or "file", content, mime_type)}
    data: dict[str, str] = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:1024]
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.post(f"{_base()}/sendDocument", data=data, files=files)
    try:
        payload = r.json()
    except Exception as e:
        raise TelegramError(f"telegram sendDocument: non-JSON response (status={r.status_code})") from e
    if not payload.get("ok"):
        raise TelegramError(f"telegram sendDocument: {payload.get('description', payload)}")
    return payload["result"]


def get_file(file_id: str) -> dict[str, Any]:
    return _post("getFile", {"file_id": file_id})


def download_file(file_path: str) -> bytes:
    s = settings()
    url = f"https://api.telegram.org/file/bot{s.telegram_bot_token}/{file_path}"
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content
