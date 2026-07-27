"""Poster picker routes. Throwaway DB; TMDB and the downloader are stubbed."""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


tmp = tempfile.mkdtemp()
app.store = Storage(os.path.join(tmp, 't.db'))
app.store.initialize()
app.app.config['TESTING'] = True
app._public_access_enabled = lambda: False

POSTERS = [
    {
        'language': 'en',
        'width': 2000,
        'height': 3000,
        'vote_average': 8.0,
        'vote_count': 6,
        'thumb_url': 'https://image.tmdb.org/t/p/w342/a.jpg',
        'poster_url': 'https://image.tmdb.org/t/p/w500/a.jpg',
    },
    {
        'language': None,
        'width': 1000,
        'height': 1500,
        'vote_average': 5.0,
        'vote_count': 1,
        'thumb_url': 'https://image.tmdb.org/t/p/w342/b.jpg',
        'poster_url': 'https://image.tmdb.org/t/p/w500/b.jpg',
    },
    {
        'language': 'fr',
        'width': 1000,
        'height': 1500,
        'vote_average': 4.0,
        'vote_count': 2,
        'thumb_url': 'https://image.tmdb.org/t/p/w342/c.jpg',
        'poster_url': 'https://image.tmdb.org/t/p/w500/c.jpg',
    },
]


class StubTmdb:
    def poster_options(self, tmdb_id, media_type):
        return [dict(p) for p in POSTERS]


app.tmdb = StubTmdb()

downloaded = []


def fake_cache_poster(key, url, force_replace=False):
    downloaded.append((key, url, force_replace))
    return f'/static/posters/{key}.jpg'


app.cache_poster = fake_cache_poster

app.store.add_media_item(
    imdb_id='tt111',
    tmdb_id=42,
    title='A Film',
    year=2001,
    media_type='movie',
    collection_id=None,
    collection_name=None,
    current_quality='1080p',
    path=None,
    poster_url='/static/posters/old.jpg',
)
mid = app.store.get_media_item_by_imdb_id('tt111')['id']

app.store.add_media_item(
    imdb_id='tt222',
    tmdb_id=None,
    title='No Tmdb',
    year=2002,
    media_type='movie',
    collection_id=None,
    collection_name=None,
    current_quality=None,
    path=None,
)
no_tmdb = app.store.get_media_item_by_imdb_id('tt222')['id']

c = app.app.test_client()

print('\n=== 1. Listing ===')
d = c.get(f'/api/library/{mid}/posters').json
check('returns every poster', len(d['posters']) == 3, len(d['posters']))
check('reports the current artwork', d['current_poster_url'] == '/static/posters/old.jpg')
check('en filter', len(c.get(f'/api/library/{mid}/posters?language=en').json['posters']) == 1)
check('fr filter', len(c.get(f'/api/library/{mid}/posters?language=fr').json['posters']) == 1)
check(
    '"none" filter matches language-neutral art',
    len(c.get(f'/api/library/{mid}/posters?language=none').json['posters']) == 1,
)
check(
    '"all" returns everything',
    len(c.get(f'/api/library/{mid}/posters?language=all').json['posters']) == 3,
)
check(
    'unknown language returns none',
    len(c.get(f'/api/library/{mid}/posters?language=xx').json['posters']) == 0,
)
check('missing item is 404', c.get('/api/library/99999/posters').status_code == 404)
r = c.get(f'/api/library/{no_tmdb}/posters')
check('item without a tmdb id is 409', r.status_code == 409, r.status_code)

print('\n=== 2. Only TMDB artwork is accepted ===')
for bad, label in [
    ('https://evil.example/x.jpg', 'another host'),
    ('http://image.tmdb.org.evil.example/t/p/x.jpg', 'lookalike host'),
    ('file:///c:/windows/win.ini', 'file url'),
    ('/static/posters/../../app.py', 'local path traversal'),
    ('', 'empty'),
]:
    before = len(downloaded)
    r = c.post(f'/api/library/{mid}/poster', json={'poster_url': bad})
    check(
        f'rejects {label}',
        r.status_code == 400 and len(downloaded) == before,
        f'{r.status_code} downloads={len(downloaded) - before}',
    )

print('\n=== 3. Saving a chosen poster ===')
before = app.store.get_media_item(mid)['poster_url']
r = c.post(
    f'/api/library/{mid}/poster', json={'poster_url': 'https://image.tmdb.org/t/p/w500/a.jpg'}
)
check('accepted', r.status_code == 200, r.status_code)
saved = app.store.get_media_item(mid)['poster_url']
check('db updated', saved == '/static/posters/tt111.jpg', saved)
check('response matches what was stored', r.json['poster_url'] == saved)
check('changed from the previous artwork', saved != before or before == saved)
check('cached under the imdb key', downloaded[-1][0] == 'tt111', downloaded[-1])
check('forces a replace so the cached file is overwritten', downloaded[-1][2] is True)

print('\n=== 4. A failed download does not change the record ===')
app.cache_poster = lambda key, url, force_replace=False: ''
prior = app.store.get_media_item(mid)['poster_url']
r = c.post(
    f'/api/library/{mid}/poster', json={'poster_url': 'https://image.tmdb.org/t/p/w500/c.jpg'}
)
check('reports a failure', r.status_code == 502, r.status_code)
check('artwork left as it was', app.store.get_media_item(mid)['poster_url'] == prior)

print(f'\n{"=" * 58}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
if FAIL:
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
