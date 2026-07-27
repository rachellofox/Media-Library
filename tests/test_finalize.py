"""Sandbox test for the upgrade-replace finalisation flow.

Uses a throwaway library root, staging dir and sqlite DB. Never touches the
real D:\\Movies or library.db.
"""

import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import app
import medialibrary.downloads
from storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + detail) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def make_video(path, size_mb=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'\x00' * (size_mb * 1024 * 1024))


def setup(tmp):
    lib = os.path.join(tmp, 'Movies')
    staging = os.path.join(tmp, 'Downloads', 'movies')
    trash = os.path.join(tmp, 'Trash')
    for d in (lib, staging, trash):
        os.makedirs(d, exist_ok=True)

    db = os.path.join(tmp, 'test.db')
    app.store = Storage(db)
    app.store.initialize()
    app.store.set_setting('movies_path', lib)
    app.store.set_setting('downloads_path', os.path.join(tmp, 'Downloads'))
    app.store.set_setting('preferred_quality', '2160p')

    # Redirect the recycle bin so tests do not fill the real one.
    recycled = []

    def fake_trash(p):
        recycled.append(p)
        dest = os.path.join(trash, os.path.basename(p))
        shutil.move(p, dest)

    # _retire_path lives in medialibrary.downloads, so that is the reference
    # the recycle call actually resolves - patching app.send2trash would
    # leave the real one in place and recycle files for real.
    medialibrary.downloads.send2trash = fake_trash
    return lib, staging, recycled


def add_item(imdb, title, year, path, quality):
    app.store.add_media_item(
        imdb_id=imdb,
        tmdb_id=None,
        title=title,
        year=year,
        media_type='movie',
        collection_id=None,
        collection_name=None,
        current_quality=quality,
        path=path,
    )
    return app.store.get_media_item_by_imdb_id(imdb)['id']


def row_for(media_id):
    return next(r for r in app.store.list_media_items() if r['id'] == media_id)


