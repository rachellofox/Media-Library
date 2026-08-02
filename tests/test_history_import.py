"""F-0208.01: reading a watch-history export from another platform.

Three known shapes (Trakt JSON, Letterboxd CSV, IMDb CSV) plus a generic
column-matching fallback for JSON or CSV whose exact source isn't known. Pure
parsing — no database, no Flask — see test_history_store.py and
test_history_import_route.py for the rest.
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from medialibrary.history_import import UnrecognisedFormat, parse_upload

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


print('\n=== 1. Trakt JSON export: movies and episodes ===')
trakt_export = [
    {
        'id': 1,
        'watched_at': '2024-01-05T20:00:00.000Z',
        'action': 'watch',
        'type': 'movie',
        'movie': {
            'title': 'Inception',
            'year': 2010,
            'ids': {'trakt': 1, 'imdb': 'tt1375666', 'tmdb': 27205},
        },
    },
    {
        'id': 2,
        'watched_at': '2024-01-06T21:00:00.000Z',
        'action': 'watch',
        'type': 'episode',
        'episode': {'season': 1, 'number': 1, 'title': 'Pilot', 'ids': {}},
        'show': {'title': 'Breaking Bad', 'year': 2008, 'ids': {'imdb': 'tt0903747', 'tmdb': 1396}},
    },
]
raw, normalized, fmt = parse_upload('history.json', json.dumps(trakt_export).encode())
check('detected as trakt-json', fmt == 'trakt-json', fmt)
check('both records kept', len(normalized) == 2, normalized)
movie, episode = normalized
check('movie title', movie['title'] == 'Inception')
check('movie media_type', movie['media_type'] == 'movie')
check('movie imdb_id', movie['imdb_id'] == 'tt1375666')
check('movie tmdb_id', movie['tmdb_id'] == 27205)
check('movie watched_at carried through', movie['watched_at'] == '2024-01-05T20:00:00.000Z')
check('episode title is the SHOW title, not the episode title', episode['title'] == 'Breaking Bad')
check('episode media_type', episode['media_type'] == 'tv')
check('episode season/number', (episode['season_number'], episode['episode_number']) == (1, 1))
check('episode imdb_id from the show', episode['imdb_id'] == 'tt0903747')

print('\n=== 2. Generic JSON: flexible field names ===')
generic = [
    {'Name': 'The Matrix', 'Year': '1999', 'Kind': 'movie', 'Date': '2024-02-01'},
    {'name': 'Fargo', 'year': 2014, 'type': 'series', 'watched_date': '2024-02-02'},
]
raw, normalized, fmt = parse_upload('export.json', json.dumps(generic).encode())
check('detected as generic-json', fmt == 'generic-json', fmt)
check('both records kept', len(normalized) == 2)
check('field-name case does not matter', normalized[0]['title'] == 'The Matrix')
check(
    'year coerced to int from a string',
    normalized[0]['year'] == 1999 and isinstance(normalized[0]['year'], int),
)
check('"movie" recognised', normalized[0]['media_type'] == 'movie')
check('"series" recognised as tv', normalized[1]['media_type'] == 'tv')
check('lowercase field names also work', normalized[1]['title'] == 'Fargo')

print('\n=== 3. Letterboxd diary CSV ===')
letterboxd = (
    'Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags,Watched Date\n'
    '2024-03-01,Paddington 2,2017,https://letterboxd.com/x/,4.5,,,2024-02-28\n'
    '2024-03-02,Everything Everywhere All at Once,2022,https://letterboxd.com/y/,5,Yes,,2024-03-02\n'
)
raw, normalized, fmt = parse_upload('diary.csv', letterboxd.encode())
check('detected as letterboxd-csv', fmt == 'letterboxd-csv', fmt)
check('both rows kept', len(normalized) == 2)
check('title from Name', normalized[0]['title'] == 'Paddington 2')
check('year parsed', normalized[0]['year'] == 2017)
check('always movie', all(r['media_type'] == 'movie' for r in normalized))
check('prefers Watched Date over Date', normalized[0]['watched_at'] == '2024-02-28')

print('\n=== 4. Letterboxd watched-films export (no separate Watched Date column) ===')
letterboxd_watched = (
    'Date,Name,Year,Letterboxd URI\n2024-04-01,Amelie,2001,https://letterboxd.com/z/\n'
)
raw, normalized, fmt = parse_upload('watched.csv', letterboxd_watched.encode())
check('still detected as letterboxd-csv', fmt == 'letterboxd-csv', fmt)
check(
    'falls back to Date when there is no Watched Date', normalized[0]['watched_at'] == '2024-04-01'
)

print('\n=== 5. IMDb ratings CSV ===')
imdb = (
    'Const,Your Rating,Date Rated,Title,Title Type,Year\n'
    'tt0111161,10,2024-01-01,The Shawshank Redemption,movie,1994\n'
    'tt0903747,9,2024-01-02,Breaking Bad,tvSeries,2008\n'
    ',8,2024-01-03,Untitled Thing,movie,2020\n'
)
raw, normalized, fmt = parse_upload('ratings.csv', imdb.encode())
check('detected as imdb-csv', fmt == 'imdb-csv', fmt)
check('all three rows kept (title present in every row)', len(normalized) == 3, normalized)
check('movie type recognised', normalized[0]['media_type'] == 'movie')
check('tvSeries recognised as tv', normalized[1]['media_type'] == 'tv')
check('imdb_id carried for a real tt id', normalized[0]['imdb_id'] == 'tt0111161')
check('a blank Const does not fabricate an imdb_id', normalized[2]['imdb_id'] is None)
check('Date Rated used as watched_at', normalized[0]['watched_at'] == '2024-01-01')

print('\n=== 6. Generic CSV: an export from neither platform ===')
generic_csv = 'title,release_year,media_type,date\nDune,2021,movie,2024-05-01\n'
raw, normalized, fmt = parse_upload('mystery.csv', generic_csv.encode())
check('detected as generic-csv', fmt == 'generic-csv', fmt)
check('title read', normalized[0]['title'] == 'Dune')
check('year alias "release_year" read', normalized[0]['year'] == 2021)
check('date alias "date" read as watched_at', normalized[0]['watched_at'] == '2024-05-01')

print('\n=== 7. Records with no usable title are dropped, not invented ===')
mixed = [
    {'title': 'Has A Title', 'year': 2020},
    {'title': '', 'year': 2021},
    {'year': 2022},  # no title key at all
]
raw, normalized, fmt = parse_upload('mixed.json', json.dumps(mixed).encode())
check('only the one titled record survives', len(normalized) == 1, normalized)
check('raw and normalised stay aligned by position', raw[0]['title'] == 'Has A Title')

print('\n=== 8. Format detection without a helpful extension ===')
raw, normalized, fmt = parse_upload('export', json.dumps(trakt_export).encode())
check('JSON content sniffed correctly with no .json extension', fmt == 'trakt-json', fmt)
raw, normalized, fmt = parse_upload('export', letterboxd.encode())
check('CSV content sniffed correctly with no .csv extension', fmt == 'letterboxd-csv', fmt)

print('\n=== 9. Genuinely bad input is rejected, not silently emptied ===')
try:
    parse_upload('bad.json', b'{not valid json')
    check('invalid JSON raises', False)
except UnrecognisedFormat:
    check('invalid JSON raises', True)

try:
    parse_upload('empty.csv', b'just,a,header\nrow with no title column,x,y\n')
    check('CSV with no recognisable title column raises', False)
except UnrecognisedFormat:
    check('CSV with no recognisable title column raises', True)

try:
    parse_upload('empty.json', json.dumps([]).encode())
    check('an empty JSON list raises rather than importing nothing silently', False)
except UnrecognisedFormat:
    check('an empty JSON list raises rather than importing nothing silently', True)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
