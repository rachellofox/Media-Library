"""Missing-episode discovery. Stubbed TMDB, throwaway DB and temp folders."""
import datetime
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}{("  -- " + str(detail)) if detail and not cond else ""}')


TODAY = datetime.date.today()
YESTERDAY = (TODAY - datetime.timedelta(days=1)).isoformat()
NEXT_WEEK = (TODAY + datetime.timedelta(days=7)).isoformat()

tmp = tempfile.mkdtemp()
lib = os.path.join(tmp, 'TV Shows')
os.makedirs(lib, exist_ok=True)
app.store = Storage(os.path.join(tmp, 't.db'))
app.store.initialize()
app.store.set_setting('tv_path', lib)
app.store.set_setting('movies_path', os.path.join(tmp, 'Movies'))

requests = {'status': 0, 'episodes': 0}


class StubTmdb:
    """Two shows: one airing with a part-aired season, one ended with a gap."""

    SHOWS = {
        100: {'status': 'Returning Series', 'in_production': True, 'next_air_date': NEXT_WEEK,
              'seasons': [
                  {'season_number': 0, 'name': 'Specials', 'episode_count': 2},
                  {'season_number': 1, 'name': 'Season 1', 'episode_count': 3},
                  {'season_number': 2, 'name': 'Season 2', 'episode_count': 4},
              ]},
        200: {'status': 'Ended', 'in_production': False, 'next_air_date': None,
              'seasons': [{'season_number': 1, 'name': 'Season 1', 'episode_count': 2}]},
    }
    EPISODES = {
        (100, 0): [{'episode_number': 1, 'title': 'Special', 'air_date': YESTERDAY},
                   {'episode_number': 2, 'title': 'Special 2', 'air_date': YESTERDAY}],
        (100, 1): [{'episode_number': n, 'title': f'S1 Ep{n}', 'air_date': YESTERDAY}
                   for n in (1, 2, 3)],
        # Season 2 is mid-broadcast: E1 and E2 aired, E3 has no date, E4 is future.
        (100, 2): [{'episode_number': 1, 'title': 'S2 Ep1', 'air_date': YESTERDAY},
                   {'episode_number': 2, 'title': 'S2 Ep2', 'air_date': YESTERDAY},
                   {'episode_number': 3, 'title': 'S2 Ep3', 'air_date': None},
                   {'episode_number': 4, 'title': 'S2 Ep4', 'air_date': NEXT_WEEK}],
        (200, 1): [{'episode_number': 1, 'title': 'Part One', 'air_date': YESTERDAY},
                   {'episode_number': 2, 'title': 'Part Two', 'air_date': YESTERDAY}],
    }

    def tv_status(self, tv_id):
        requests['status'] += 1
        return dict(self.SHOWS[int(tv_id)])

    def season_episodes(self, tv_id, season_number):
        requests['episodes'] += 1
        return [dict(e) for e in self.EPISODES.get((int(tv_id), int(season_number)), [])]


app.tmdb = StubTmdb()


def make(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'\x00' * 2048)


def add(imdb, title, tmdb_id, path):
    app.store.add_media_item(imdb_id=imdb, tmdb_id=tmdb_id, title=title, year=2010,
                             media_type='tv', collection_id=None, collection_name=None,
                             current_quality='1080p', path=path)


# Airing show: owns all of S1, and S2E01 only.
airing = os.path.join(lib, 'Airing Show')
for n in (1, 2, 3):
    make(os.path.join(airing, 'Season 01', f'Airing Show - S01E{n:02}.mkv'))
make(os.path.join(airing, 'Season 02', 'Airing Show - S02E01.mkv'))
add('tt100', 'Airing Show', 100, airing)

# Ended show: a single file covering both episodes.
ended = os.path.join(lib, 'Ended Show')
make(os.path.join(ended, 'Season 01', 'Ended Show - S01E01-E02 - Double.mkv'))
add('tt200', 'Ended Show', 200, ended)

