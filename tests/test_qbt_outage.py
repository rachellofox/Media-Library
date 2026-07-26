"""Download state must survive a qBittorrent outage. Throwaway DB only."""
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


tmp = tempfile.mkdtemp()
app.store = Storage(os.path.join(tmp, 't.db'))
app.store.initialize()
app.store.set_setting('movies_path', os.path.join(tmp, 'Movies'))
os.makedirs(os.path.join(tmp, 'Movies'), exist_ok=True)

app._qbt_webui_enabled = lambda: True


def add(imdb, title, year, path=None):
    app.store.add_media_item(imdb_id=imdb, tmdb_id=None, title=title, year=year,
                             media_type='movie', collection_id=None, collection_name=None,
                             current_quality=None, path=path)
    return app.store.get_media_item_by_imdb_id(imdb)['id']


def row_for(mid):
    return next(r for r in app.store.list_media_items() if r['id'] == mid)


def stale(mid):
    """Backdate the state well past the 12h staleness window."""
    with app.store.conn() as c:
        c.execute("UPDATE download_states SET updated_at = datetime('now','-2 days') "
                  "WHERE media_item_id = ?", (mid,))


print('\n=== 1. qB unreachable must NOT clear a stale download state ===')
mid = add('tt0088247', 'The Terminator', 1984)
app.store.set_download_state(mid, 'downloading', 'qb_webui', 'x', torrent_hash='A' * 40)
stale(mid)


def boom():
    raise app.QbtUnavailableError('connection refused')


app._qbt_webui_torrents_info = boom
app._auto_finalize_qb_completed_downloads()
check('state survives the outage', (row_for(mid)['download_status'] or '') == 'downloading',
      row_for(mid)['download_status'])
check('item still visible in the library view',
      bool((row_for(mid)['path'] or '').strip()) or row_for(mid)['download_status'] in
      {'starting', 'handed_off', 'downloading'})

print('\n=== 2. qB reachable and genuinely empty: stale state IS cleared ===')
app._qbt_webui_torrents_info = lambda: []
app._auto_finalize_qb_completed_downloads()
check('stale state cleared when qB really has nothing',
      (row_for(mid)['download_status'] or '') == '', row_for(mid)['download_status'])

print('\n=== 3. Orphaned item is re-adopted from a running torrent ===')
running = [{
    'hash': 'B' * 40, 'name': 'The Terminator 1984 2160p Bluray x265 KiNGDOM',
    'state': 'downloading', 'progress': 0.24, 'amount_left': 999,
    'save_path': os.path.join(tmp, 'Downloads'), 'content_path': os.path.join(tmp, 'Downloads', 'x'),
}]
app._qbt_webui_torrents_info = lambda: running
app._auto_finalize_qb_completed_downloads()
r = row_for(mid)
check('download state rebuilt', (r['download_status'] or '') == 'downloading', r['download_status'])
check('torrent hash recorded', (r['download_torrent_hash'] or '') == 'B' * 40, r['download_torrent_hash'])
check('back in the library view', r['download_status'] in {'starting', 'handed_off', 'downloading'})

print('\n=== 4. Re-adoption will not grab the wrong year ===')
mid2 = add('tt0103064', 'Terminator 2: Judgment Day', 1991)
app._auto_finalize_qb_completed_downloads()
check('1991 item not adopted by the 1984 torrent',
      (row_for(mid2)['download_status'] or '') == '', row_for(mid2)['download_status'])

print('\n=== 5. Re-adoption leaves items that already have a file alone ===')
have_folder = os.path.join(tmp, 'Movies', 'Owned (2001)')
os.makedirs(have_folder, exist_ok=True)
with open(os.path.join(have_folder, 'Owned (2001).mkv'), 'wb') as fh:
    fh.write(b'\x00' * 2048)
mid3 = add('tt1111111', 'Owned', 2001, have_folder)
app._qbt_webui_torrents_info = lambda: [{
    'hash': 'C' * 40, 'name': 'Owned 2001 1080p', 'state': 'downloading',
    'progress': 0.1, 'amount_left': 10, 'save_path': tmp, 'content_path': tmp,
}]
app._auto_finalize_qb_completed_downloads()
check('item with a local file not re-adopted',
      (row_for(mid3)['download_status'] or '') == '', row_for(mid3)['download_status'])

print('\n=== 6. qbt-test reports a failure instead of "0 torrents" ===')
app.app.config['TESTING'] = True
app._qbt_webui_url = lambda: 'http://127.0.0.1:8090'
app._qbt_webui_torrents_info = boom
r = app.app.test_client().get('/api/settings/qbt-test')
check('unreachable qB returns 502, not ok/0', r.status_code == 502, r.status_code)

print(f'\n{"=" * 58}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
if FAIL:
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
