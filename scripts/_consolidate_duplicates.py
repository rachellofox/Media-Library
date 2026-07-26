"""
Duplicate copy consolidation.
Usage: python scripts/_consolidate_duplicates.py [--apply]

Finds library folders holding a second copy of a title that is already in the
library, keeps the best-quality copy, moves it into the canonical folder, and
sends the superseded copies to the Recycle Bin.

An orphan folder is only matched to a library entry when every token of the
entry's title and year appears in the folder name, so a sequel cannot absorb
its predecessor.

Dry run by default - nothing is moved or deleted without --apply.

Note: moving a file that qBittorrent is still seeding will break that torrent.
Remove or recheck the torrent afterwards if you are still seeding these.
"""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from naming import canonical_paths
from quality import QUALITY_ORDER, detect_quality_from_file

APPLY = '--apply' in sys.argv
VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}


def tokens(text):
    return {t for t in re.split(r'[^a-z0-9]+', (text or '').lower()) if t}


def videos_in(folder):
    found = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                full = os.path.join(root, name)
                try:
                    found.append((os.path.getsize(full), full))
                except OSError:
                    pass
    return found


def rank(path):
    """Sort key: quality first, then file size."""
    quality = detect_quality_from_file(path, ffprobe_exe=app.FFPROBE_EXE)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return QUALITY_ORDER.get((quality or '').lower(), 0), size, quality


items = app.store.list_media_items()
linked = {
    os.path.normcase(os.path.normpath(i['path'])): i['id']
    for i in items if (i['path'] or '').strip()
}

plans = []
for item in items:
    db_path = (item['path'] or '').strip()
    root = app._library_root_for(item['media_type'] or 'movie')
    if not root or not os.path.isdir(root):
        continue

    want = tokens(item['title']) | ({str(item['year'])} if item['year'] else set())
    if not want:
        continue

    candidates = [db_path] if db_path and os.path.isdir(db_path) else []
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        if os.path.normcase(os.path.normpath(folder)) in linked:
            continue  # belongs to some library entry already
        if want <= tokens(name) and videos_in(folder):
            candidates.append(folder)

    if len(candidates) < 2:
        continue

    scored = []
    for folder in candidates:
        for _size, video in videos_in(folder):
            scored.append((rank(video), video, folder))
    if not scored:
        continue
    scored.sort(key=lambda s: s[0], reverse=True)
    (_qrank, _size, best_quality), best_video, best_folder = scored[0]

    dest = canonical_paths(
        root, item['title'] or '', item['year'],
        os.path.splitext(best_video)[1], item['media_type'] or 'movie',
    )
    if not dest:
        continue
    dest_folder, dest_file = dest

    losers = [f for f in candidates if os.path.normcase(f) != os.path.normcase(best_folder)]
    plans.append({
        'id': item['id'], 'title': item['title'], 'year': item['year'],
        'db_path': db_path, 'best_video': best_video, 'best_folder': best_folder,
        'best_quality': best_quality, 'dest_folder': dest_folder, 'dest_file': dest_file,
        'losers': losers, 'stale_quality': item['current_quality'],
        'discarded': [(v, rank(v)[2]) for _r, v, f in scored[1:]],
    })

print('=' * 78)
print(f'DUPLICATE CONSOLIDATION{"  (APPLYING)" if APPLY else "  (dry run)"}')
print('=' * 78)

if not plans:
    print('\nNo duplicate copies found.')

reclaimed = 0
for p in plans:
    print(f'\n#{p["id"]}  {p["title"]} ({p["year"]})')
    print(f'   DB currently  : {p["db_path"]}')
    print(f'   DB quality    : {p["stale_quality"]}  ->  {p["best_quality"]}')
    print(f'   KEEP          : {p["best_quality"]:>6}  {p["best_video"]}')
    for video, quality in p['discarded']:
        try:
            reclaimed += os.path.getsize(video)
        except OSError:
            pass
        print(f'   RECYCLE       : {quality or "?":>6}  {video}')
    print(f'   MOVE TO       : {p["dest_file"]}')

print(f'\n{"=" * 78}')
print(f'  Titles to consolidate : {len(plans)}')
print(f'  Space reclaimed       : {reclaimed / 2**30:.2f} GB')
print('=' * 78)

if not APPLY:
    print('\nDry run - nothing changed. Re-run with --apply to perform this.')
    sys.exit(0)

print('\nApplying...')
for p in plans:
    # Recycle the superseded copies first: one of those folders is often the
    # canonical name we need to move the keeper into. The keeper lives in a
    # different folder, so it is never at risk here.
    for folder in p['losers']:
        if app._retire_path(folder):
            print(f'  recycled: {folder}')
        else:
            print(f'  FAILED to recycle (aborting #{p["id"]}): {folder}')
            break
    else:
        if os.path.normcase(p['best_video']) != os.path.normcase(p['dest_file']):
            os.makedirs(p['dest_folder'], exist_ok=True)
            if os.path.exists(p['dest_file']):
                print(f'  SKIP, destination occupied: {p["dest_file"]}')
                continue
            # os.rename, never shutil.move: shutil falls back to
            # copy-then-delete on a locked file, silently leaving a full
            # duplicate behind when the delete fails.
            try:
                os.rename(p['best_video'], p['dest_file'])
            except OSError as exc:
                hint = ' (file in use)' if getattr(exc, 'winerror', None) == 32 else ''
                print(f'  FAILED to move #{p["id"]}: {exc}{hint}')
                if os.path.isdir(p['dest_folder']) and not os.listdir(p['dest_folder']):
                    try:
                        os.rmdir(p['dest_folder'])
                    except OSError:
                        pass
                continue
            print(f'  moved: -> {p["dest_file"]}')
            # Drop the now-empty folder the keeper came from.
            if (os.path.isdir(p['best_folder']) and not videos_in(p['best_folder'])
                    and os.path.normcase(p['best_folder'])
                    != os.path.normcase(p['dest_folder'])):
                    app._retire_path(p['best_folder'])
                    print(f'  recycled empty source: {p["best_folder"]}')

        app.store.update_path(p['id'], p['dest_folder'])
        if p['best_quality']:
            app.store.update_quality(p['id'], p['best_quality'])
        print(f'  DB updated: #{p["id"]} -> {p["dest_folder"]} ({p["best_quality"]})')

print('\nDone.')