print('\n=== 1. Only aired, unowned episodes are reported ===')
shows = app._discover_missing_episodes()
by_title = {s['title']: s for s in shows}
check('the airing show is listed', 'Airing Show' in by_title, list(by_title))
check('the multi-episode file counts as owning both, so no gap',
      'Ended Show' not in by_title, by_title.get('Ended Show'))

airing_show = by_title.get('Airing Show', {})
check('only S02E02 is missing', airing_show.get('missing_count') == 1, airing_show.get('missing_count'))
seasons = {s['season_number']: s for s in airing_show.get('seasons', [])}
check('season 1 is complete so absent', 1 not in seasons, list(seasons))
check('season 2 reported', 2 in seasons)
missing_numbers = [e['episode_number'] for e in seasons.get(2, {}).get('missing', [])]
check('unaired E04 excluded', 4 not in missing_numbers, missing_numbers)
check('date-less E03 excluded', 3 not in missing_numbers, missing_numbers)
check('aired E02 included', missing_numbers == [2], missing_numbers)
check('specials are never reported', 0 not in seasons)
check('airing status surfaced', airing_show.get('in_production') is True)
check('next air date surfaced', airing_show.get('next_air_date') == NEXT_WEEK)
check('owned count is per season', seasons.get(2, {}).get('owned_count') == 1)

print('\n=== 2. Results are cached in the database ===')
before = dict(requests)
app._discover_missing_episodes()
check('no further status requests', requests['status'] == before['status'],
      f"{before['status']} -> {requests['status']}")
check('no further episode requests', requests['episodes'] == before['episodes'],
      f"{before['episodes']} -> {requests['episodes']}")
check('cache survives a new Storage instance',
      app.store.get_cached_season(100, 2, 24) is not None)

print('\n=== 3. Ignoring a season, then a whole show ===')
app.store.ignore_tv_season(100, 2, 'Airing Show')
check('ignored season removes the show entirely (it was its only gap)',
      'Airing Show' not in {s['title'] for s in app._discover_missing_episodes()})
app.store.unignore_tv(100, 2)
check('unignoring restores it',
      'Airing Show' in {s['title'] for s in app._discover_missing_episodes()})
app.store.ignore_tv_season(100, Storage.IGNORE_WHOLE_SHOW, 'Airing Show')
check('whole-show ignore hides it',
      'Airing Show' not in {s['title'] for s in app._discover_missing_episodes()})
app.store.unignore_tv(100)
check('clearing all ignores restores it',
      'Airing Show' in {s['title'] for s in app._discover_missing_episodes()})

print('\n=== 4. A show with no identifiable local episodes is skipped ===')
mystery = os.path.join(lib, 'Mystery Show')
make(os.path.join(mystery, 'EP01 - No Marker.mkv'))
add('tt300', 'Mystery Show', 200, mystery)
titles = {s['title'] for s in app._discover_missing_episodes()}
check('skipped rather than reported as entirely missing', 'Mystery Show' not in titles, titles)

print('\n=== 5. Airing shows sort above ended ones ===')
# Give the ended show a real gap by replacing its double file with only E01.
import shutil

shutil.rmtree(os.path.join(ended, 'Season 01'))
make(os.path.join(ended, 'Season 01', 'Ended Show - S01E01.mkv'))
ordered = [s['title'] for s in app._discover_missing_episodes()]
check('both shows now listed', {'Airing Show', 'Ended Show'} <= set(ordered), ordered)
check('the airing show comes first', ordered.index('Airing Show') < ordered.index('Ended Show'), ordered)

print('\n=== 6. Movies are never considered ===')
app.store.add_media_item(imdb_id='tt900', tmdb_id=100, title='A Movie', year=2001,
                         media_type='movie', collection_id=None, collection_name=None,
                         current_quality='1080p', path=os.path.join(tmp, 'Movies'))
check('movie absent from the results',
      'A Movie' not in {s['title'] for s in app._discover_missing_episodes()})

print(f'\n{"=" * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
if FAIL:
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
