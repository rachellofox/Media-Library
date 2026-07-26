"""
Movie folder audit.
Usage: python scripts/_audit_movies.py path\\to\\movies
For each subfolder in the given folder, reports:
  - the expected standard name (folder name + video extension)
  - video files whose names don't match the folder name
  - extra files that are neither video nor subtitle
  - folders with no video file at all
  - loose video files sitting directly in the root (not in a subfolder)

Does NOT modify anything.
"""
import os
import sys

if len(sys.argv) < 2:
    print('Usage: python scripts/_audit_movies.py path\\to\\movies')
    sys.exit(1)

MOVIES_ROOT = sys.argv[1]

VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}
SUB_EXTS   = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx', '.sup'}

misnamed_video = []
extra_files    = []
no_video       = []
loose_files    = []

for entry in sorted(os.scandir(MOVIES_ROOT), key=lambda e: e.name.lower()):
    if entry.is_file():
        _, ext = os.path.splitext(entry.name)
        if ext.lower() in VIDEO_EXTS:
            loose_files.append(entry.path)
        continue

    if not entry.is_dir():
        continue

    folder_name = entry.name
    expected_stem = folder_name

    folder_videos = []
    folder_extras = []

    for child in sorted(os.scandir(entry.path), key=lambda e: e.name.lower()):
        if not child.is_file():
            folder_extras.append({'path': child.path, 'reason': 'subdirectory'})
            continue
        _, ext = os.path.splitext(child.name)
        ext_lower = ext.lower()

        if ext_lower in VIDEO_EXTS:
            folder_videos.append(child)
        elif ext_lower in SUB_EXTS:
            pass
        else:
            folder_extras.append({'path': child.path, 'reason': f'non-video/sub file ({ext or "no ext"})'})

    if not folder_videos:
        no_video.append(entry.path)
        if folder_extras:
            extra_files.extend(folder_extras)
        continue

    for vid in folder_videos:
        stem, ext = os.path.splitext(vid.name)
        if stem != expected_stem:
            misnamed_video.append({
                'folder': folder_name,
                'current': vid.name,
                'expected': expected_stem + ext,
            })

    if folder_extras:
        extra_files.extend(folder_extras)


print(f"{'='*70}")
print(f"MOVIE FOLDER AUDIT  —  {MOVIES_ROOT}")
print(f"{'='*70}\n")

print(f"Misnamed video files ({len(misnamed_video)})")
for item in misnamed_video:
    print(f"  Folder : {item['folder']}")
    print(f"  Current: {item['current']}")
    print(f"  Rename → {item['expected']}")
    print()

print(f"Extra files to remove ({len(extra_files)})")
for item in extra_files:
    print(f"  [{item['reason']}]  {item['path']}")

print(f"\nFolders with no video file ({len(no_video)})")
for p in no_video:
    print(f"  {p}")

print(f"\nLoose video files in root — not in subfolder ({len(loose_files)})")
for p in loose_files:
    print(f"  {p}")

print(f"\n{'='*70}")
print("SUMMARY")
print(f"  Misnamed video files  : {len(misnamed_video)}")
print(f"  Extra files to remove : {len(extra_files)}")
print(f"  Folders with no video : {len(no_video)}")
print(f"  Loose root videos     : {len(loose_files)}")
print(f"{'='*70}")
