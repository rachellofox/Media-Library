"""F-0108.01: per-episode playback progress — resume, watched, next-episode.

playback_positions used to be keyed by media_item_id alone, so a show
remembered one position across every episode: watching S01E01 to the end and
starting S01E02 would show S01E02 as 45 minutes in. These check the storage
layer directly, the API surface the player calls, and the migration that
widens an existing single-row-per-item table without losing movie positions.

Sandbox only — throwaway DB, no Flask test client needed for the storage-layer
checks; the API checks use one.
"""

import os
import sqlite3
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import app
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def add_show(store, imdb, title):
    store.add_media_item(
        imdb_id=imdb,
        tmdb_id=None,
        title=title,
        year=2020,
        media_type='tv',
        collection_id=None,
        collection_name=None,
        current_quality=None,
        path=None,
    )
    return store.get_media_item_by_imdb_id(imdb)['id']


print('\n=== 1. Two episodes of one show keep separate positions ===')
with tempfile.TemporaryDirectory() as tmp:
    store = Storage(os.path.join(tmp, 't.db'))
    store.initialize()
    show_id = add_show(store, 'tt1', 'A Show')

    store.set_playback_position(show_id, 400, 1320, episode_key='S01/E01.mkv')
    store.set_playback_position(show_id, 30, 1320, episode_key='S01/E02.mkv')

    e1 = store.get_playback_position(show_id, 'S01/E01.mkv')
    e2 = store.get_playback_position(show_id, 'S01/E02.mkv')
    check('episode 1 keeps its own position', e1['position_seconds'] == 400)
    check('episode 2 keeps its own position', e2['position_seconds'] == 30)
    check('positions did not merge', e1['position_seconds'] != e2['position_seconds'])

print('\n=== 2. Watched is derived from position, and sticks once set ===')
with tempfile.TemporaryDirectory() as tmp:
    store = Storage(os.path.join(tmp, 't.db'))
    store.initialize()
    show_id = add_show(store, 'tt2', 'A Show')

    store.set_playback_position(show_id, 700, 1320, episode_key='S01/E01.mkv')
    check(
        'not watched partway through',
        not store.get_playback_position(show_id, 'S01/E01.mkv')['watched'],
    )

    store.set_playback_position(show_id, 1300, 1320, episode_key='S01/E01.mkv')
    check(
        'watched once past the threshold',
        store.get_playback_position(show_id, 'S01/E01.mkv')['watched'],
    )

    # Rewinding to review a scene should not un-mark a finished episode.
    store.set_playback_position(show_id, 200, 1320, episode_key='S01/E01.mkv')
    row = store.get_playback_position(show_id, 'S01/E01.mkv')
    check('still watched after rewinding', row['watched'])
    check('position still updates while rewinding', row['position_seconds'] == 200)

print('\n=== 3. list_playback_positions reports every episode of a show at once ===')
with tempfile.TemporaryDirectory() as tmp:
    store = Storage(os.path.join(tmp, 't.db'))
    store.initialize()
    show_id = add_show(store, 'tt3', 'A Show')
    other_id = add_show(store, 'tt4', 'Another Show')

    store.set_playback_position(show_id, 1300, 1320, episode_key='S01/E01.mkv')
    store.set_playback_position(show_id, 200, 1320, episode_key='S01/E02.mkv')
    store.set_playback_position(other_id, 900, 1320, episode_key='S01/E01.mkv')

    positions = store.list_playback_positions(show_id)
    check(
        'both episodes of this show are present', set(positions) == {'S01/E01.mkv', 'S01/E02.mkv'}
    )
    check("another show's position is not mixed in", len(positions) == 2)
    check('watched flag carries through the list', positions['S01/E01.mkv']['watched'])

print('\n=== 4. Movies use the empty episode_key and are unaffected ===')
with tempfile.TemporaryDirectory() as tmp:
    store = Storage(os.path.join(tmp, 't.db'))
    store.initialize()
    store.add_media_item(
        imdb_id='tt5',
        tmdb_id=None,
        title='A Film',
        year=2020,
        media_type='movie',
        collection_id=None,
        collection_name=None,
        current_quality=None,
        path=None,
    )
    movie_id = store.get_media_item_by_imdb_id('tt5')['id']

    store.set_playback_position(movie_id, 500, 6000)  # no episode_key passed
    row = store.get_playback_position(movie_id)
    check('a movie position round-trips with no episode key', row['position_seconds'] == 500)
    store.set_playback_position(movie_id, 900, 6000)
    check(
        'updating a movie position overwrites, not duplicates',
        store.get_playback_position(movie_id)['position_seconds'] == 900,
    )

