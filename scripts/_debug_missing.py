"""
Search library.db for items matching one or more title/path keywords.
Usage: python scripts/_debug_missing.py <keyword> [<keyword> ...]
Example: python scripts/_debug_missing.py venom spider
"""
import sqlite3
import sys

if len(sys.argv) < 2:
    print('Usage: python scripts/_debug_missing.py <keyword> [<keyword> ...]')
    sys.exit(1)

targets = [a.lower() for a in sys.argv[1:]]

conn = sqlite3.connect('library.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

clauses = ' OR '.join(
    'lower(title) LIKE ? OR lower(path) LIKE ?' for _ in targets
)
params = [v for t in targets for v in (f'%{t}%', f'%{t}%')]
cur.execute(
    f'SELECT id, imdb_id, title, year, media_type, poster_url, path FROM media_items WHERE {clauses}',
    params,
)
rows = cur.fetchall()
for r in rows:
    print(dict(r))
print(f'\nFound: {len(rows)}')
conn.close()
