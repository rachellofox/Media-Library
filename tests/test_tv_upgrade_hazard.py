"""Does finalising a download for a TV show endanger the whole show folder?

Sandbox only: temp dirs and a throwaway DB, and the recycle bin is redirected.
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
from storage import Storage

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
    shutil.move(p, os.path.join(trash, os.path.basename(p)))


# _retire_path lives in medialibrary.downloads, so that is the reference the
# recycle call actually resolves — patching app.send2trash would leave the real
# one in place and recycle files for real.
medialibrary.downloads.send2trash = fake_trash


def make(path, mb=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(b'\x00' * (mb * 1024 * 1024))
    return path


# A show with two seasons of episodes, as a real library would have.
show = os.path.join(lib, 'Some Show')
episodes = [
    make(os.path.join(show, 'Season 01', f'Some Show - S01E{n:02} - Ep 1080p.mkv'), 2)
    for n in range(1, 11)
]
episodes += [
    make(os.path.join(show, 'Season 02', f'Some Show - S02E{n:02} - Ep 1080p.mkv'), 2)
    for n in range(1, 11)
]
print(f'show folder starts with {len(episodes)} episodes')

app.store.add_media_item(
    imdb_id='tt9999999', tmdb_id=None, title='Some Show', year=2010, media_type='tv',
    collection_id=None, collection_name=None, current_quality='1080p', path=show,
)
media_id = app.store.get_media_item_by_imdb_id('tt9999999')['id']

# Someone downloads a single 2160p episode against the show entry, which is what
# the existing Retry / Find Torrent flow on a TV hero would do.
dl_folder = os.path.join(staging, 'Some.Show.S02E05.2160p.WEB-DL')
dl_file = make(os.path.join(dl_folder, 'Some.Show.S02E05.2160p.WEB-DL.mkv'), 6)

existing_path = show
is_upgrade = bool(existing_path) and not app._is_local_media_missing(existing_path)
print(f'submit-time classification: mode={"upgrade" if is_upgrade else "fill"}')

app.store.set_download_state(
    media_item_id=media_id, status='downloading', source='qb_webui', message='x',
    torrent_hash='A' * 40, mode='upgrade' if is_upgrade else 'fill',
    previous_path=existing_path,
)

row = next(r for r in app.store.list_media_items() if r['id'] == media_id)
torrent = {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': dl_folder}

app._finalize_completed_download(row, torrent)

surviving = [
    os.path.join(r, f)
    for r, _d, fs in os.walk(show) for f in fs
] if os.path.isdir(show) else []

print()
print(f'show folder still exists : {os.path.isdir(show)}')
print(f'episodes surviving       : {len(surviving)} of {len(episodes)}')
print(f'paths recycled           : {recycled}')
print(f'DB path now              : {app.store.get_media_item(media_id)["path"]}')

lost = len(episodes) - len(surviving)
if lost > 0:
    print()
    print(f'*** DATA LOSS: {lost} episode file(s) removed from the show folder ***')
    sys.exit(1)
print()
print('No episodes were lost.')
