import requests

TOKEN = "1015617928:AAFVLXthgF6p27dk8ieh5Wo2lteLJhP0po4"
CHAT_ID = "@testtestphp"

def send_tg(text, disable_notification=True):
    url = f'https://api.telegram.org/bot{TOKEN}/sendMessage'
    payload = {
        'chat_id': CHAT_ID,
        'text': f'`{text}`',
        'parse_mode': 'Markdown',
        'disable_notification': disable_notification
    }

    response = requests.post(url, data=payload)
    return response.ok




