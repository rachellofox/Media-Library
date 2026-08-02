"""B-0108.04: TV must never be offered a quality check that goes nowhere.

The upgrade card's search is title-and-year for one release — a movie's
shape. On a TV show it used to run anyway: check_quality has no media_type
guard, so it could report "found" for a title+year search that means nothing
for a show, the toast said "Higher quality found.", and the client's own
retryLibraryDownloadSearch — which is what would open the torrent pane next —
silently no-ops for anything but a movie. The result matched the report
exactly: "reports complete without opening the torrent pane".

Fixed at _upgrade_available, the one place both the grid badge and the hero
card's visibility are decided, plus the route itself for a client that still
asks. Checks both layers, and that a movie is completely unaffected.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary.auth import _auth_username
from medialibrary.items import _upgrade_available
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


tmp = tempfile.mkdtemp()
app.store = Storage(os.path.join(tmp, 't.db'))
app.store.initialize()

app.store.add_media_item(
    imdb_id='tt-tv',
    tmdb_id=None,
    title='A Show',
    year=2020,
    media_type='tv',
    collection_id=None,
    collection_name=None,
    current_quality='720p',
    path=None,
)
tv_id = app.store.get_media_item_by_imdb_id('tt-tv')['id']

app.store.add_media_item(
    imdb_id='tt-movie',
    tmdb_id=None,
    title='A Film',
    year=2020,
    media_type='movie',
    collection_id=None,
    collection_name=None,
    current_quality='720p',
    path=None,
)
movie_id = app.store.get_media_item_by_imdb_id('tt-movie')['id']

# Both get an identical "found a better release" result recorded, so the only
# variable between them is media_type.
for media_id in (tv_id, movie_id):
    app.store.add_quality_check(
        media_item_id=media_id,
        best_found_quality='2160p',
        best_found_name='A Title 2160p',
        best_found_desc_link='',
        found=True,
        raw_result_count=1,
    )


def row(media_id):
    return next(r for r in app.store.list_media_items() if r['id'] == media_id)


print('\n=== 1. _upgrade_available: same recorded result, different media_type ===')
check('TV: never offered, regardless of what was found', _upgrade_available(row(tv_id)) is False)
check('movie: genuinely has an upgrade', _upgrade_available(row(movie_id)) is True)

print('\n=== 2. /check/<id> rejects a TV item outright ===')
client = app.app.test_client()
with client.session_transaction() as session:
    session['auth_user'] = _auth_username()

r_tv = client.post(f'/check/{tv_id}', headers={'X-Requested-With': 'fetch'})
check('TV: rejected with a clear reason, not run', r_tv.status_code == 400, r_tv.status_code)
check(
    'the reason says why, not just that it failed',
    r_tv.get_json().get('error') == 'not_a_movie',
    r_tv.get_json(),
)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
