"""F-0208.01: the history database itself — separate from library.db.

Covers what parsing tests can't: that re-importing the same file is safe (no
duplicate rows), that a genuine rewatch with a different date is NOT treated
as a duplicate, and that the store never touches media_items.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from medialibrary.history_import import parse_upload
from medialibrary.history_store import HistoryStore

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


tmp = tempfile.mkdtemp()
store = HistoryStore(os.path.join(tmp, 'history.db'))
store.initialize()

LETTERBOXD = (
    'Date,Name,Year,Letterboxd URI,Watched Date\n'
    '2024-01-01,Paddington 2,2017,https://letterboxd.com/x/,2024-01-01\n'
    '2024-01-02,Amelie,2001,https://letterboxd.com/y/,2024-01-02\n'
)

print('\n=== 1. A first import stores every record ===')
raw, normalized, fmt = parse_upload('diary.csv', LETTERBOXD.encode())
result = store.import_records(raw, normalized, fmt, 'diary.csv')
check('both records imported', result['imported'] == 2, result)
check('nothing skipped the first time', result['skipped_duplicate'] == 0, result)
check('store count matches', store.count() == 2, store.count())

print('\n=== 2. Re-importing the exact same file imports nothing new ===')
raw2, normalized2, fmt2 = parse_upload('diary.csv', LETTERBOXD.encode())
result2 = store.import_records(raw2, normalized2, fmt2, 'diary.csv')
check('nothing newly imported', result2['imported'] == 0, result2)
check('both rows reported as duplicates', result2['skipped_duplicate'] == 2, result2)
check('store count unchanged', store.count() == 2, store.count())

print('\n=== 3. An updated export with one new row only adds the new one ===')
LETTERBOXD_UPDATED = LETTERBOXD + '2024-01-03,Dune,2021,https://letterboxd.com/z/,2024-01-03\n'
raw3, normalized3, fmt3 = parse_upload('diary.csv', LETTERBOXD_UPDATED.encode())
result3 = store.import_records(raw3, normalized3, fmt3, 'diary2.csv')
check('only the new row imported', result3['imported'] == 1, result3)
check('the two old rows skipped as duplicates', result3['skipped_duplicate'] == 2, result3)
check('store count grew by exactly one', store.count() == 3, store.count())

print('\n=== 4. A genuine rewatch (same title, different date) is not a duplicate ===')
rewatch_a = 'Date,Name,Year,Letterboxd URI\n2024-02-01,Amelie,2001,https://letterboxd.com/a/\n'
rewatch_b = 'Date,Name,Year,Letterboxd URI\n2024-06-01,Amelie,2001,https://letterboxd.com/a/\n'
r1, n1, f1 = parse_upload('a.csv', rewatch_a.encode())
r2, n2, f2 = parse_upload('b.csv', rewatch_b.encode())
res1 = store.import_records(r1, n1, f1, 'a.csv')
res2 = store.import_records(r2, n2, f2, 'b.csv')
check('first watch imported', res1['imported'] == 1, res1)
check('second watch imported too, not skipped', res2['imported'] == 1, res2)

print('\n=== 5. summary() reports totals and sources ===')
summary = store.summary()
check('total matches the store count', summary['total'] == store.count(), summary)
sources = {row['source'] for row in summary['by_source']}
check('every source that imported something appears', sources == {'letterboxd-csv'}, sources)

print('\n=== 6. This is a completely separate database from library.db ===')
import sqlite3

connection = sqlite3.connect(store.db_path)
tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
connection.close()
check('watch_history exists', 'watch_history' in tables)
check('media_items does not exist in this database', 'media_items' not in tables, tables)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
