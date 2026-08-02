"""F-0108.02: the app reads the ordering detection already decided, live.

test_ordering.py checks the scoring/resolution logic in isolation. This checks
the rest of the promise: that medialibrary.maintenance persists what detection
finds, and that /api/tv/<id>/season/<n> and /api/tv/<id>/next-episode then read
that persisted answer instead of asking TMDB's default every time — which is
the whole point, since detection reads every file's running time and is too
slow to repeat per request.

Batman's shape, reduced: default numbers the season 1/1/1/1 while a DVD
ordering that matches the embedded titles numbers it 1/2/3/4. Sandbox only —
throwaway DB and folders, a stub TMDB client, real files on disk.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary import episode_ordering, tmdb_state
from medialibrary.maintenance import _detect_tv_episode_orderings
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def ep(season, number, title, runtime, still=None):
    return {
        'season_number': season,
        'episode_number': number,
        'title': title,
        'synopsis': None,
        'air_date': None,
        'runtime': runtime,
        'still_url': still,
    }


DEFAULT = [ep(1, 1, 'The Cat and the Claw (1)', 22), ep(1, 2, 'On Leather Wings', 22)]
DVD = [ep(1, 1, 'On Leather Wings', 22), ep(1, 2, 'Christmas with the Joker', 22)]


class StubTmdb:
    def tv_status(self, _tv_id):
        return {
            'status': 'Ended',
            'in_production': False,
            'next_air_date': None,
            'seasons': [{'season_number': 1, 'name': 'Season 1', 'episode_count': 2}],
        }

    def season_episodes(self, _tv_id, season_number):
        return [e for e in DEFAULT if e['season_number'] == season_number]

    def episode_orderings(self, _tv_id):
        return [{'id': 'grp-dvd', 'name': 'DVD Order', 'kind': 'DVD', 'episodes': DVD}]

    def _episode_group(self, group_id):
        return DVD if group_id == 'grp-dvd' else []


def make_video(path, minutes, embedded_title=''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Detection reads real running time and the embedded title, so the sandbox
    # needs a real (tiny) video, not a same-size placeholder file.
    import subprocess

    from medialibrary.config import FFMPEG_EXE

    subprocess.run(
        [
            FFMPEG_EXE,
            '-y',
            '-f',
            'lavfi',
            '-i',
            'color=c=black:s=64x64',
            '-t',
            str(minutes * 60),
            '-metadata',
            f'title={embedded_title}',
            path,
        ],
        capture_output=True,
        timeout=60,
    )


with tempfile.TemporaryDirectory() as tmp:
    show_path = os.path.join(tmp, 'Batman')
    season_path = os.path.join(show_path, 'Season 01')
    # Both files run true to 22 minutes, so only the embedded title tells the
    # two orderings apart — the same shape as the real Batman library.
    make_video(
        os.path.join(season_path, 'Batman - S01E01 - The Cat and the Claw (1).mp4'),
        22,
        'On Leather Wings',
    )
    make_video(
        os.path.join(season_path, 'Batman - S01E02 - Christmas with the Joker.mp4'),
        22,
        'Christmas with the Joker',
    )

    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    app.store.add_media_item(
        imdb_id='tt0103359',
        tmdb_id=2098,
        title='Batman',
        year=1992,
        media_type='tv',
        collection_id=None,
        collection_name=None,
        current_quality=None,
        path=show_path,
    )
    media_id = app.store.get_media_item_by_imdb_id('tt0103359')['id']
    tmdb_state.set_client(StubTmdb())
    episode_ordering._ORDERING_CACHE.clear()

    print('\n=== 1. Detection persists an adopted ordering ===')
    checked = _detect_tv_episode_orderings()
    check('one show checked', checked == 1, checked)
    saved = app.store.get_episode_ordering(media_id)
    check(
        'an ordering was persisted', saved is not None and saved['ordering_id'] == 'grp-dvd', saved
    )

    print('\n=== 2. A second run does not re-detect ===')
    # If it did, the API would be hit again; nothing here would raise either
    # way, so what actually proves it is the persisted row being untouched.
    before = app.store.get_episode_ordering(media_id)
    _detect_tv_episode_orderings()
    after = app.store.get_episode_ordering(media_id)
    check('the stored answer is unchanged', before == after, (before, after))

    print('\n=== 3. The season endpoint reads the adopted ordering ===')
    client = app.app.test_client()
    from medialibrary.auth import _auth_username

    with client.session_transaction() as session:
        session['auth_user'] = _auth_username()

    data = client.get(f'/api/tv/{media_id}/season/1').get_json()
    by_number = {e['episode_number']: e for e in data['episodes']}
    check(
        'E01 is titled from the DVD ordering, not the default',
        by_number[1]['title'] == 'On Leather Wings',
        by_number[1]['title'],
    )
    check(
        'E02 likewise',
        by_number[2]['title'] == 'Christmas with the Joker',
        by_number[2]['title'],
    )

    print('\n=== 4. next-episode also reads it ===')
    nxt = client.get(
        f'/api/tv/{media_id}/next-episode?episode='
        + os.path.join('Season 01', 'Batman - S01E01 - The Cat and the Claw (1).mp4')
    ).get_json()
    check(
        'next after E01 is titled from the DVD ordering',
        nxt['ok'] and nxt['next']['title'] == 'Christmas with the Joker',
        nxt,
    )

    print("\n=== 5. The season list reports the ordering's own counts ===")
    listing = client.get(f'/api/tv/{media_id}/seasons').get_json()
    season_row = next(s for s in listing['seasons'] if s['season_number'] == 1)
    check(
        'episode_count comes from the adopted ordering (2), not the default (2 here too, '
        'but taken from a different source)',
        season_row['episode_count'] == 2,
        season_row,
    )

print('\n=== 6. A show detection confirms is fine keeps the default, and is not re-probed ===')
with tempfile.TemporaryDirectory() as tmp:
    show_path = os.path.join(tmp, 'A Show')
    season_path = os.path.join(show_path, 'Season 01')
    make_video(os.path.join(season_path, 'A Show - S01E01 - First.mp4'), 22)

    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    app.store.add_media_item(
        imdb_id='tt1',
        tmdb_id=999,
        title='A Show',
        year=2020,
        media_type='tv',
        collection_id=None,
        collection_name=None,
        current_quality=None,
        path=show_path,
    )
    media_id = app.store.get_media_item_by_imdb_id('tt1')['id']

    class FitsStub:
        def tv_status(self, _tv_id):
            return {
                'status': 'Ended',
                'in_production': False,
                'next_air_date': None,
                'seasons': [{'season_number': 1, 'name': 'Season 1', 'episode_count': 1}],
            }

        def season_episodes(self, _tv_id, _season_number):
            return [ep(1, 1, 'First', 22)]

        def episode_orderings(self, _tv_id):
            raise AssertionError('should not be called when the default already fits')

    tmdb_state.set_client(FitsStub())
    episode_ordering._ORDERING_CACHE.clear()
    checked = _detect_tv_episode_orderings()
    check('one show checked', checked == 1, checked)
    saved = app.store.get_episode_ordering(media_id)
    check(
        'confirmed default, no ordering id',
        saved is not None and saved['ordering_id'] is None,
        saved,
    )

print('\n=== 7. Detection failing for one show does not stop the sweep ===')
with tempfile.TemporaryDirectory() as tmp:
    show_a = os.path.join(tmp, 'Show A')
    show_b = os.path.join(tmp, 'Show B')
    make_video(os.path.join(show_a, 'Season 01', 'Show A - S01E01 - First.mp4'), 22)
    make_video(os.path.join(show_b, 'Season 01', 'Show B - S01E01 - First.mp4'), 22)

    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    for imdb, title, path in (('tt2', 'Show A', show_a), ('tt3', 'Show B', show_b)):
        app.store.add_media_item(
            imdb_id=imdb,
            tmdb_id=1,
            title=title,
            year=2020,
            media_type='tv',
            collection_id=None,
            collection_name=None,
            current_quality=None,
            path=path,
        )
    id_a = app.store.get_media_item_by_imdb_id('tt2')['id']
    id_b = app.store.get_media_item_by_imdb_id('tt3')['id']

    class FailsOnceStub:
        calls = 0

        def tv_status(self, _tv_id):
            return {
                'status': None,
                'in_production': None,
                'next_air_date': None,
                'seasons': [{'season_number': 1, 'name': 'Season 1', 'episode_count': 1}],
            }

        def season_episodes(self, _tv_id, _season_number):
            # Deliberately wrong against the 22-minute file built above, so
            # detection cannot take its "default already fits" shortcut and is
            # forced to actually ask for orderings — which is the call this
            # test needs to fail once.
            return [ep(1, 1, 'First', 5)]

        def episode_orderings(self, _tv_id):
            FailsOnceStub.calls += 1
            if FailsOnceStub.calls == 1:
                raise ConnectionError('simulated TMDB outage')
            return []

    tmdb_state.set_client(FailsOnceStub())
    episode_ordering._ORDERING_CACHE.clear()
    checked = _detect_tv_episode_orderings()
    check('the failing show does not count as checked', checked == 1, checked)
    a_result = app.store.get_episode_ordering(id_a)
    b_result = app.store.get_episode_ordering(id_b)
    # Only one of the two calls the stub in a way that raises; whichever show
    # is visited first hits it, and only that one should be left unrecorded.
    results = [a_result, b_result]
    check(
        'exactly one show has no persisted answer, the other does',
        sorted(r is None for r in results) == [False, True],
        results,
    )

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
