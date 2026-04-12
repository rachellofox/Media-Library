"""
Movie folder cleanup — steps 1, 2, 3.
  1. Rename misnamed video files to match their parent folder name.
  2. Promote nested videos out of sub-subfolders; delete the now-empty sub-subfolder.
  3. Delete junk files (.txt, .ico, poster .jpg/.jpeg).

Featurettes subdirectories are NOT touched.
Subtitle files and their subdirectories (Subs, Subtitles) are NOT touched.

Run with just the path for a dry run (no files changed).
Run with  --execute  to apply changes.
Usage: python scripts/_cleanup_movies.py path\\to\\movies [--execute]
"""
import os, sys, shutil

_path_args = [a for a in sys.argv[1:] if a != '--execute']
if not _path_args:
    print('Usage: python scripts/_cleanup_movies.py path\\to\\movies [--execute]')
    sys.exit(1)
MOVIES_ROOT = _path_args[0]
VIDEO_EXTS  = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}
SUB_EXTS    = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx', '.sup'}
JUNK_EXTS   = {'.txt', '.ico', '.jpg', '.jpeg', '.png', '.nfo', '.db'}
SKIP_DIRS   = {'featurettes', 'subs', 'subtitles', 'extras', 'bonus'}

DRY_RUN = '--execute' not in sys.argv

def log(action, msg):
    tag = f'[{action:<8}]'
    prefix = '  DRY RUN' if DRY_RUN else '  DONE   '
    print(f'{prefix} {tag} {msg}')

renames = promotions = deletions = 0

for entry in sorted(os.scandir(MOVIES_ROOT), key=lambda e: e.name.lower()):
    if not entry.is_dir():
        continue

    folder_name  = entry.name
    folder_path  = entry.path
    expected_stem = folder_name

    videos_in_root = []
    subdirs        = []
    junk           = []

    for child in os.scandir(folder_path):
        _, ext = os.path.splitext(child.name)
        ext_l = ext.lower()

        if child.is_dir():
            if child.name.lower() not in SKIP_DIRS:
                subdirs.append(child)
        elif ext_l in VIDEO_EXTS:
            videos_in_root.append(child)
        elif ext_l in SUB_EXTS:
            pass  # keep subtitles at root
        elif ext_l in JUNK_EXTS:
            junk.append(child)

    # ── 1. Rename misnamed video files ────────────────────────────────────
    for vid in videos_in_root:
        stem, ext = os.path.splitext(vid.name)
        if stem != expected_stem:
            new_name = expected_stem + ext
            new_path = os.path.join(folder_path, new_name)
            log('RENAME', f'{folder_name}  |  {vid.name!r}  →  {new_name!r}')
            if not DRY_RUN:
                os.rename(vid.path, new_path)
            renames += 1

    # ── 2. Promote nested videos from sub-subfolders ──────────────────────
    for subdir in subdirs:
        sub_videos = [
            c for c in os.scandir(subdir.path)
            if c.is_file() and os.path.splitext(c.name)[1].lower() in VIDEO_EXTS
        ]

        if not sub_videos:
            continue  # nothing to promote; leave subdir alone

        for vid in sub_videos:
            _, ext = os.path.splitext(vid.name)
            new_name = expected_stem + ext
            dest     = os.path.join(folder_path, new_name)
            log('PROMOTE', f'{vid.path!r}  →  {dest!r}')
            if not DRY_RUN:
                shutil.move(vid.path, dest)
            promotions += 1

        log('RMDIR', f'{subdir.path!r}')
        if not DRY_RUN:
            # Remove any remaining non-video leftovers, then the dir
            for leftover in os.scandir(subdir.path):
                if leftover.is_file():
                    os.remove(leftover.path)
            try:
                os.rmdir(subdir.path)
            except OSError:
                shutil.rmtree(subdir.path)

    # ── 3. Delete junk files ──────────────────────────────────────────────
    for f in junk:
        log('DELETE', f'{f.path!r}')
        if not DRY_RUN:
            os.remove(f.path)
        deletions += 1

print()
print('=' * 70)
mode = 'DRY RUN — no files were changed' if DRY_RUN else 'EXECUTED — changes applied'
print(f'  {mode}')
print(f'  Renames    : {renames}')
print(f'  Promotions : {promotions}')
print(f'  Deletions  : {deletions}')
print('=' * 70)
if DRY_RUN:
    print('  Re-run with --execute to apply.')
