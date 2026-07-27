"""Filing a finished TV download must never endanger the rest of the show."""

import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
import medialibrary.downloads
from medialibrary.identify import VIDEO_EXTENSIONS
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def scenario():
    """Fresh temp library, DB and redirected recycle bin per scenario."""
    tmp = tempfile.mkdtemp()
    lib = os.path.join(tmp, 'TV Shows')
    staging = os.path.join(tmp, 'Downloads')
    trash = os.path.join(tmp, 'Trash')
    for d in (lib, staging, trash):
        os.makedirs(d, exist_ok=True)
    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    app.store.set_setting('tv_path', lib)
    app.store.set_setting('movies_path', os.path.join(tmp, 'Movies'))
    recycled = []

    def fake_trash(p):
        recycled.append(p)
        shutil.move(p, os.path.join(trash, os.path.basename(p) + str(len(recycled))))

    # _retire_path lives in medialibrary.downloads, so that is the reference
    # the recycle call actually resolves - patching app.send2trash would
    # leave the real one in place and recycle files for real.
    medialibrary.downloads.send2trash = fake_trash
    return tmp, lib, staging, recycled


def make(path, mb=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'\x00' * (mb * 1024 * 1024))
    return path


def add_show(show_path, title='Some Show'):
    app.store.add_media_item(
        imdb_id='tt' + str(abs(hash(show_path)) % 10**7),
        tmdb_id=None,
        title=title,
        year=2010,
        media_type='tv',
        collection_id=None,
        collection_name=None,
        current_quality='1080p',
        path=show_path,
    )
    return next(r['id'] for r in app.store.list_media_items() if r['path'] == show_path)


