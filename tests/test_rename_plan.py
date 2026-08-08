"""F-0808.01: the Tools section's canonical rename planner.

This renames the user's actual media files, so the properties that matter most
are the refusals: never overwrite, never touch a title mid-download, never
rename two files onto one name, and never rename a TV episode onto its show's
name (episode naming needs the TMDB episode list and is a separate tool).

Sandbox only — a throwaway library root and database, never the real one.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary.auth import _auth_username
from medialibrary.rename_plan import apply_renames, entry_key, plan_renames
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def setup():
    tmp = tempfile.mkdtemp()
    movies = os.path.join(tmp, 'Movies')
    tv = os.path.join(tmp, 'TV Shows')
    for d in (movies, tv):
        os.makedirs(d, exist_ok=True)
    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    return movies, tv


def make_file(path, size=1024):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'\x00' * size)
    return path


def add(title, year, media_type, path, imdb_id):
    app.store.add_media_item(
        imdb_id=imdb_id,
        tmdb_id=None,
        title=title,
        year=year,
        media_type=media_type,
        collection_id=None,
        collection_name=None,
        current_quality=None,
        path=path,
    )
    return app.store.get_media_item_by_imdb_id(imdb_id)['id']


print('\n=== 1. A misnamed movie folder and its files are both planned ===')
movies, tv = setup()
folder = os.path.join(movies, 'Batman and Robin (1997)')
make_file(os.path.join(folder, 'Batman and Robin (1997).mkv'))
make_file(os.path.join(folder, 'batman.and.robin.1997.1080p.srt'))
add('Batman & Robin', 1997, 'movie', folder, 'tt0118688')

plan = plan_renames(app.store)
check('one folder rename planned', len(plan['folders']) == 1, plan['folders'])
check(
    'folder renamed to the canonical stem',
    plan['folders'][0]['to_name'] == 'Batman & Robin (1997)',
    plan['folders'][0],
)
check('both files planned', len(plan['files']) == 2, plan['files'])
check(
    'video and subtitle both take the canonical stem',
    {entry['to_name'] for entry in plan['files']}
    == {'Batman & Robin (1997).mkv', 'Batman & Robin (1997).srt'},
    plan['files'],
)
check('nothing blocked', plan['blocked'] == 0, plan)

print('\n=== 2. Applying renames moves the files and updates the recorded path ===')
result = apply_renames(app.store, plan)
check('three renames applied', result['renamed'] == 3, result)
check('none failed', result['failed'] == [], result)
new_folder = os.path.join(movies, 'Batman & Robin (1997)')
check('folder now canonical on disk', os.path.isdir(new_folder))
check('video renamed', os.path.isfile(os.path.join(new_folder, 'Batman & Robin (1997).mkv')))
check('subtitle renamed', os.path.isfile(os.path.join(new_folder, 'Batman & Robin (1997).srt')))
row = next(dict(r) for r in app.store.list_media_items())
check('database points at the new folder', row['path'] == new_folder, row['path'])
check('re-planning now finds nothing to do', plan_renames(app.store)['total'] == 0)

print('\n=== 3. A name already correct is never planned ===')
movies, tv = setup()
folder = os.path.join(movies, 'Alien (1979)')
make_file(os.path.join(folder, 'Alien (1979).mkv'))
make_file(os.path.join(folder, 'Alien (1979).en.srt'))  # language suffix is already correct
add('Alien', 1979, 'movie', folder, 'tt0078748')
plan = plan_renames(app.store)
check('nothing planned at all', plan['total'] == 0, plan)

print('\n=== 4. A title mid-download is left out entirely ===')
movies, tv = setup()
folder = os.path.join(movies, 'Wrong Name (2001)')
make_file(os.path.join(folder, 'Wrong Name (2001).mkv'))
media_id = add('Right Name', 2001, 'movie', folder, 'tt1111111')
app.store.set_download_state(media_id, 'downloading', 'qb_webui', 'x', torrent_hash='A' * 40)
plan = plan_renames(app.store)
check('nothing planned for it', plan['total'] == 0, plan)
check(
    'and it is reported as skipped, with the reason',
    len(plan['skipped']) == 1 and plan['skipped'][0]['reason'] == 'download in progress',
    plan['skipped'],
)

print('\n=== 5. A rename onto a name that already exists is refused, not applied ===')
movies, tv = setup()
folder = os.path.join(movies, 'Chloe (2009)')
make_file(os.path.join(folder, 'Chloe (2009).mkv'))
add('Chloe', 2010, 'movie', folder, 'tt1002563')  # stored year disagrees with the folder
blocker = os.path.join(movies, 'Chloe (2010)')
os.makedirs(blocker, exist_ok=True)  # something is already called that

plan = plan_renames(app.store)
folder_entry = plan['folders'][0]
check('the folder rename is marked as a conflict', folder_entry['conflict'] is True, folder_entry)
check('and counted as blocked', plan['blocked'] >= 1, plan)
result = apply_renames(app.store, plan)
check('the conflicting rename was skipped', result['skipped_conflict'] >= 1, result)
check('the original folder is untouched', os.path.isdir(folder))
check('the blocker is untouched', os.path.isdir(blocker))
# The file inside is a separate entry with a free destination, so it still
# renames — the two are ticked independently in the UI and neither depends on
# the other. What matters is that the video is still there under one name.
check(
    'the video still exists, renamed in place rather than lost',
    os.path.isfile(os.path.join(folder, 'Chloe (2010).mkv')),
    os.listdir(folder),
)
check('nothing was left in the blocker folder', os.listdir(blocker) == [], os.listdir(blocker))

print('\n=== 6. Two entries claiming one destination are both refused ===')
movies, tv = setup()
first = os.path.join(movies, 'Dupe A (2000)')
second = os.path.join(movies, 'Dupe B (2000)')
make_file(os.path.join(first, 'a.mkv'))
make_file(os.path.join(second, 'b.mkv'))
add('Same Title', 2000, 'movie', first, 'tt2000001')
add('Same Title', 2000, 'movie', second, 'tt2000002')

plan = plan_renames(app.store)
check(
    'both folder renames flagged as conflicts',
    all(entry['conflict'] for entry in plan['folders']),
    plan['folders'],
)
result = apply_renames(app.store, plan)
check('neither was applied', os.path.isdir(first) and os.path.isdir(second), result)

print('\n=== 7. A TV show folder is renamed, but its episodes are never touched ===')
movies, tv = setup()
show = os.path.join(tv, 'Wandavision')
make_file(os.path.join(show, 'Season 01', 'WandaVision - S01E01 - Filmed.mkv'))
make_file(os.path.join(show, 'stray-episode.mkv'))  # a loose file directly in the show folder
add('WandaVision', 2021, 'tv', show, 'tt9140554')

plan = plan_renames(app.store)
check('the show folder is planned', len(plan['folders']) == 1, plan['folders'])
check(
    'TV folders are not year-stamped',
    plan['folders'][0]['to_name'] == 'WandaVision',
    plan['folders'][0],
)
check(
    'no episode file is planned — that is the episode tool, not this one',
    plan['files'] == [],
    plan['files'],
)

print('\n=== 8. Only the ticked renames are applied ===')
movies, tv = setup()
keep = os.path.join(movies, 'Keep As Is (1999)')
change = os.path.join(movies, 'Change Me (1999)')
make_file(os.path.join(keep, 'Keep As Is (1999).mkv'))
make_file(os.path.join(change, 'Change Me (1999).mkv'))
add('Keep As Is', 2000, 'movie', keep, 'tt3000001')  # would become "(2000)"
add('Changed', 1999, 'movie', change, 'tt3000002')

plan = plan_renames(app.store)
check('two folder renames offered', len(plan['folders']) == 2, plan['folders'])
chosen = entry_key(change)
result = apply_renames(app.store, plan, selected_keys={chosen})
check('only one applied', result['renamed'] == 1, result)
check('the chosen one moved', os.path.isdir(os.path.join(movies, 'Changed (1999)')))
check('the unticked one is untouched', os.path.isdir(keep))

print('\n=== 9. Routes: preview lists, apply refuses an empty selection ===')
movies, tv = setup()
folder = os.path.join(movies, 'Route Test (2005)')
make_file(os.path.join(folder, 'Route Test (2005).mkv'))
add('Route Tested', 2005, 'movie', folder, 'tt4000001')

client = app.app.test_client()
with client.session_transaction() as session:
    session['auth_user'] = _auth_username()

resp = client.get('/api/tools/rename-preview')
check('preview 200', resp.status_code == 200, resp.status_code)
payload = resp.get_json()
check('preview reports the folder', len(payload['folders']) == 1, payload)
check('preview exposes a key to select it by', bool(payload['folders'][0].get('key')), payload)
check(
    'preview does not leak absolute paths',
    'from' not in payload['folders'][0] and 'to' not in payload['folders'][0],
    payload['folders'][0],
)

resp = client.post('/api/tools/rename-apply', json={'keys': []})
check('an empty selection is refused', resp.status_code == 400, resp.status_code)
check('with a reason', resp.get_json().get('error') == 'no_selection', resp.get_json())
check('and nothing was renamed', os.path.isdir(folder))

print('\n=== 10. Route: applying a selected key renames it ===')
resp = client.post('/api/tools/rename-apply', json={'keys': [payload['folders'][0]['key']]})
check('apply 200', resp.status_code == 200, resp.status_code)
check('one renamed', resp.get_json().get('renamed') == 1, resp.get_json())
check('renamed on disk', os.path.isdir(os.path.join(movies, 'Route Tested (2005)')))

print('\n=== 11. A stale key naming something already renamed is ignored, not an error ===')
resp = client.post('/api/tools/rename-apply', json={'keys': [payload['folders'][0]['key']]})
check('still 200', resp.status_code == 200, resp.status_code)
check('nothing renamed the second time', resp.get_json().get('renamed') == 0, resp.get_json())

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
