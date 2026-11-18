"""Telegram notifications (owner's private chat). No-op if not configured."""

import logging
import os

import requests

from clipper.env import load_env

log = logging.getLogger(__name__)


def send(text: str, silent: bool = True) -> bool:
    load_env()
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.info("[tg off] %s", text)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "disable_notification": silent},
            timeout=10,
        )
        return r.ok
    except requests.RequestException as e:
        # the request URL contains the bot token: never log it
        log.warning("telegram send failed: %s", str(e).replace(token, "<token>") if token else e)
        return False
