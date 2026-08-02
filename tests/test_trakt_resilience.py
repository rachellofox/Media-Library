"""B-0108.03: a transient Trakt failure must not silently disconnect Trakt.

_refresh_trakt_token_if_needed cleared stored auth on ANY exception while
refreshing an expiring token - a network blip, a Trakt outage, a rate limit -
not just a token Trakt itself rejects. Since this refresh runs on the way to
loading the watchlist, a single bad moment near token expiry would disconnect
Trakt with nothing to say why: the watchlist would just go from "your titles"
to "connect Trakt" between one visit and the next.

Also checks _discover_watchlist tells "the request failed" apart from "the
watchlist is empty" and "not connected" - three different states a caller
needs to render three different messages for, previously collapsed to two.

Never touches the real OS keyring: _trakt_client_secret is monkeypatched
directly rather than going through keyring.get_password, since this machine's
keyring holds the real user's actual Trakt app secret.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary import discover, trakt_auth
from medialibrary.storage import Storage
from medialibrary.trakt_client import TraktRequestError

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def setup():
    tmp = tempfile.mkdtemp()
    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    app.store.set_setting('trakt_client_id', 'test-client-id')
    trakt_auth._trakt_client_secret = lambda: 'test-secret'  # never touch the real keyring


def store_expired_token():
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    trakt_auth._save_json_setting(
        trakt_auth.TRAKT_TOKEN_SETTING,
        {'access_token': 'old', 'refresh_token': 'refresh-me', 'expires_at': expired},
    )


class RaisingClient:
    """Stands in for TraktClient; exchange_refresh_token raises what's given."""

    def __init__(self, to_raise):
        self._to_raise = to_raise

    def __call__(self, *_a, **_k):
        return self

    def exchange_refresh_token(self, _refresh_token):
        raise self._to_raise


print('\n=== 1. A transient failure keeps the stored token ===')
for label, error in (
    ('network error', TraktRequestError('network_error')),
    ('rate limited', TraktRequestError('rate_limited', status_code=429)),
    ('server error', TraktRequestError('http_error', status_code=503)),
    ('unexpected exception', ValueError('malformed response')),
):
    setup()
    store_expired_token()
    trakt_auth.TraktClient = RaisingClient(error)

    result = trakt_auth._refresh_trakt_token_if_needed()
    check(f'{label}: refresh reports unavailable, not connected', result is None, result)
    still_there = trakt_auth._load_json_setting(trakt_auth.TRAKT_TOKEN_SETTING)
    check(f'{label}: the stored token survives', still_there is not None, still_there)

print('\n=== 2. A token Trakt itself rejects is cleared ===')
setup()
store_expired_token()
trakt_auth.TraktClient = RaisingClient(TraktRequestError('unauthorized', status_code=401))
result = trakt_auth._refresh_trakt_token_if_needed()
check('refresh reports unavailable', result is None)
check(
    'and the token is actually gone',
    trakt_auth._load_json_setting(trakt_auth.TRAKT_TOKEN_SETTING) is None,
)

print('\n=== 3. A successful refresh replaces the token ===')


class OkClient:
    def __init__(self, *_a, **_k):
        pass

    def exchange_refresh_token(self, refresh_token):
        assert refresh_token == 'refresh-me'
        return {'access_token': 'new-token', 'refresh_token': 'new-refresh', 'expires_in': 7200}


setup()
store_expired_token()
trakt_auth.TraktClient = OkClient
result = trakt_auth._refresh_trakt_token_if_needed()
check(
    'the new token is returned',
    result is not None and result['access_token'] == 'new-token',
    result,
)
check(
    'and persisted',
    trakt_auth._load_json_setting(trakt_auth.TRAKT_TOKEN_SETTING)['access_token'] == 'new-token',
)

print('\n=== 4. discover_watchlist tells apart no-client / failed / empty / has-items ===')


def with_client(stub):
    discover.configure(
        get_store=lambda: app.store, get_tmdb=lambda: None, get_trakt_client=lambda: stub
    )


class FailingTrakt:
    def watchlist_movies(self):
        raise ConnectionError('simulated outage')

    def watchlist_shows(self):
        return []


class EmptyTrakt:
    def watchlist_movies(self):
        return []

    def watchlist_shows(self):
        return []


setup()
discover.configure(
    get_store=lambda: app.store, get_tmdb=lambda: None, get_trakt_client=lambda: None
)
items, ok = discover._discover_watchlist()
check(
    'no client: empty list, reported as ok (not a failure)', items == [] and ok is True, (items, ok)
)

with_client(FailingTrakt())
items, ok = discover._discover_watchlist()
check('client raises: empty list, reported as NOT ok', items == [] and ok is False, (items, ok))

with_client(EmptyTrakt())
items, ok = discover._discover_watchlist()
check('client returns nothing: empty list, reported as ok', items == [] and ok is True, (items, ok))

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
