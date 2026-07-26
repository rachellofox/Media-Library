"""
Audit: compare a movies folder on disk vs library.db entries.
Usage: python scripts/_dbcheck.py path\\to\\movies
Reports:
  - folders on disk not in DB (missing from library)
  - DB movie entries whose path no longer exists on disk
  - DB movie entries with bad/missing IMDb IDs
  - DB movie entries with no poster
"""

import os
import sqlite3
import sys

if len(sys.argv) < 2:
    print('Usage: python scripts/_dbcheck.py path\\to\\movies')
    sys.exit(1)

MOVIES_ROOT = sys.argv[1]
VIDEO_EXTS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}


def all_movie_dirs(root):
    """Return normalised paths for every subfolder and loose video file in root."""
    paths = set()
    try:
        for entry in os.scandir(root):
            if entry.is_dir() and not entry.name.startswith('.'):
                paths.add(os.path.normcase(os.path.normpath(entry.path)))
            elif entry.is_file():
                _, ext = os.path.splitext(entry.name)
                if ext.lower() in VIDEO_EXTS:
                    paths.add(os.path.normcase(os.path.normpath(entry.path)))
    except PermissionError:
        pass
    return paths


disk_paths = all_movie_dirs(MOVIES_ROOT)

conn = sqlite3.connect('library.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute(
    'SELECT id, imdb_id, title, year, media_type, poster_url, path '
    "FROM media_items WHERE media_type = 'movie'"
)
db_movies = cur.fetchall()
conn.close()

db_norm = {os.path.normcase(os.path.normpath(r['path'])): r for r in db_movies if r['path']}

# ---- On disk but not in DB ----
missing_from_db = sorted(disk_paths - set(db_norm.keys()))
print(f'=== On disk but MISSING from DB ({len(missing_from_db)}) ===')
for p in missing_from_db:
    print(f'  {p}')

# ---- In DB but path gone from disk ----
stale_in_db = []
for norm_path, row in db_norm.items():
    if norm_path not in disk_paths:
        stale_in_db.append(row)
print(f'\n=== In DB but path NOT on disk ({len(stale_in_db)}) ===')
for r in stale_in_db:
    print(f'  id={r["id"]}  path={r["path"]!r}')

# ---- Bad IMDb IDs (nm*, wrong tt*, or missing) ----
bad_id = []
for r in db_movies:
    iid = (r['imdb_id'] or '').strip()
    if not iid or not iid.startswith('tt') or r['title'] == iid:
        bad_id.append(r)
print(f'\n=== Bad/missing IMDb IDs or title still equals ID ({len(bad_id)}) ===')
for r in bad_id:
    print(f'  id={r["id"]}  imdb_id={r["imdb_id"]!r}  title={r["title"]!r}  path={r["path"]!r}')

# ---- No poster ----
no_poster = [r for r in db_movies if not r['poster_url']]
print(f'\n=== No poster ({len(no_poster)}) ===')
for r in no_poster:
    print(f'  id={r["id"]}  imdb_id={r["imdb_id"]!r}  title={r["title"]!r}')

print('\n--- Summary ---')
print(f'  Disk entries  : {len(disk_paths)}')
print(f'  DB movies     : {len(db_movies)}')
print(f'  Missing from DB: {len(missing_from_db)}')
print(f'  Stale in DB   : {len(stale_in_db)}')
print(f'  Bad IDs       : {len(bad_id)}')
print(f'  No poster     : {len(no_poster)}')
