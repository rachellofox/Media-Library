"""The TMDB client, and the key that creates it.

The client is rebuilt whenever the API key changes, so nothing may hold the
object itself — `client()` is the only way to reach it, and every caller goes
through that. Holding the instance is how a stale client survives a key change
and quietly returns nothing.

The key is kept in the OS keyring, read behind a guard so a machine with no
keyring backend reports "not configured" rather than failing to start.
"""

import keyring

from medialibrary.tmdb_client import TmdbClient

# Rebuilt by _refresh_tmdb_client(); never held by anything else.
_client = None


def client():
    """The current TMDB client, or None when no key is configured."""
    return _client


def set_client(new_client) -> None:
    """Replace the client outright.

    For the tests, which supply a stub rather than a key. Production code calls
    _refresh_tmdb_client(), which builds one from the stored key.
    """
    global _client
    _client = new_client


TMDB_SECRET_SERVICE = 'MediaLibrary'

TMDB_SECRET_ACCOUNT = 'tmdb_api_key'


def _tmdb_api_key() -> str:
    try:
        stored_key = keyring.get_password(TMDB_SECRET_SERVICE, TMDB_SECRET_ACCOUNT)
    except Exception:
        stored_key = ''
    return (stored_key or '').strip()


def _set_tmdb_api_key(api_key: str) -> None:
    api_key = (api_key or '').strip()
    if not api_key:
        return
    keyring.set_password(TMDB_SECRET_SERVICE, TMDB_SECRET_ACCOUNT, api_key)


def _refresh_tmdb_client() -> None:
    global _client
    api_key = _tmdb_api_key()
    _client = TmdbClient(api_key=api_key) if api_key else None
