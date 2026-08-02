"""F-0108.06: a season-pack download must file every episode, not just one.

Follows directly from adding a season-pack search to find-missing: without
this, finding and downloading "Show S04" as a pack would file only the
largest file in it (the pre-existing single-episode logic) and leave the rest
sitting unfiled and unmentioned — the same shape of bug fixed for movie packs
in F-0108.07, here on the TV side.

Sandbox only — throwaway library root, staging dir, DB and recycle bin.
"""

import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
import medialibrary.downloads
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def setup():
    tmp = tempfile.mkdtemp()
    lib = os.path.join(tmp, 'TV Shows')
    staging = os.path.join(tmp, 'Downloads')
    trash = os.path.join(tmp, 'Trash')
    for d in (lib, staging, trash):
        os.makedirs(d, exist_ok=True)
    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    app.store.set_setting('tv_path', lib)
    medialibrary.downloads.send2trash = lambda p: shutil.move(
        p, os.path.join(trash, os.path.basename(p))
    )
    return lib, staging


def make(path, mb=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'\x00' * (mb * 1024 * 1024))
    return path


def add_show(show_path, title='A Show'):
    app.store.add_media_item(
        imdb_id='tt' + str(abs(hash(show_path)) % 10**7),
        tmdb_id=None,
        title=title,
        year=2010,
        media_type='tv',
        collection_id=None,
        collection_name=None,
        current_quality=None,
        path=show_path,
    )
    return app.store.get_media_item_by_imdb_id('tt' + str(abs(hash(show_path)) % 10**7))['id']


def row_for(media_id):
    return next(r for r in app.store.list_media_items() if r['id'] == media_id)


def start_download(media_id):
    app.store.set_download_state(
        media_id, 'downloading', 'qb_webui', 'x', torrent_hash='A' * 40, mode='fill'
    )


print('\n=== 1. Every episode in the pack is filed, not just the largest ===')
lib, staging = setup()
show_path = os.path.join(lib, 'A Show')
os.makedirs(show_path, exist_ok=True)  # a find-missing target always already exists
media_id = add_show(show_path)
start_download(media_id)

pack = os.path.join(staging, 'A.Show.S04.1080p')
make(os.path.join(pack, 'A.Show.S04E01.mkv'), mb=3)  # largest, deliberately not the only one
make(os.path.join(pack, 'A.Show.S04E02.mkv'), mb=1)
make(os.path.join(pack, 'A.Show.S04E03.mkv'), mb=1)