print('\n=== 5. Migrating an existing single-row-per-item table ===')
with tempfile.TemporaryDirectory() as tmp:
    db_path = os.path.join(tmp, 't.db')
    # Build the OLD schema by hand, as a real pre-upgrade database would have it.
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE media_items (id INTEGER PRIMARY KEY, imdb_id TEXT UNIQUE, title TEXT,
                                   media_type TEXT, path TEXT);
        CREATE TABLE playback_positions (
            media_item_id INTEGER PRIMARY KEY,
            position_seconds REAL NOT NULL,
            duration_seconds REAL,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(media_item_id) REFERENCES media_items(id)
        );
        INSERT INTO media_items (id, imdb_id, title, media_type)
        VALUES (1, 'tt9', 'Old Film', 'movie');
        INSERT INTO playback_positions (media_item_id, position_seconds, duration_seconds)
        VALUES (1, 1234, 5000);
    """)
    con.commit()
    con.close()

    store = Storage(db_path)
    store.initialize()  # runs the migration
    row = store.get_playback_position(1)
    check(
        'the old position survives the migration',
        row is not None and row['position_seconds'] == 1234,
    )
    check(
        'a second episode can now be added for the same item',
        store.set_playback_position(1, 10, 100, episode_key='some/episode.mkv') is None,
    )
    check(
        'the migrated row and the new episode do not collide',
        store.get_playback_position(1)['position_seconds'] == 1234,
    )

    store.initialize()  # must be idempotent — a second startup should not re-migrate or fail
    check(
        're-running initialize does not disturb the migrated data',
        store.get_playback_position(1)['position_seconds'] == 1234,
    )

print('\n=== 6. The playback API is keyed by ?episode= ===')
with tempfile.TemporaryDirectory() as tmp:
    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    show_id = add_show(app.store, 'tt6', 'A Show')
    client = app.app.test_client()
    from medialibrary.auth import _auth_username

    with client.session_transaction() as session:
        session['auth_user'] = _auth_username()

    client.post(
        f'/api/video/{show_id}/playback?episode=S01/E01.mkv',
        json={'position_seconds': 500, 'duration_seconds': 1200},
    )
    client.post(
        f'/api/video/{show_id}/playback?episode=S01/E02.mkv',
        json={'position_seconds': 20, 'duration_seconds': 1200},
    )

    r1 = client.get(f'/api/video/{show_id}/playback?episode=S01/E01.mkv').get_json()
    r2 = client.get(f'/api/video/{show_id}/playback?episode=S01/E02.mkv').get_json()
    check('episode 1 position via the API', r1['position_seconds'] == 500)
    check('episode 2 position via the API', r2['position_seconds'] == 20)
    check(
        'a third, never-played episode reports zero, not an error',
        client.get(f'/api/video/{show_id}/playback?episode=S01/E03.mkv').get_json()[
            'position_seconds'
        ]
        == 0,
    )

print('\n=== 7. Season list carries resume/watched per episode ===')
with tempfile.TemporaryDirectory() as tmp:
    show_path = os.path.join(tmp, 'A Show')
    season_path = os.path.join(show_path, 'Season 01')
    os.makedirs(season_path, exist_ok=True)
    for name in (
        'A Show - S01E01 - First.mkv',
        'A Show - S01E02 - Second.mkv',
        'A Show - S01E03 - Third.mkv',
    ):
        with open(os.path.join(season_path, name), 'wb') as handle:
            handle.write(b'\0' * 1024)

    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    app.store.add_media_item(
        imdb_id='tt7',
        tmdb_id=None,
        title='A Show',
        year=2020,
        media_type='tv',
        collection_id=None,
        collection_name=None,
        current_quality=None,
        path=show_path,
    )
    show_id = app.store.get_media_item_by_imdb_id('tt7')['id']

    app.store.set_playback_position(
        show_id, 1300, 1320, episode_key=os.path.join('Season 01', 'A Show - S01E01 - First.mkv')
    )
    app.store.set_playback_position(
        show_id, 200, 1320, episode_key=os.path.join('Season 01', 'A Show - S01E02 - Second.mkv')
    )

    client = app.app.test_client()
    from medialibrary.auth import _auth_username

    with client.session_transaction() as session:
        session['auth_user'] = _auth_username()

    data = client.get(f'/api/tv/{show_id}/season/1').get_json()
    by_number = {e['episode_number']: e for e in data['episodes']}
    check('3 episodes returned', len(data['episodes']) == 3, data['episodes'])
    check('watched episode is marked watched', by_number[1]['watched'] is True)
    check(
        'watched episode has no resume_seconds (finished, not resumable)',
        by_number[1]['resume_seconds'] is None,
    )
    check('partway episode carries its resume point', by_number[2]['resume_seconds'] == 200)
    check(
        'never-started episode has no resume_seconds',
        by_number[3]['resume_seconds'] is None and by_number[3]['watched'] is False,
    )

    print('\n=== 8. next-episode follows the season list order, not the filename ===')
    nxt = client.get(
        f'/api/tv/{show_id}/next-episode?episode='
        + os.path.join('Season 01', 'A Show - S01E01 - First.mkv')
    ).get_json()
    check('next after E01 is E02', nxt['ok'] and nxt['next']['episode_number'] == 2, nxt)

    nxt3 = client.get(
        f'/api/tv/{show_id}/next-episode?episode='
        + os.path.join('Season 01', 'A Show - S01E03 - Third.mkv')
    ).get_json()
    check('no next after the last episode', nxt3['ok'] and nxt3['next'] is None, nxt3)

    nxt_bad = client.get(
        f'/api/tv/{show_id}/next-episode?episode=not-a-real-episode.mkv'
    ).get_json()
    check(
        'an unparseable episode reports no next rather than erroring',
        nxt_bad['ok'] and nxt_bad['next'] is None,
        nxt_bad,
    )

    missing = client.get(f'/api/tv/{show_id}/next-episode').get_json()
    check(
        'no episode param at all is handled the same way',
        missing['ok'] and missing['next'] is None,
        missing,
    )

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
