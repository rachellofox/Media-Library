"""
Poster-fetch diagnostic script.
Run with a temporary TMDB API key argument to get live API results.
"""

import sqlite3
import sys

conn = sqlite3.connect('library.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# --- 1. Show items with no poster ---
print('=== Items with no poster ===')
cur.execute("""
    SELECT id, imdb_id, title, year, media_type
    FROM media_items
    WHERE poster_url IS NULL OR poster_url = ''
    ORDER BY title
""")
no_poster = cur.fetchall()
for r in no_poster:
    print(f'  id={r["id"]}  imdb_id={r["imdb_id"]}  title={r["title"]!r}')
print(f'Total: {len(no_poster)}\n')

conn.close()

api_key = (sys.argv[1] if len(sys.argv) > 1 else '').strip()
if not api_key:
    print('TMDB API key not provided — skipping live API tests')
    print('Usage:  .\\.venv\\Scripts\\python.exe scripts\\_debug_query.py <tmdb_api_key>')
    sys.exit(0)

from medialibrary.tmdb_client import TmdbClient

tmdb = TmdbClient(api_key=api_key)

# --- 2. Test the four specific user-requested titles via search ---
print('=== Search test for requested titles ===')
test_searches = [
    'Underworld Evolution',
    'Thor Ragnarok',
    'The Nun 2',
    'TAR',
]
for query in test_searches:
    results = tmdb.search(query)
    print(f'\nSearch: {query!r}')
    if not results:
        print('  [NO RESULTS]')
    for r in results[:2]:
        print(
            f'  tmdb_id={r["tmdb_id"]}  type={r["media_type"]}  '
            f'title={r["title"]!r}  year={r["year"]}  '
            f'has_thumb={"yes" if r.get("thumbnail") else "NO"}'
        )

# --- 3. Test metadata_by_imdb_id for each broken raw-ID entry ---
broken_ids = [r['imdb_id'] for r in no_poster if r['imdb_id'] and r['imdb_id'].startswith('tt')]
if broken_ids:
    print(f'\n=== TMDB /find lookup for {len(broken_ids)} raw-IMDb-ID entries ===')
    for imdb_id in broken_ids:
        meta = tmdb.metadata_by_imdb_id(imdb_id)
        ok = bool(meta.get('poster_url'))
        print(
            f'  {imdb_id} -> title={meta.get("title")!r}  '
            f'type={meta.get("media_type")}  '
            f'poster={"YES" if ok else "MISSING"}'
        )