app._finalize_completed_download(
    row_for(media_id),
    {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': pack},
)

for n in (1, 2, 3):
    want = os.path.join(show_path, 'Season 04', f'A Show - S04E0{n}.mkv')
    check(f'S04E0{n} filed', os.path.isfile(want), want)
check('download state cleared', row_for(media_id)['download_status'] is None)

print('\n=== 2. One foreign-named file is refused; the rest still file ===')
lib, staging = setup()
show_path = os.path.join(lib, 'A Show')
os.makedirs(show_path, exist_ok=True)  # a find-missing target always already exists
media_id = add_show(show_path)
start_download(media_id)

pack = os.path.join(staging, 'A.Show.S04.1080p')
make(os.path.join(pack, 'A.Show.S04E01.mkv'))
make(os.path.join(pack, 'A.Show.S04E02.mkv'))
make(os.path.join(pack, 'Chernobyl.S04E03.mkv'))  # names a different show entirely

app._finalize_completed_download(
    row_for(media_id),
    {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': pack},
)

check('S04E01 filed', os.path.isfile(os.path.join(show_path, 'Season 04', 'A Show - S04E01.mkv')))
check('S04E02 filed', os.path.isfile(os.path.join(show_path, 'Season 04', 'A Show - S04E02.mkv')))
check(
    'the foreign file was not filed under this show',
    not os.path.isfile(os.path.join(show_path, 'Season 04', 'A Show - S04E03.mkv')),
)
check(
    'the foreign file is untouched in the pack folder',
    os.path.isfile(os.path.join(pack, 'Chernobyl.S04E03.mkv')),
)
row = row_for(media_id)
check('flagged for review, not silently cleared', row['download_status'] == 'needs_review')
check(
    'the message says how many filed and names the problem file',
    row['download_message']
    and 'Filed 2 of 3' in row['download_message']
    and 'Chernobyl' in row['download_message'],
    row['download_message'],
)

print('\n=== 3. A file no better than what is already held is reported, others still file ===')
lib, staging = setup()
show_path = os.path.join(lib, 'A Show')
media_id = add_show(show_path)
existing = make(
    os.path.join(show_path, 'Season 04', 'A Show - S04E02.mkv'), mb=5
)  # large = high quality
start_download(media_id)

pack = os.path.join(staging, 'A.Show.S04.1080p')
make(os.path.join(pack, 'A.Show.S04E01.mkv'), mb=2)
make(os.path.join(pack, 'A.Show.S04E02.mkv'), mb=1)  # smaller than what's already held

app._finalize_completed_download(
    row_for(media_id),
    {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': pack},
)
check(
    'S04E01 filed (nothing held before)',
    os.path.isfile(os.path.join(show_path, 'Season 04', 'A Show - S04E01.mkv')),
)
check('S04E02 was not overwritten with a smaller file', os.path.isfile(existing))
row = row_for(media_id)
check('flagged for review', row['download_status'] == 'needs_review')
check(
    'message explains E02 specifically',
    row['download_message'] and 'S04E02' in row['download_message'],
    row['download_message'],
)

print('\n=== 4. A placement failure leaves the download state untouched, for a retry ===')
lib, staging = setup()
show_path = os.path.join(lib, 'A Show')
os.makedirs(show_path, exist_ok=True)  # a find-missing target always already exists
media_id = add_show(show_path)
start_download(media_id)

pack = os.path.join(staging, 'A.Show.S04.1080p')
make(os.path.join(pack, 'A.Show.S04E01.mkv'))
make(os.path.join(pack, 'A.Show.S04E02.mkv'))

real_place = medialibrary.downloads._place_video_in_library


def failing_place(source, dest):
    if 'S04E02' in dest:
        return None  # simulate this one file failing to place
    return real_place(source, dest)


medialibrary.downloads._place_video_in_library = failing_place
try:
    app._finalize_completed_download(
        row_for(media_id),
        {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': pack},
    )
finally:
    medialibrary.downloads._place_video_in_library = real_place

check(
    'S04E01 filed despite E02 failing',
    os.path.isfile(os.path.join(show_path, 'Season 04', 'A Show - S04E01.mkv')),
)
check(
    'E02 was not filed',
    not os.path.isfile(os.path.join(show_path, 'Season 04', 'A Show - S04E02.mkv')),
)
check(
    'download state is left as downloading, not cleared or flagged, so the next pass retries',
    row_for(media_id)['download_status'] == 'downloading',
    row_for(media_id)['download_status'],
)

print('\n=== 5. A single-episode "pack" folder behaves exactly like a bare single file ===')
lib, staging = setup()
show_path = os.path.join(lib, 'A Show')
os.makedirs(show_path, exist_ok=True)  # a find-missing target always already exists
media_id = add_show(show_path)
start_download(media_id)

folder = os.path.join(staging, 'A.Show.S04E01.1080p')
make(os.path.join(folder, 'A.Show.S04E01.mkv'))
make(os.path.join(folder, 'sample.mkv'), mb=0)  # a non-episode extra should not confuse this

app._finalize_completed_download(
    row_for(media_id),
    {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': folder},
)
check(
    'the single episode filed as normal',
    os.path.isfile(os.path.join(show_path, 'Season 04', 'A Show - S04E01.mkv')),
)
check('download state cleared', row_for(media_id)['download_status'] is None)

print('\n=== 6. episode-candidates searches the season pack when episode is omitted ===')
from medialibrary.auth import _auth_username

lib, staging = setup()
show_path = os.path.join(lib, 'A Show')
os.makedirs(show_path, exist_ok=True)
media_id = add_show(show_path)

captured_queries = []


class StubQb:
    def set_mirror_urls(self, _urls):
        pass

    def _run_search(self, query):
        captured_queries.append(query)
        return [{'name': f'match for {query}', 'seeds': '10'}]


import medialibrary.web.tv as tv_web

tv_web.runtime.configure(store=lambda: app.store, tmdb=lambda: None, qb=lambda: StubQb())

client = app.app.test_client()
with client.session_transaction() as session:
    session['auth_user'] = _auth_username()

r_episode = client.get(f'/api/tv/{media_id}/episode-candidates?season=4&episode=2')
r_season = client.get(f'/api/tv/{media_id}/episode-candidates?season=4')

check(
    'single-episode search still builds SxxExx',
    captured_queries[0] == 'A Show S04E02',
    captured_queries,
)
check(
    'season-only search builds just Sxx, no episode',
    captured_queries[1] == 'A Show S04',
    captured_queries,
)
check('single-episode result title', r_episode.get_json()['title'] == 'A Show S04E02')
check(
    'season result title reads as a season, not "E00"',
    r_season.get_json()['title'] == 'A Show Season 4',
    r_season.get_json()['title'],
)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
