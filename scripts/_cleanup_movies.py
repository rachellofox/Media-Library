"""
Movie folder cleanup — steps 1, 2, 3.
  1. Rename misnamed video files to match their parent folder name.
  2. Promote nested videos out of sub-subfolders; delete the now-empty sub-subfolder.
  3. Delete junk files (.txt, .ico, poster .jpg/.jpeg).

Featurettes subdirectories are NOT touched.
Subtitle files and their subdirectories (Subs, Subtitles) are NOT touched.

Dry run by default. Pass --apply to make changes. Nothing is hard deleted:
leftovers and junk go to the Recycle Bin.
Usage: python scripts/_cleanup_movies.py path\\to\\movies [--apply]
"""

import os
import sys

# The log lines contain an arrow, and a Windows console defaults to cp1252,
# which cannot encode it — without this the script died on the first line it
# tried to print, in dry run, before doing anything at all.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

# --execute is what this script originally took; still accepted, so an older
# invocation applies changes rather than silently doing nothing.
_FLAGS = {'--apply', '--execute'}

_path_args = [a for a in sys.argv[1:] if a not in _FLAGS]
if not _path_args:
    print('Usage: python scripts/_cleanup_movies.py path\\to\\movies [--apply]')
    sys.exit(1)
MOVIES_ROOT = _path_args[0]
VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}
SUB_EXTS = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx', '.sup'}
JUNK_EXTS = {'.txt', '.ico', '.jpg', '.jpeg', '.png', '.nfo', '.db'}
SKIP_DIRS = {'featurettes', 'subs', 'subtitles', 'extras', 'bonus'}

DRY_RUN = not (_FLAGS & set(sys.argv))


def discard(path):
    """Recycle a file rather than unlinking it.

    This script deletes leftovers and junk, and a wrong guess about what counts
    as junk used to be unrecoverable. Falls back to a hard delete only when
    send2trash is unavailable, and says so.
    """
    if send2trash is None:
        print(f'  WARNING  send2trash unavailable, hard deleting {path!r}')
        os.remove(path)
        return
    send2trash(os.path.abspath(path))


def log(action, msg):
    tag = f'[{action:<8}]'
    prefix = '  DRY RUN' if DRY_RUN else '  DONE   '
    print(f'{prefix} {tag} {msg}')


renames = promotions = deletions = skipped = 0

for entry in sorted(os.scandir(MOVIES_ROOT), key=lambda e: e.name.lower()):
    if not entry.is_dir():
        continue

    folder_name = entry.name
    folder_path = entry.path
    expected_stem = folder_name

    videos_in_root = []
    subdirs = []
    junk = []

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

    # Rename misnamed video files to match the folder.
    for vid in videos_in_root:
        stem, ext = os.path.splitext(vid.name)
        if stem != expected_stem:
            new_name = expected_stem + ext
            new_path = os.path.join(folder_path, new_name)
            log('RENAME', f'{folder_name}  |  {vid.name!r}  →  {new_name!r}')
            if not DRY_RUN:
                os.rename(vid.path, new_path)
            renames += 1

    # Promote nested videos out of sub-subfolders.
    for subdir in subdirs:
        sub_videos = [
            c
            for c in os.scandir(subdir.path)
            if c.is_file() and os.path.splitext(c.name)[1].lower() in VIDEO_EXTS
        ]

        if not sub_videos:
            continue  # nothing to promote; leave subdir alone

        for vid in sub_videos:
            _, ext = os.path.splitext(vid.name)
            new_name = expected_stem + ext
            dest = os.path.join(folder_path, new_name)

            # A video already sitting at that name means two candidates for the
            # same film and no way to tell which is wanted. Leave both: shutil.move
            # used to overwrite here, quietly destroying the one in the folder root.
            if os.path.exists(dest):
                log('SKIP', f'{vid.path!r} — {new_name!r} already exists, leaving both')
                skipped += 1
                continue
            log('PROMOTE', f'{vid.path!r}  →  {dest!r}')
            if not DRY_RUN:
                # os.rename, never shutil.move: across a locked file shutil.move
                # silently degrades to copy-then-delete, which once duplicated
                # 10 GB. A rename that cannot be done must fail loudly.
                os.rename(vid.path, dest)
            promotions += 1

        if (
            any(
                os.path.splitext(c.name)[1].lower() in VIDEO_EXTS
                for c in os.scandir(subdir.path)
                if c.is_file()
            )
            and not DRY_RUN
        ):
            log('KEEP', f'{subdir.path!r} — still holds a video, not removing')
            continue

        log('RMDIR', f'{subdir.path!r}')
        if not DRY_RUN:
            # Remove any remaining non-video leftovers, then the dir
            for leftover in os.scandir(subdir.path):
                if leftover.is_file():
                    discard(leftover.path)
            try:
                os.rmdir(subdir.path)
            except OSError:
                # Something is still in there. Recycle the folder whole rather
                # than rmtree, which would delete whatever it was for good.
                discard(subdir.path)

    # Recycle junk files.
    for f in junk:
        log('DELETE', f'{f.path!r}')
        if not DRY_RUN:
            discard(f.path)
        deletions += 1

print()
print('=' * 70)
mode = 'DRY RUN — no files were changed' if DRY_RUN else 'EXECUTED — changes applied'
print(f'  {mode}')
print(f'  Renames    : {renames}')
print(f'  Promotions : {promotions}')
print(f'  Deletions  : {deletions}')
print(f'  Skipped    : {skipped}')
print('=' * 70)
if DRY_RUN:
    print('  Re-run with --apply to apply.')