# test 1
print('\n=== 1. Upgrade replaces existing file and renames canonically ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging, recycled = setup(tmp)

    old_folder = os.path.join(lib, 'Ip Man 3 (2018)')
    old_file = os.path.join(old_folder, 'Ip Man 3 1080p.mkv')
    make_video(old_file)

    # Release folder is misnamed with the WRONG year, as the real one was.
    dl_folder = os.path.join(staging, 'Ip Man 3 (2015) (2160p BluRay x265 Tigole)')
    dl_file = os.path.join(dl_folder, 'Ip.Man.3.2015.2160p.BluRay.x265.mkv')
    make_video(dl_file, 2)

    mid = add_item('tt3提', 'Ip Man 3', 2018, old_folder, '1080p')
    app.store.set_download_state(
        mid,
        'downloading',
        'qb_webui',
        'x',
        torrent_hash='A' * 40,
        mode='upgrade',
        previous_path=old_folder,
    )

    torrent = {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': dl_folder}
    check('torrent reported complete', app._torrent_is_complete(torrent))

    app._finalize_completed_download(row_for(mid), torrent)

    want_folder = os.path.join(lib, 'Ip Man 3 (2018)')
    want_file = os.path.join(want_folder, 'Ip Man 3 (2018).mkv')
    check('canonical file exists', os.path.isfile(want_file), want_file)
    check(
        'canonical name uses TMDB year 2018 not release year 2015',
        '(2018)' in want_file and '2015' not in want_file,
    )
    check('old 1080p file recycled', not os.path.exists(old_file))
    check('recycle was used (not hard delete)', len(recycled) == 1, str(recycled))
    check(
        'DB path points at canonical folder',
        app.store.get_media_item(mid)['path'] == want_folder,
        app.store.get_media_item(mid)['path'],
    )
    check('download state cleared', row_for(mid)['download_status'] is None)
    check('seeding copy still present in staging', os.path.isfile(dl_file))
    check(
        'library file is a hardlink to the seeded file',
        os.path.exists(want_file) and os.stat(want_file).st_nlink >= 2,
    )
    check(
        'quality updated to 2160p',
        app.store.get_media_item(mid)['current_quality'] == '2160p',
        str(app.store.get_media_item(mid)['current_quality']),
    )

# test 2
print('\n=== 2. Downgrade is refused, original is preserved ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging, recycled = setup(tmp)

    old_folder = os.path.join(lib, 'Underworld (2003)')
    old_file = os.path.join(old_folder, 'Underworld 2160p.mkv')
    make_video(old_file, 3)

    dl_folder = os.path.join(staging, 'Underworld.2003.720p')
    dl_file = os.path.join(dl_folder, 'Underworld.2003.720p.mkv')
    make_video(dl_file)

    mid = add_item('tt0320691', 'Underworld', 2003, old_folder, '2160p')
    app.store.set_download_state(
        mid,
        'downloading',
        'qb_webui',
        'x',
        torrent_hash='B' * 40,
        mode='upgrade',
        previous_path=old_folder,
    )

    app._finalize_completed_download(
        row_for(mid),
        {'state': 'seeding', 'progress': 1.0, 'amount_left': 0, 'content_path': dl_folder},
    )

    check('original 2160p file untouched', os.path.isfile(old_file))
    check('nothing recycled', len(recycled) == 0, str(recycled))
    check('DB path unchanged', app.store.get_media_item(mid)['path'] == old_folder)
    check(
        'flagged needs_review',
        row_for(mid)['download_status'] == 'needs_review',
        str(row_for(mid)['download_status']),
    )

# test 3
print('\n=== 3. Incomplete download is not finalised ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging, recycled = setup(tmp)
    partial = {'state': 'downloading', 'progress': 0.4, 'amount_left': 900, 'content_path': staging}
    check('downloading state rejected', not app._torrent_is_complete(partial))
    check(
        'seeding but bytes left rejected',
        not app._torrent_is_complete(
            {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 4096, 'content_path': staging}
        ),
    )
    check(
        'seeding but progress<1 rejected',
        not app._torrent_is_complete(
            {'state': 'seeding', 'progress': 0.99, 'amount_left': 0, 'content_path': staging}
        ),
    )
    check(
        'checkingUP with full bytes accepted',
        app._torrent_is_complete(
            {'state': 'checkingUP', 'progress': 1.0, 'amount_left': 0, 'content_path': staging}
        ),
    )

# test 4
print('\n=== 4. Fresh fill (no existing file) still works ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging, recycled = setup(tmp)

    dl_folder = os.path.join(staging, 'The.Terminator.1984.2160p')
    dl_file = os.path.join(dl_folder, 'The.Terminator.1984.2160p.mkv')
    make_video(dl_file, 2)

    mid = add_item('tt0088247', 'The Terminator', 1984, None, None)
    app.store.set_download_state(
        mid, 'downloading', 'qb_webui', 'x', torrent_hash='C' * 40, mode='fill'
    )

    app._finalize_completed_download(
        row_for(mid),
        {'state': 'uploading', 'progress': 1.0, 'amount_left': 0, 'content_path': dl_folder},
    )

    want = os.path.join(lib, 'The Terminator (1984)', 'The Terminator (1984).mkv')
    check('fill placed canonically', os.path.isfile(want), want)
    check('nothing recycled on a fill', len(recycled) == 0)
    check('download state cleared', row_for(mid)['download_status'] is None)

# test 5
print('\n=== 5. Staging folder is never scanned into the library ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging, recycled = setup(tmp)
    # staging placed INSIDE the library root, the risky configuration
    inside = os.path.join(lib, 'Downloads')
    os.makedirs(inside, exist_ok=True)
    app.store.set_setting('downloads_path', inside)
    make_video(os.path.join(inside, 'Half.Finished.Movie.2160p.mkv'))
    make_video(os.path.join(lib, 'Real Movie (2001)', 'Real Movie (2001).mkv'))

    names = [e['name'] for e in app.scan_media_entries(lib, 'movie')]
    check('real movie folder scanned', 'Real Movie (2001)' in names, str(names))
    check('staging folder skipped', 'Downloads' not in names, str(names))

# test 6
print('\n=== 6. Upgrading an already-canonical file (destination occupied) ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging, recycled = setup(tmp)

    # Old file is ALREADY canonically named - the destination we want to use.
    old_folder = os.path.join(lib, 'Blade Trinity (2004)')
    old_file = os.path.join(old_folder, 'Blade Trinity (2004).mkv')
    make_video(old_file, 1)
    old_size = os.path.getsize(old_file)

    dl_folder = os.path.join(staging, 'Blade.Trinity.2004.2160p.UHD')
    dl_file = os.path.join(dl_folder, 'Blade.Trinity.2004.2160p.UHD.mkv')
    make_video(dl_file, 4)
    new_size = os.path.getsize(dl_file)

    mid = add_item('tt0359013', 'Blade: Trinity', 2004, old_folder, '1080p')
    app.store.set_download_state(
        mid,
        'downloading',
        'qb_webui',
        'x',
        torrent_hash='D' * 40,
        mode='upgrade',
        previous_path=old_folder,
    )

    app._finalize_completed_download(
        row_for(mid),
        {'state': 'seeding', 'progress': 1.0, 'amount_left': 0, 'content_path': dl_folder},
    )

    check('canonical file still exists', os.path.isfile(old_file))
    check(
        'canonical file is now the 4K one',
        os.path.getsize(old_file) == new_size,
        f'{os.path.getsize(old_file)} vs expected {new_size}',
    )
    check('old 1080p was recycled', len(recycled) == 1, str(recycled))
    check(
        'no .incoming leftover',
        not any(f.endswith('.incoming') for f in os.listdir(old_folder)),
        str(os.listdir(old_folder)),
    )
    check(
        'exactly one video in folder', len(os.listdir(old_folder)) == 1, str(os.listdir(old_folder))
    )
    check(
        'quality now 2160p',
        app.store.get_media_item(mid)['current_quality'] == '2160p',
        str(app.store.get_media_item(mid)['current_quality']),
    )

# test 7
print('\n=== 7. Failure to recycle leaves the original intact ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging, recycled = setup(tmp)
    medialibrary.downloads.send2trash = None  # simulate send2trash unavailable

    old_folder = os.path.join(lib, 'Hanna (2011)')
    old_file = os.path.join(old_folder, 'Hanna (2011).mkv')
    make_video(old_file, 1)
    old_size = os.path.getsize(old_file)

    dl_folder = os.path.join(staging, 'Hanna.2011.2160p')
    dl_file = os.path.join(dl_folder, 'Hanna.2011.2160p.mkv')
    make_video(dl_file, 4)

    mid = add_item('tt0993842', 'Hanna', 2011, old_folder, '1080p')
    app.store.set_download_state(
        mid,
        'downloading',
        'qb_webui',
        'x',
        torrent_hash='E' * 40,
        mode='upgrade',
        previous_path=old_folder,
    )

    app._finalize_completed_download(
        row_for(mid),
        {'state': 'seeding', 'progress': 1.0, 'amount_left': 0, 'content_path': dl_folder},
    )

    check(
        'original file preserved when recycling impossible',
        os.path.isfile(old_file) and os.path.getsize(old_file) == old_size,
    )
    check(
        'no .incoming leftover',
        not any(f.endswith('.incoming') for f in os.listdir(old_folder)),
        str(os.listdir(old_folder)),
    )
    check('seeded copy untouched', os.path.isfile(dl_file))

print(f'\n{"=" * 60}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
if FAIL:
    print('Failures:')
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
