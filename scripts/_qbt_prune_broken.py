"""
Broken torrent pruner.
Usage: python scripts/_qbt_prune_broken.py [--apply] [--all]

Lists torrents whose data no longer exists where qBittorrent expects it —
usually because the file was renamed, moved into its canonical folder, or
replaced by a quality upgrade.

Removes them from qBittorrent **without deleting any files** (`deleteFiles=false`),
so nothing on disk is touched either way.

Scope it with --only to avoid touching broken torrents you did not mean to
remove, e.g.

    python scripts/_qbt_prune_broken.py --only "Ip Man" --only Pirates --apply

Dry run by default - nothing is removed without --apply.
"""
import os
import sys
import urllib.parse

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402

APPLY = '--apply' in sys.argv
ONLY = [
    sys.argv[i + 1].lower()
    for i, a in enumerate(sys.argv[:-1]) if a == '--only'
]

if not app._qbt_webui_enabled():
    print('qBittorrent WebUI is not configured - nothing to do.')
    sys.exit(0)

torrents = app._qbt_webui_torrents_info() or []
if not torrents:
    print('qBittorrent reported no torrents.')
    sys.exit(0)

broken = []
skipped = []
for t in torrents:
    content = (t.get('content_path') or '').strip()
    if not content or os.path.exists(content):
        continue
    name = (t.get('name') or '').lower()
    if ONLY and not any(term in name for term in ONLY):
        skipped.append(t)
        continue
    broken.append(t)

print('=' * 78)
print(f'BROKEN TORRENTS{"  (APPLYING)" if APPLY else "  (dry run)"}')
print('=' * 78)

if not broken:
    print(f'\nAll {len(torrents)} torrents have their data intact.')
    sys.exit(0)

for t in broken:
    print(f'\n  {t.get("name", "")[:70]}')
    print(f'     state={t.get("state")}  ratio={float(t.get("ratio") or 0):.2f}')
    print(f'     expects: {t.get("content_path")}')

if skipped:
    print(f'\n  Excluded by --only ({len(skipped)}), left untouched:')
    for t in skipped:
        print(f'     {t.get("name", "")[:68]}')

print(f'\n{"=" * 78}')
print(f'  Broken and in scope: {len(broken)} of {len(torrents)} torrents')
print('  Files on disk are never touched - only the torrent entry is removed.')
print('=' * 78)

if not APPLY:
    print('\nDry run - nothing removed. Re-run with --apply to remove these.')
    sys.exit(0)

print('\nRemoving from qBittorrent (keeping all files)...')
removed = 0
for t in broken:
    info_hash = (t.get('hash') or '').strip()
    if not info_hash:
        continue
    payload = urllib.parse.urlencode({
        'hashes': info_hash.lower(),
        'deleteFiles': 'false',
    }).encode('utf-8')
    try:
        app._qbt_webui_open('/api/v2/torrents/delete', method='POST', data=payload)
        removed += 1
        print(f'  removed: {t.get("name", "")[:66]}')
    except Exception as exc:  # noqa: BLE001
        print(f'  FAILED  {t.get("name", "")[:60]}: {exc}')

print(f'\nRemoved {removed} torrent(s). No files were deleted.')
