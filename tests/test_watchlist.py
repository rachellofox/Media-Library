"""F-0208.02: a local watchlist, stored in library.db, not synced from Trakt.

Covers the storage layer (medialibrary.storage watchlist_items methods) and
the Discover routes built on it: add, remove, the /api/discover strip data,
in_watchlist on the hero lookup, and that adding a title to the library drops
it from the watchlist automatically (owning it and wanting to watch it are
mutually exclusive states).
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary import tmdb_state
from medialibrary.auth import _auth_username
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


tmp = tempfile.mkdtemp()
app.store = Storage(os.path.join(tmp, 't.db'))
app.store.initialize()

client = app.app.test_client()
with client.session_transaction() as session:
    session['auth_user'] = _auth_username()


class FakeTmdb:
    """Just enough of TmdbClient for the watchlist routes under test."""

    def __init__(self, catalogue):
        self.catalogue = catalogue

    def metadata_by_tmdb_id(self, tmdb_id, media_type):
        return self.catalogue.get((int(tmdb_id), media_type), {})

    def trending(self):
        return []


INCEPTION = {
    'imdb_id': 'tt1375666',
    'tmdb_id': 27205,
    'title': 'Inception',
    'year': 2010,
    'media_type': 'movie',
    'poster_url': 'https://image.tmdb.org/inception.jpg',
    'synopsis': None,
    'actors': None,
    'genre_1': None,
    'genre_2': None,
    'collection_id': None,
    'collection_name': None,
}
BREAKING_BAD = {
    'imdb_id': 'tt0903747',
    'tmdb_id': 1396,
    'title': 'Breaking Bad',
    'year': 2008,
    'media_type': 'tv',
    'poster_url': 'https://image.tmdb.org/bb.jpg',
    'synopsis': None,
    'actors': None,
    'genre_1': None,
    'genre_2': None,
    'collection_id': None,
    'collection_name': None,
}
tmdb_state.set_client(FakeTmdb({(27205, 'movie'): INCEPTION, (1396, 'tv'): BREAKING_BAD}))


print('\n=== 1. Storage: add, list, is_in_watchlist, remove ===')
app.store.add_to_watchlist(
    tmdb_id=27205, media_type='movie', imdb_id='tt1375666', title='Inception', year=2010,
    poster_url='https://image.tmdb.org/inception.jpg',
)
check('now in the watchlist', app.store.is_in_watchlist(27205, 'movie') is True)
check(
    'not in the watchlist under the wrong media_type',
    app.store.is_in_watchlist(27205, 'tv') is False,
)
rows = app.store.list_watchlist()
check('one row', len(rows) == 1, rows)
check('title carried through', rows[0]['title'] == 'Inception')

print('\n=== 2. Storage: adding the same tmdb_id+media_type again updates, not duplicates ===')
app.store.add_to_watchlist(
    tmdb_id=27205, media_type='movie', imdb_id='tt1375666', title='Inception (2010 re-release)',
    year=2010, poster_url='https://image.tmdb.org/inception2.jpg',
)
rows = app.store.list_watchlist()
check('still one row', len(rows) == 1, rows)
check('fields updated in place', rows[0]['title'] == 'Inception (2010 re-release)', rows[0])

print('\n=== 3. Storage: remove ===')
app.store.remove_from_watchlist(27205, 'movie')
check('gone', app.store.is_in_watchlist(27205, 'movie') is False)
check('list is empty', app.store.list_watchlist() == [])

print('\n=== 4. Route: POST /api/discover/watchlist/add looks up TMDB, not client strings ===')
resp = client.post(
    '/api/discover/watchlist/add',
    json={'tmdb_id': 1396, 'media_type': 'tv'},
)
check('200 OK', resp.status_code == 200, resp.status_code)
payload = resp.get_json()
check('ok', payload.get('ok') is True, payload)
check('media_type echoed', payload.get('media_type') == 'tv', payload)
check(
    'stored with the TMDB title, not a client-supplied one',
    app.store.list_watchlist()[0]['title'] == 'Breaking Bad',
)

print('\n=== 5. Route: GET /api/discover reflects the local watchlist ===')
resp = client.get('/api/discover')
data = resp.get_json()
watchlist = data.get('watchlist') or []
check('one item in the watchlist strip', len(watchlist) == 1, watchlist)
check('is Breaking Bad', (watchlist[0] or {}).get('title') == 'Breaking Bad')

print('\n=== 6. Route: GET /api/discover/hero reports in_watchlist ===')
resp = client.get('/api/discover/hero?tmdb_id=1396&media_type=tv')
item = resp.get_json()['item']
check('in_watchlist true for a watchlisted title', item.get('in_watchlist') is True, item)
resp = client.get('/api/discover/hero?tmdb_id=27205&media_type=movie')
item = resp.get_json()['item']
check('in_watchlist false for one that is not', item.get('in_watchlist') is False, item)

print('\n=== 7. Route: POST /api/discover/watchlist/remove ===')
resp = client.post('/api/discover/watchlist/remove', json={'tmdb_id': 1396, 'media_type': 'tv'})
check('200 OK', resp.status_code == 200, resp.status_code)
check('removed', app.store.list_watchlist() == [])

print('\n=== 8. Adding a watchlisted title to the library removes it from the watchlist ===')
app.store.add_to_watchlist(
    tmdb_id=27205, media_type='movie', imdb_id='tt1375666', title='Inception', year=2010,
    poster_url='https://image.tmdb.org/inception.jpg',
)
check('watchlisted before adding to the library', app.store.is_in_watchlist(27205, 'movie') is True)
resp = client.post('/api/discover/add-and-search', json={'tmdb_id': 27205, 'media_type': 'movie'})
check(
    'add-and-search succeeded (or at least reached the library write)',
    resp.status_code in (200, 503),
    resp.status_code,
)
check(
    'no longer on the watchlist once owned',
    app.store.is_in_watchlist(27205, 'movie') is False,
)

print('\n=== 9. Validation: a bad tmdb_id or media_type is rejected, not silently ignored ===')
resp = client.post(
    '/api/discover/watchlist/add', json={'tmdb_id': 'not-a-number', 'media_type': 'movie'}
)
check('invalid tmdb_id -> 400', resp.status_code == 400, resp.status_code)
check('reason given', resp.get_json().get('error') == 'invalid_tmdb_id')

resp = client.post('/api/discover/watchlist/add', json={'tmdb_id': 27205, 'media_type': 'book'})
check('invalid media_type -> 400', resp.status_code == 400, resp.status_code)
check('reason given', resp.get_json().get('error') == 'invalid_media_type')

print('\n=== 10. TMDB not configured: add fails clearly, remove still works ===')
tmdb_state.set_client(None)
resp = client.post('/api/discover/watchlist/add', json={'tmdb_id': 1, 'media_type': 'movie'})
check('503 without TMDB', resp.status_code == 503, resp.status_code)
app.store.add_to_watchlist(tmdb_id=1, media_type='movie', title='Untitled')
resp = client.post('/api/discover/watchlist/remove', json={'tmdb_id': 1, 'media_type': 'movie'})
check('remove needs no TMDB client', resp.status_code == 200, resp.status_code)
check('removed', app.store.is_in_watchlist(1, 'movie') is False)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
