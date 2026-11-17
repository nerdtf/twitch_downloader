import requests

import env


def send_tg(text, disable_notification=True):
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[tg disabled: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set] {text}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"`{text}`",
        "parse_mode": "Markdown",
        "disable_notification": disable_notification,
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
    except requests.RequestException as e:
        print(f"Telegram send failed: {e}")
        return False
    return response.ok
