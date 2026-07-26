"""
Canonical naming preview.
Usage: python scripts/_plan_canonical_names.py [--apply]

Compares every library entry's folder and video filename against the canonical
form derived from its stored TMDB title and year:

    <Title> (<Year>)/<Title> (<Year>).<ext>

Reports what would change. Does NOT modify anything unless --apply is passed,
and even then it only renames — nothing is ever deleted.
"""
import os
import sys

# Titles carry characters the legacy console codepage cannot render.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from naming import canonical_stem
from storage import Storage

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}
SUB_EXTS = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx', '.sup'}

APPLY = '--apply' in sys.argv

store = Storage(os.path.join(BASE_DIR, 'library.db'))

folder_renames = []
file_renames = []
skipped = []

for item in store.list_media_items():
    path = (item['path'] or '').strip()
    if not path or not os.path.isdir(path):
        continue

    stem = canonical_stem(item['title'] or '', item['year'], item['media_type'] or 'movie')
    if not stem:
        skipped.append((item['id'], item['title'], 'no canonical name'))
        continue

    current_folder = os.path.basename(os.path.normpath(path))
    parent = os.path.dirname(os.path.normpath(path))

    if current_folder != stem:
        target = os.path.join(parent, stem)
        conflict = os.path.exists(target) and os.path.normcase(target) != os.path.normcase(path)
        folder_renames.append({
            'id': item['id'], 'from': path, 'to': target, 'conflict': conflict,
        })

    # Video/subtitle files inside should share the folder's canonical stem.
    try:
        children = sorted(os.listdir(path))
    except OSError:
        continue
    for name in children:
        child = os.path.join(path, name)
        if not os.path.isfile(child):
            continue
        child_stem, ext = os.path.splitext(name)
        if ext.lower() not in VIDEO_EXTS | SUB_EXTS:
            continue
        # Subtitles may carry a language suffix, e.g. "Movie.en.srt".
        if ext.lower() in SUB_EXTS and child_stem.lower().startswith(stem.lower()):
            continue
        if child_stem != stem:
            file_renames.append({
                'id': item['id'], 'folder': current_folder,
                'from': name, 'to': f'{stem}{ext}',
                'path': child, 'target': os.path.join(path, f'{stem}{ext}'),
            })

print('=' * 74)
print(f'CANONICAL NAMING PREVIEW{"  (APPLYING)" if APPLY else "  (dry run)"}')
print('=' * 74)

print(f'\nFolders to rename ({len(folder_renames)})')
for r in folder_renames:
    flag = '  [CONFLICT - target exists, skipped]' if r['conflict'] else ''
    print(f'  #{r["id"]}')
    print(f'      from: {r["from"]}')
    print(f'        to: {r["to"]}{flag}')

print(f'\nFiles to rename ({len(file_renames)})')
for r in file_renames:
    print(f'  #{r["id"]}  [{r["folder"]}]')
    print(f'      from: {r["from"]}')
    print(f'        to: {r["to"]}')

if skipped:
    print(f'\nSkipped ({len(skipped)})')
    for sid, title, why in skipped:
        print(f'  #{sid}  {title}  - {why}')

if APPLY:
    print('\nApplying renames...')
    # Files first: renaming the folder would invalidate the recorded paths.
    for r in file_renames:
        if os.path.exists(r['target']):
            print(f'  SKIP (exists): {r["target"]}')
            continue
        try:
            os.rename(r['path'], r['target'])
            print(f'  renamed file: {r["from"]} -> {r["to"]}')
        except OSError as exc:
            print(f'  FAILED file {r["from"]}: {exc}')

    for r in folder_renames:
        if r['conflict']:
            print(f'  SKIP (conflict): {r["to"]}')
            continue
        try:
            os.rename(r['from'], r['to'])
            store.update_path(r['id'], r['to'])
            print(f'  renamed folder: {os.path.basename(r["from"])} -> {os.path.basename(r["to"])}')
        except OSError as exc:
            print(f'  FAILED folder {r["from"]}: {exc}')
else:
    print('\nDry run - nothing changed. Re-run with --apply to perform these renames.')

print('=' * 74)
print(f'  Folders to rename : {len(folder_renames)}')
print(f'  Files to rename   : {len(file_renames)}')
print('=' * 74)
