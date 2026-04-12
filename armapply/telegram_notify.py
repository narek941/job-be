from __future__ import annotations

import logging

import httpx

from armapply.config import TELEGRAM_BOT_TOKEN

log = logging.getLogger(__name__)


def send_telegram_message(chat_id: str, text: str, bot_token: str | None = None) -> bool:
    token = bot_token or TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = httpx.post(url, json={
            "chat_id": chat_id, 
            "text": text[:4000],
            "parse_mode": "Markdown"
        }, timeout=15.0)
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False
