"""Trakt account state: credentials, the device flow, and the token.

The client id and username are ordinary settings; the client secret and the
OAuth token are secrets, so the secret lives in the OS keyring and the token in
the database. Every keyring read is guarded, because a machine with no keyring
backend should degrade to "not connected" rather than fail to start.

`trakt_context()` is what the pages render from — one dict describing whether
Trakt is configured, connected, and to whom.
"""

from datetime import datetime, timedelta, timezone

import keyring

from medialibrary import runtime
from medialibrary.settings_util import (
    _load_json_setting,
    _parse_iso_datetime,
    _save_json_setting,
    _utc_now,
)
from medialibrary.trakt_client import TraktClient, TraktRequestError

TRAKT_TOKEN_SETTING = 'trakt_oauth_token'
TRAKT_PROFILE_SETTING = 'trakt_oauth_profile'
TRAKT_DEVICE_SETTING = 'trakt_oauth_device'
TRAKT_SECRET_SERVICE = 'MediaLibrary'
TRAKT_SECRET_ACCOUNT = 'trakt_client_secret'


def _trakt_client_id() -> str:
    return (runtime.store().get_setting('trakt_client_id') or '').strip()


def _trakt_client_secret() -> str:
    try:
        stored_secret = keyring.get_password(TRAKT_SECRET_SERVICE, TRAKT_SECRET_ACCOUNT)
    except Exception:
        stored_secret = ''
    return (stored_secret or '').strip()


def _set_trakt_client_secret(secret: str) -> None:
    secret = (secret or '').strip()
    if not secret:
        return
    keyring.set_password(TRAKT_SECRET_SERVICE, TRAKT_SECRET_ACCOUNT, secret)


def _trakt_username() -> str:
    return (runtime.store().get_setting('trakt_username') or '').strip()


def _serialize_trakt_token(token: dict) -> dict:
    created_at = token.get('created_at')
    expires_in = int(token.get('expires_in') or 0)
    if created_at is not None:
        issued_at = datetime.fromtimestamp(int(created_at), tz=timezone.utc)
    else:
        issued_at = _utc_now()
    expires_at = issued_at + timedelta(seconds=expires_in)
    payload = dict(token)
    payload['expires_at'] = expires_at.isoformat()
    return payload


def _trakt_device_flow() -> dict | None:
    flow = _load_json_setting(TRAKT_DEVICE_SETTING)
    if not flow:
        return None
    expires_at = _parse_iso_datetime(flow.get('expires_at'))
    if expires_at and expires_at <= _utc_now():
        _save_json_setting(TRAKT_DEVICE_SETTING, None)
        return None
    return flow


def _clear_trakt_auth() -> None:
    _save_json_setting(TRAKT_TOKEN_SETTING, None)
    _save_json_setting(TRAKT_PROFILE_SETTING, None)
    _save_json_setting(TRAKT_DEVICE_SETTING, None)


def _refresh_trakt_token_if_needed() -> dict | None:
    """The current access token, refreshing it first if it is due to expire.

    Only a refresh Trakt itself rejects as invalid (401 'unauthorized') means
    the connection is actually gone, and only that clears it. Anything else —
    a network hiccup, a Trakt outage, a rate limit — left the stored token in
    place before this fixed it and disconnected Trakt on the next page load
    that happened to call this near expiry, with nothing to say why: the
    watchlist would just go from "your titles" to "connect Trakt" between one
    visit and the next. Failing this one refresh and trying again later is the
    honest response to a failure that says nothing about whether the token is
    still good.
    """
    token = _load_json_setting(TRAKT_TOKEN_SETTING)
    if not token:
        return None
    expires_at = _parse_iso_datetime(token.get('expires_at'))
    if expires_at and expires_at > (_utc_now() + timedelta(seconds=60)):
        return token
    client_id = _trakt_client_id()
    client_secret = _trakt_client_secret()
    if not client_id or not client_secret or not token.get('refresh_token'):
        # Nothing left to refresh with — this is a real disconnection, not a
        # transient failure, so clearing here is correct rather than cautious.
        _clear_trakt_auth()
        return None
    try:
        trakt = TraktClient(client_id=client_id, client_secret=client_secret)
        refreshed = _serialize_trakt_token(trakt.exchange_refresh_token(token['refresh_token']))
        _save_json_setting(TRAKT_TOKEN_SETTING, refreshed)
        return refreshed
    except TraktRequestError as exc:
        if exc.code == 'unauthorized':
            _clear_trakt_auth()
        return None
    except Exception:
        # DNS failure, timeout, malformed response — none of these are Trakt
        # saying the token is bad, so the token stays and this refresh is
        # simply treated as not having happened yet.
        return None


def _connected_trakt_profile() -> dict | None:
    profile = _load_json_setting(TRAKT_PROFILE_SETTING)
    return profile or None


def _trakt_client() -> TraktClient | None:
    client_id = _trakt_client_id()
    token = _refresh_trakt_token_if_needed()
    if not client_id or not token or not token.get('access_token'):
        return None
    return TraktClient(
        client_id=client_id,
        client_secret=_trakt_client_secret(),
        access_token=token['access_token'],
    )


def _trakt_display_username() -> str:
    profile = _connected_trakt_profile() or {}
    return profile.get('slug') or profile.get('username') or _trakt_username()


def _trakt_context() -> dict:
    client_id = _trakt_client_id()
    client_secret = _trakt_client_secret()
    username = _trakt_username()
    profile = _connected_trakt_profile() or {}
    return {
        'oauth_available': bool(client_id and client_secret),
        'connected': bool(_refresh_trakt_token_if_needed()),
        'configured': bool(client_id and client_secret),
        'username': _trakt_display_username(),
        'profile': profile,
        'device_flow': _trakt_device_flow(),
        'client_id': client_id,
        'saved_username': username,
        'secret_configured': bool(client_secret),
    }
