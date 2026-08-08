"""B-0808.01: a completed torrent must not read as still downloading.

qBittorrent 5.0 renamed the pausedDL/pausedUP states to stoppedDL/stoppedUP
(the Stop/Start terminology change). QBT_DONE_STATES only knew the old
pausedUP name, so a torrent that finished and stopped seeding under qB 5
never satisfied _torrent_is_complete() -- the hero card stayed stuck showing
an active download at 100% forever, even with the files already sitting on
disk. Found live: "Hacks" season 5 finished downloading but its hero card
never stopped showing a download in progress.

Covers both the unit-level check and the full auto-finalize sweep, since the
five existing test_tv_pack_finalize.py scenarios all call
_finalize_completed_download directly and so never exercised the
_torrent_is_complete gate this bug lives in.
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
import medialibrary.qbt as qbt
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


print('\n=== 1. _torrent_is_complete recognises both the old and new qB done-state names ===')
for state in ('pausedUP', 'stoppedUP', 'PAUSEDUP', 'STOPPEDUP', 'seeding', 'stalledUP'):
    torrent = {'state': state, 'progress': 1.0, 'amount_left': 0}
    check(f'{state} -> complete', qbt._torrent_is_complete(torrent), torrent)

print('\n=== 2. Still refuses a state that genuinely is not done ===')
for state in ('downloading', 'stalledDL', 'stoppedDL', 'metaDL', ''):
    torrent = {'state': state, 'progress': 1.0, 'amount_left': 0}
    check(f'{state} -> not complete', not qbt._torrent_is_complete(torrent), torrent)

print('\n=== 3. stoppedUP alone is not enough without the byte counters agreeing ===')
torrent = {'state': 'stoppedUP', 'progress': 0.5, 'amount_left': 100}
check('progress < 1.0 still refused', not qbt._torrent_is_complete(torrent))


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

show_path = os.path.join(lib, 'Hacks')
os.makedirs(show_path, exist_ok=True)
app.store.add_media_item(
    imdb_id='tt13623126',
    tmdb_id=None,
    title='Hacks',
    year=2021,
    media_type='tv',
    collection_id=None,
    collection_name=None,
    current_quality=None,
    path=show_path,
)
media_id = app.store.get_media_item_by_imdb_id('tt13623126')['id']
info_hash = 'D' * 40
app.store.set_download_state(
    media_id, 'downloading', 'qb_webui', 'x', torrent_hash=info_hash, mode='fill'
)

pack = os.path.join(staging, 'Hacks.S05.1080p')
for n in (1, 2, 3):
    episode_path = os.path.join(pack, f'Hacks.S05E0{n}.mkv')
    os.makedirs(os.path.dirname(episode_path), exist_ok=True)
    with open(episode_path, 'wb') as fh:
        fh.write(b'\x00' * (1024 * 1024))

completed_torrent = {
    'hash': info_hash.lower(),
    'state': 'stoppedUP',
    'progress': 1.0,
    'amount_left': 0,
    'content_path': pack,
}
qbt._qbt_webui_enabled = lambda: True
qbt._qbt_webui_torrent_info = lambda h: completed_torrent if h.upper() == info_hash else None
qbt._qbt_webui_torrents_info = lambda: [completed_torrent]

print('\n=== 4. The full auto-finalize sweep clears a stoppedUP season pack ===')
app._auto_finalize_qb_completed_downloads()

row = next(r for r in app.store.list_media_items() if r['id'] == media_id)
for n in (1, 2, 3):
    want = os.path.join(show_path, 'Season 05', f'Hacks - S05E0{n}.mkv')
    check(f'S05E0{n} filed', os.path.isfile(want), want)
check(
    'download state cleared -- the hero card stops showing an active download',
    row['download_status'] is None,
    row['download_status'],
)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