def finalize(media_id, dl_folder, mode='upgrade', previous=None):
    app.store.set_download_state(
        media_item_id=media_id,
        status='downloading',
        source='qb_webui',
        message='x',
        torrent_hash='A' * 40,
        mode=mode,
        previous_path=previous,
    )
    row = next(r for r in app.store.list_media_items() if r['id'] == media_id)
    app._finalize_completed_download(
        row, {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': dl_folder}
    )
    return next(r for r in app.store.list_media_items() if r['id'] == media_id)


def videos_under(path):
    return [
        os.path.join(r, f)
        for r, _d, fs in os.walk(path)
        for f in fs
        if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS
    ]


print('\n=== 1. A single-episode download must not destroy the show ===')
tmp, lib, staging, recycled = scenario()
show = os.path.join(lib, 'Some Show')
for n in range(1, 11):
    make(os.path.join(show, 'Season 01', f'Some Show - S01E{n:02} - T 1080p.mkv'), 2)
for n in range(1, 11):
    make(os.path.join(show, 'Season 02', f'Some Show - S02E{n:02} - T 1080p.mkv'), 2)
mid = add_show(show)
dl = os.path.join(staging, 'Some.Show.S02E05.2160p')
make(os.path.join(dl, 'Some.Show.S02E05.2160p.mkv'), 6)
finalize(mid, dl, previous=show)
check('show folder survives', os.path.isdir(show))
check('episode count unchanged at 20', len(videos_under(show)) == 20, len(videos_under(show)))
check(
    'only the superseded episode file was recycled',
    len(recycled) == 1 and 'S02E05' in recycled[0],
    recycled,
)
check(
    'the folder itself was never recycled',
    not any(r.rstrip(os.sep).endswith('Some Show') for r in recycled),
)
placed = os.path.join(show, 'Season 02', 'Some Show - S02E05.mkv')
check('new episode filed under its canonical name', os.path.isfile(placed), placed)
check('DB still points at the show folder', app.store.get_media_item(mid)['path'] == show)

print('\n=== 2. A worse episode is refused, nothing is touched ===')
tmp, lib, staging, recycled = scenario()
show = os.path.join(lib, 'Show B')
good = make(os.path.join(show, 'Season 01', 'Show B - S01E01 - T 2160p.mkv'), 8)
mid = add_show(show, 'Show B')
dl = os.path.join(staging, 'Show.B.S01E01.720p')
make(os.path.join(dl, 'Show.B.S01E01.720p.mkv'), 1)
row = finalize(mid, dl, previous=show)
check('existing episode untouched', os.path.isfile(good))
check('nothing recycled', not recycled, recycled)
check(
    'flagged needs_review', (row['download_status'] or '') == 'needs_review', row['download_status']
)

print('\n=== 3. A multi-episode file is never retired for one of its episodes ===')
tmp, lib, staging, recycled = scenario()
show = os.path.join(lib, 'Show C')
two_parter = make(os.path.join(show, 'Season 04', 'Show C - S04E01-E02 - Two Parter 1080p.mkv'), 4)
mid = add_show(show, 'Show C')
dl = os.path.join(staging, 'Show.C.S04E02.2160p')
make(os.path.join(dl, 'Show.C.S04E02.2160p.mkv'), 9)
finalize(mid, dl, previous=show)
check('the two-part file survives', os.path.isfile(two_parter))
check('it was not recycled', not any('E01-E02' in r for r in recycled), recycled)
check(
    'the upgraded episode was still filed',
    os.path.isfile(os.path.join(show, 'Season 04', 'Show C - S04E02.mkv')),
)

print('\n=== 4. A download with no SxxExx is left alone ===')
tmp, lib, staging, recycled = scenario()
show = os.path.join(lib, 'Show D')
keep = make(os.path.join(show, 'Season 01', 'Show D - S01E01 - T.mkv'), 2)
mid = add_show(show, 'Show D')
dl = os.path.join(staging, 'Show.D.Some.Bonus.Feature')
orphan = make(os.path.join(dl, 'Show D Behind The Scenes.mkv'), 3)
row = finalize(mid, dl, previous=show)
check('existing episode untouched', os.path.isfile(keep))
check('nothing recycled', not recycled, recycled)
check('download left where it was', os.path.isfile(orphan))
check('download state cleared', (row['download_status'] or '') == '')

print('\n=== 5. Movies still replace their file as before ===')
tmp, lib, staging, recycled = scenario()
movies = os.path.join(tmp, 'Movies')
os.makedirs(movies, exist_ok=True)
app.store.set_setting('movies_path', movies)
folder = os.path.join(movies, 'A Film (2001)')
old = make(os.path.join(folder, 'A Film (2001).mkv'), 2)
app.store.add_media_item(
    imdb_id='tt5555555',
    tmdb_id=None,
    title='A Film',
    year=2001,
    media_type='movie',
    collection_id=None,
    collection_name=None,
    current_quality='1080p',
    path=folder,
)
mid = next(r['id'] for r in app.store.list_media_items() if r['imdb_id'] == 'tt5555555')
dl = os.path.join(staging, 'A.Film.2001.2160p')
make(os.path.join(dl, 'A.Film.2001.2160p.mkv'), 7)
finalize(mid, dl, previous=folder)
check('movie replaced in place', os.path.isfile(os.path.join(folder, 'A Film (2001).mkv')))
check('old movie file recycled', len(recycled) == 1, recycled)

print('\n=== 6. A movie folder holding several videos is not recycled wholesale ===')
tmp, lib, staging, recycled = scenario()
movies = os.path.join(tmp, 'Movies')
os.makedirs(movies, exist_ok=True)
app.store.set_setting('movies_path', movies)
pack = os.path.join(movies, 'Odd Folder')
target = make(os.path.join(pack, 'Odd Folder 1080p.mkv'), 2)
bystander = make(os.path.join(pack, 'Another Film 1080p.mkv'), 3)
app.store.add_media_item(
    imdb_id='tt6666666',
    tmdb_id=None,
    title='Odd Folder',
    year=2005,
    media_type='movie',
    collection_id=None,
    collection_name=None,
    current_quality='1080p',
    path=pack,
)
mid = next(r['id'] for r in app.store.list_media_items() if r['imdb_id'] == 'tt6666666')
dl = os.path.join(staging, 'Odd.Folder.2005.2160p')
make(os.path.join(dl, 'Odd.Folder.2005.2160p.mkv'), 9)
finalize(mid, dl, previous=pack)
check('the bystander video survives', os.path.isfile(bystander))
check(
    'the whole folder was not recycled',
    not any(r.rstrip(os.sep).endswith('Odd Folder') for r in recycled),
    recycled,
)

print(f'\n{"=" * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
if FAIL:
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
