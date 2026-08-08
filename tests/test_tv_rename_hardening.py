"""F-0808.01: the TV renamer must never change what episode a file claims to be.

Written from a real failure. An earlier hand-run script named episodes from
TMDB's episode list and rewrote Buffy season 3 episodes 18-22 into TMDB's
aired order when the files were in production order, putting the wrong titles
on the wrong episodes; the user repaired them by hand.

The scenario in test 1 is that exact case, and it is the reason the rest of
this module exists. Every other test here defends one of the properties that
stop it happening again.

Sandbox only — a throwaway library root and database.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary.naming_presets import get_preset
from medialibrary.storage import Storage
from medialibrary.tv_rename_plan import (
    apply_tv_renames,
    ordering_is_trusted,
    plan_show_renames,
    title_from_filename,
)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def setup():
    tmp = tempfile.mkdtemp()
    tv = os.path.join(tmp, 'TV Shows')
    os.makedirs(tv, exist_ok=True)
    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    return tv


def make(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'\x00' * 1024)
    return path


def add_show(title, year, path, imdb_id):
    app.store.add_media_item(
        imdb_id=imdb_id, tmdb_id=None, title=title, year=year, media_type='tv',
        collection_id=None, collection_name=None, current_quality=None, path=path,
    )
    return dict(app.store.get_media_item_by_imdb_id(imdb_id))


HOUSE = get_preset('house')

# Buffy season 3 as the files have it (production order) versus as TMDB has it
# (aired order, with Earshot held back to 22 after its broadcast was delayed).
BUFFY_ON_DISK = {18: 'Earshot', 19: 'Choices', 20: 'The Prom',
                 21: 'Graduation Day (1)', 22: 'Graduation Day (2)'}
BUFFY_TMDB = {(3, 18): 'Choices', (3, 19): 'The Prom', (3, 20): 'Graduation Day (1)',
              (3, 21): 'Graduation Day (2)', (3, 22): 'Earshot'}


print('\n=== 1. The Buffy case: a show numbered differently from TMDB is left alone ===')
tv = setup()
show_path = os.path.join(tv, 'Buffy the Vampire Slayer')
for number, title in BUFFY_ON_DISK.items():
    make(os.path.join(show_path, 'Season 03',
                      f'Buffy the Vampire Slayer - S03E{number:02d} - {title}.mp4'))
item = add_show('Buffy the Vampire Slayer', 1997, show_path, 'tt0118276')

trusted, reason = ordering_is_trusted(
    {(3, n): os.path.join(show_path, 'Season 03',
                          f'Buffy the Vampire Slayer - S03E{n:02d} - {t}.mp4')
     for n, t in BUFFY_ON_DISK.items()},
    BUFFY_TMDB,
)
check('the disagreement is detected', trusted is False, reason)
plan = plan_show_renames(item, HOUSE, BUFFY_TMDB, [])
check('the show is not trusted', plan['trusted'] is False, plan['trust_reason'])
check('nothing is renamed', plan['episodes'] == [], plan['episodes'])

print('\n=== 2. Even forced through, no TMDB title can reach a file ===')
# Every planned entry for an untrusted show must take its title from the
# filename or have none — never from TMDB.
sources = {entry['title_source'] for entry in plan['episodes']}
check('no entry sourced a title from TMDB', 'tmdb' not in sources, sources)

print('\n=== 3. An SxxExx marker is never reassigned ===')
tv = setup()
show_path = os.path.join(tv, 'Some Show')
# Deliberately untidy names that still carry their own markers.
make(os.path.join(show_path, 'Season 01', 'Some.Show.S01E04.Real.Title.1080p.WEB.x264.mkv'))
make(os.path.join(show_path, 'Season 01', 'Some Show - S01E05 - Another (1080p BluRay x265).mkv'))
item = add_show('Some Show', 2015, show_path, 'tt2000001')
tmdb = {(1, 4): 'Completely Different', (1, 5): 'Also Different'}
plan = plan_show_renames(item, HOUSE, tmdb, [])
for entry in plan['episodes']:
    marker = f'S{entry["season"]:02d}E{entry["episode"]:02d}'
    check(f'{marker} kept its own number', marker in entry['to_name'], entry['to_name'])
check(
    'titles came from the filenames, not TMDB',
    all(entry['title_source'] == 'filename' for entry in plan['episodes']),
    [(e['to_name'], e['title_source']) for e in plan['episodes']],
)
check(
    'the real titles survived',
    any('Real Title' in e['to_name'] for e in plan['episodes'])
    and any('Another' in e['to_name'] for e in plan['episodes']),
    [e['to_name'] for e in plan['episodes']],
)

print('\n=== 4. A trusted show may have a missing title filled from TMDB ===')
tv = setup()
show_path = os.path.join(tv, 'Agreeing Show')
make(os.path.join(show_path, 'Season 01', 'Agreeing Show - S01E01 - Pilot.mkv'))
make(os.path.join(show_path, 'Season 01', 'Agreeing Show - S01E02.mkv'))  # no title
item = add_show('Agreeing Show', 2020, show_path, 'tt2000002')
tmdb = {(1, 1): 'Pilot', (1, 2): 'Second One'}
plan = plan_show_renames(item, HOUSE, tmdb, [])
check('the show is trusted', plan['trusted'] is True, plan['trust_reason'])
filled = [e for e in plan['episodes'] if e['episode'] == 2]
check('the gap was filled', len(filled) == 1 and filled[0]['title_source'] == 'tmdb', filled)
check('with TMDB\'s title', 'Second One' in filled[0]['to_name'], filled)

print('\n=== 5. An untrusted show with no title gets a number-only name, never a guess ===')
tv = setup()
show_path = os.path.join(tv, 'Disagreeing Show')
make(os.path.join(show_path, 'Season 01', 'Disagreeing Show - S01E01 - Wrong Per TMDB.mkv'))
make(os.path.join(show_path, 'Season 01', 'Disagreeing.Show.S01E02.1080p.WEB.h264-GRP.mkv'))
item = add_show('Disagreeing Show', 2020, show_path, 'tt2000003')
tmdb = {(1, 1): 'Something Else', (1, 2): 'Tempting Title'}
plan = plan_show_renames(item, HOUSE, tmdb, [])
check('not trusted', plan['trusted'] is False, plan['trust_reason'])
untitled = [e for e in plan['episodes'] if e['episode'] == 2]
check('the titleless file is renamed', len(untitled) == 1, plan['episodes'])
landed = untitled[0]['to_name']
check('to a number-only name', landed.endswith('S01E02.mkv'), landed)
check("TMDB's title was not used", 'Tempting' not in landed, landed)

print('\n=== 6. A file with no marker is reported for manual intervention, never renamed ===')
tv = setup()
show_path = os.path.join(tv, 'Marker Free')
make(os.path.join(show_path, 'Season 01', 'Marker Free - S01E01 - Fine.mkv'))
make(os.path.join(show_path, 'Season 01', 'some mystery file.mkv'))
item = add_show('Marker Free', 2020, show_path, 'tt2000004')
plan = plan_show_renames(item, HOUSE, {(1, 1): 'Fine'}, [])
check('it is listed as manual', len(plan['manual']) == 1, plan['manual'])
check(
    'and no rename targets it',
    all('mystery' not in e['from_name'] for e in plan['episodes']),
    plan['episodes'],
)

print('\n=== 7. Titles that look like filenames survive intact ===')
for filename, expected in [
    ("Breaking Bad - S01E03 - ...And the Bag's in the River.mkv", "...And the Bag's in the River"),
    ('Alias - S05E02 - ...1.mkv', '...1'),
    ('Alias - S05E10 - S.O.S.mkv', 'S.O.S'),
    ('Mr. Robot - S01E01 - eps1.0_hellofriend.mov.mkv', 'eps1.0_hellofriend.mov'),
    ('AHS - S02E01 - Welcome to Briarcliff (1080p BluRay x265 RZeroX).mkv',
     'Welcome to Briarcliff'),
    ('Show.S01E02.Some.Title.Here.720p.HDTV.x264.mkv', 'Some Title Here'),
    ('Show.S01E01.1080p.WEB.h264-EDITH.mkv', ''),
]:
    check(f'{expected!r} read back', title_from_filename(filename) == expected,
          title_from_filename(filename))

print('\n=== 8. A file already correctly named is not planned ===')
tv = setup()
show_path = os.path.join(tv, 'Tidy Show')
make(os.path.join(show_path, 'Season 02', 'Tidy Show - S02E01 - All Good.mkv'))
item = add_show('Tidy Show', 2020, show_path, 'tt2000005')
plan = plan_show_renames(item, HOUSE, {(2, 1): 'All Good'}, [])
check('nothing to do', plan['episodes'] == [], plan['episodes'])

print('\n=== 9. Applying renames moves the file and keeps its number ===')
tv = setup()
show_path = os.path.join(tv, 'Apply Show')
make(os.path.join(show_path, 'Apply.Show.S01E07.The.Title.1080p.WEB.mkv'))
item = add_show('Apply Show', 2020, show_path, 'tt2000006')
plan = plan_show_renames(item, HOUSE, {}, [])
result = apply_tv_renames({'shows': [plan]})
check('one renamed', result['renamed'] == 1, result)
landed = os.path.join(show_path, 'Season 01', 'Apply Show - S01E07 - The Title.mkv')
check('landed under the canonical name', os.path.isfile(landed), os.listdir(show_path))

print('\n=== 10. A preset changes the shape, never the episode identity ===')
tv = setup()
show_path = os.path.join(tv, 'Preset Show')
make(os.path.join(show_path, 'Season 01', 'Preset Show - S01E03 - Third.mkv'))
item = add_show('Preset Show', 2019, show_path, 'tt2000007')
kodi = plan_show_renames(item, get_preset('kodi'), {(1, 3): 'Third'}, [])
check('kodi reshapes the name', len(kodi['episodes']) == 1, kodi['episodes'])
check(
    'still S01E03, still "Third"',
    'S01E03' in kodi['episodes'][0]['to_name'] and 'Third' in kodi['episodes'][0]['to_name'],
    kodi['episodes'][0]['to_name'],
)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
