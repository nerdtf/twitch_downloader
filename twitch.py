import requests
from time import sleep
import utils
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

auth = {'Client-ID': str(utils.get_client_id()),
        'Authorization': 'Bearer ' + utils.get_app_access_token()}

def requests_retry_session(
    retries=3,
    backoff_factor=2,
    status_forcelist=(500, 502, 503, 504),
):
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


def get_user_info(user_login, *args: str) -> list:
    """
    Gets user info for user logins
    See https://dev.twitch.tv/docs/api/reference#get-users

    Parameters
    ----------
    user_login: str
        username string
    args: str
        additional string usernames (max. 99)

    Returns
    -------
    list
        contains user_info dicts
    """
    get_user_id_url = 'https://api.twitch.tv/helix/users?login=' + user_login
    if len(args) > 99:
        args = args[:99]
    for user_login_i in args:
        get_user_id_url += '&login=' + user_login_i
    session = requests_retry_session()
    try:
        r = session.get(get_user_id_url, headers=auth, timeout=20)
    except Exception as e:
        print(f"Error during API call: {e}")
        return []
    temp = r.json()
    return list(temp['data']) if temp['data'] else []


def get_stream_info(*user_ids):
    """
    Gets stream info for user ids
    See https://dev.twitch.tv/docs/api/reference#get-streams

    Parameters
    ----------
    user_id: str
        user id string
    args: str
        additional string user ids (max. 99)

    Returns
    -------
    list
        contains stream_info dicts
    """
    if len(user_ids) > 100:
        user_ids = user_ids[:100]
    base_url = 'https://api.twitch.tv/helix/streams?'
    auth_token = utils.get_app_access_token()  # Get the app access token
    headers = {
        'Client-ID': utils.get_client_id(),
        'Authorization': f'Bearer {auth_token}'
    }

    user_id_params = '&'.join([f'user_id={uid}' for uid in user_ids])
    full_url = base_url + user_id_params
    session = requests_retry_session()
    stream_info = {uid: {'status': 'offline'} for uid in user_ids} # Default to offline
    try:
        response = session.get(full_url, headers=headers, timeout=20)
        if response.status_code == 200:
            data = response.json().get('data', [])
            for stream in data:
                user_id = stream['user_id']
                stream_info[user_id] = {
                    'status': 'online',
                    'title': stream.get('title', 'No Title'),
                    'viewer_count': stream.get('viewer_count'),
                    'started_at': stream.get('started_at'),
                }
        else:
            print(f"Error fetching stream info: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error during API call: {e}")
    return stream_info
