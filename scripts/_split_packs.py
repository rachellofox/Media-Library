"""
Collection pack splitter.
Usage: python scripts/_split_packs.py [--apply]

Takes library entries whose file still sits inside a shared collection-pack
folder and gives each film its own canonical folder:

    Pirates ... Collection/Pirates ... Black Pearl 2003 ... .mkv
      -> Pirates of the Caribbean The Curse of the Black Pearl (2003)/
           Pirates of the Caribbean The Curse of the Black Pearl (2003).mkv

Names come from the stored TMDB title and year, never the release filename.
Matching subtitle sidecars travel with their film. Once every film has been
moved out, the emptied pack folder is sent to the Recycle Bin.

Dry run by default - nothing moves without --apply.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from naming import canonical_paths

APPLY = '--apply' in sys.argv
SUB_EXTS = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx', '.sup'}


def sidecars_for(video_path):
    """Subtitle files sitting beside a video and sharing its stem."""
    folder = os.path.dirname(video_path)
    stem = os.path.splitext(os.path.basename(video_path))[0].lower()
    found = []
    try:
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if not os.path.isfile(full):
                continue
            child_stem, ext = os.path.splitext(name)
            if ext.lower() in SUB_EXTS and child_stem.lower().startswith(stem):
                found.append(full)
    except OSError:
        pass
    return found


plans = []
for item in app.store.list_media_items():
    path = (item['path'] or '').strip()
    if not path or not os.path.isfile(path):
        continue  # only entries pointing at a bare file can be pack members

    media_type = item['media_type'] or 'movie'
    root = app._library_root_for(media_type)
    parent = os.path.dirname(path)
    if not root or os.path.normcase(os.path.normpath(parent)) == os.path.normcase(os.path.normpath(root)):
        continue  # a loose file directly in the library root, not a pack member

    dest = canonical_paths(
        root, item['title'] or '', item['year'],
        os.path.splitext(path)[1], media_type,
    )
    if not dest:
        print(f'  ! no canonical name for #{item["id"]} {item["title"]!r} - skipped')
        continue
    dest_folder, dest_file = dest

    plans.append({
        'id': item['id'], 'title': item['title'], 'year': item['year'],
        'src': path, 'pack': parent,
        'dest_folder': dest_folder, 'dest_file': dest_file,
        'subs': sidecars_for(path),
        'conflict': os.path.exists(dest_file),
    })

print('=' * 78)
print(f'COLLECTION PACK SPLIT{"  (APPLYING)" if APPLY else "  (dry run)"}')
print('=' * 78)

if not plans:
    print('\nNo pack members found - every library entry already has its own folder.')
    sys.exit(0)

by_pack = {}
for p in plans:
    by_pack.setdefault(p['pack'], []).append(p)

for pack, members in by_pack.items():
    print(f'\n{os.path.basename(pack)}   ({len(members)} films)')
    for p in sorted(members, key=lambda m: m['year'] or 0):
        flag = '   [CONFLICT - destination exists, will skip]' if p['conflict'] else ''
        print(f'   #{p["id"]}  {p["title"]} ({p["year"]}){flag}')
        print(f'        from: {os.path.basename(p["src"])}')
        print(f'          to: {p["dest_folder"]}\\{os.path.basename(p["dest_file"])}')
        for sub in p['subs']:
            print(f'         sub: {os.path.basename(sub)}')

print(f'\n{"=" * 78}')
print(f'  Packs   : {len(by_pack)}')
print(f'  Films   : {len(plans)}')
print('=' * 78)

if not APPLY:
    print('\nDry run - nothing changed. Re-run with --apply to perform the split.')
    sys.exit(0)

print('\nApplying...')
for pack, members in by_pack.items():
    for p in members:
        if p['conflict']:
            print(f'  SKIP #{p["id"]}, destination exists: {p["dest_file"]}')
            continue

        created_folder = not os.path.isdir(p['dest_folder'])
        try:
            os.makedirs(p['dest_folder'], exist_ok=True)
            # os.rename, never shutil.move: on a locked file shutil silently
            # falls back to copy-then-delete, which leaves a full second copy
            # behind when the delete then fails. Same-volume rename is atomic
            # and instant, and simply raises if the file is in use.
            os.rename(p['src'], p['dest_file'])
            for sub in p['subs']:
                target_stem = os.path.splitext(os.path.basename(p['dest_file']))[0]
                suffix = os.path.basename(sub)[len(os.path.splitext(os.path.basename(p['src']))[0]):]
                os.rename(sub, os.path.join(p['dest_folder'], f'{target_stem}{suffix}'))
            app.store.update_path(p['id'], p['dest_folder'])
            print(f'  moved #{p["id"]} -> {p["dest_folder"]}')
        except OSError as exc:
            hint = ''
            if getattr(exc, 'winerror', None) == 32:
                hint = ' (file in use - stop the torrent or close the player holding it)'
            elif getattr(exc, 'errno', None) == 18:
                hint = ' (destination is on another drive - move it manually)'
            print(f'  FAILED #{p["id"]}: {exc}{hint}')
            # Do not leave an empty folder behind; empty folders are exactly
            # what made titles look missing in the first place.
            if created_folder and os.path.isdir(p['dest_folder']) and not os.listdir(p['dest_folder']):
                try:
                    os.rmdir(p['dest_folder'])
                except OSError:
                    pass

    # Retire the shell only once no film is left behind in it.
    if os.path.isdir(pack) and not app._feature_videos_in(pack):
        leftovers = [f for _r, _d, fs in os.walk(pack) for f in fs]
        if app._retire_path(pack):
            print(f'  recycled emptied pack folder ({len(leftovers)} leftover file(s)): {pack}')
        else:
            print(f'  could not recycle pack folder: {pack}')

print('\nDone.')
