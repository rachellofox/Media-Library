"""Duplicates are reported, never resolved.

The app used to keep the largest of two files claiming one episode and drop the
other with no record, so a library could hold 78 duplicates and say nothing.
These check the reporting, and — just as important — that reporting them did not
change which file the episode list shows or start deleting anything.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medialibrary.identify import duplicate_episode_files, scan_local_episodes

failures = []


def check(name, condition):
    if not condition:
        failures.append(name)
    print(f'{"ok  " if condition else "FAIL"}  {name}')


def make(folder, name, size):
    path = os.path.join(folder, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(b'\0' * size)
    return path


with tempfile.TemporaryDirectory() as show:
    season = os.path.join(show, 'Season 01')
    small = make(season, 'A Show - S01E01 - Pilot.mp4', 1000)
    large = make(season, 'A Show - S01E01 - Pilot.mkv', 5000)
    only = make(season, 'A Show - S01E02 - Second.mkv', 2000)
    extra = make(season, 'Behind the Scenes.mkv', 500)

    dupes = duplicate_episode_files(show, 'A Show')
    check('the duplicated episode is reported', list(dupes) == [(1, 1)])
    check('an episode held once is not reported', (1, 2) not in dupes)
    check('both copies are listed', sorted(dupes[(1, 1)]) == sorted([small, large]))
    check('largest copy is listed first', dupes[(1, 1)][0] == large)

    matched, unmatched = scan_local_episodes(show, 'A Show')
    check('the episode list still shows the largest', matched[(1, 1)] == large)
    check('the single-copy episode is unaffected', matched[(1, 2)] == only)
    check('the featurette is still unmatched', unmatched == [extra])

    check('nothing was deleted', all(os.path.exists(p) for p in (small, large, only, extra)))

    # A show with no duplicates must report none, or the flag cries wolf on
    # every show in the library.
    os.remove(small)
    check('no duplicates once the second copy goes', duplicate_episode_files(show, 'A Show') == {})

with tempfile.TemporaryDirectory() as show:
    # A file naming a different show is unmatched, so it can never be counted as
    # a duplicate of the episode its marker claims.
    season = os.path.join(show, 'Season 01')
    make(season, 'Parks and Recreation - S01E01 - Pilot.mkv', 3000)
    make(season, 'Chernobyl - S01E01 - Something.mkv', 9000)
    dupes = duplicate_episode_files(show, 'Parks and Recreation')
    check('a different show is not a duplicate', dupes == {})

with tempfile.TemporaryDirectory() as empty:
    check('an empty folder reports none', duplicate_episode_files(empty, 'Nothing') == {})
check('a missing folder reports none', duplicate_episode_files(r'Z:\nope', 'Nothing') == {})


# The count has to reach the page, not just exist. Throwaway DB and folders, so
# nothing here touches the real library.
import app
from medialibrary.auth import _auth_username
from medialibrary.storage import Storage

workspace = tempfile.mkdtemp()
show_folder = os.path.join(workspace, 'TV Shows', 'A Show')
make(os.path.join(show_folder, 'Season 01'), 'A Show - S01E01 - Pilot.mp4', 1000)
make(os.path.join(show_folder, 'Season 01'), 'A Show - S01E01 - Pilot.mkv', 5000)
make(os.path.join(show_folder, 'Season 01'), 'A Show - S01E02 - Second.mkv', 2000)

app.store = Storage(os.path.join(workspace, 'test.db'))
app.store.initialize()
app.store.add_media_item(
    imdb_id='tt0000001',
    tmdb_id=None,
    title='A Show',
    year=2001,
    media_type='tv',
    collection_id=None,
    collection_name=None,
    current_quality='1080p',
    path=show_folder,
)
media_id = app.store.list_media_items()[0]['id']

client = app.app.test_client()
with client.session_transaction() as session:
    session['auth_user'] = _auth_username()
response = client.get(f'/api/tv/{media_id}/seasons')
check('the seasons endpoint answers', response.status_code == 200)
payload = response.get_json() if response.status_code == 200 else {}
check('duplicate_count reaches the page', payload.get('duplicate_count') == 1)
check('the season still counts two episodes', payload['seasons'][0]['owned_count'] == 2)

print(f'\n{"FAILED: " + ", ".join(failures) if failures else "all passed"}')
sys.exit(1 if failures else 0)
