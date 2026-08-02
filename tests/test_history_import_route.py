"""F-0208.01: the Settings-page upload route, end to end.

Real file upload through the Flask test client — not a call into the parser
or store directly — so this catches anything that only breaks at the HTTP
layer (multipart handling, the size cap, error status codes).
"""

import io
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary.auth import _auth_username
from medialibrary.history_store import HistoryStore

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


tmp = tempfile.mkdtemp()
app.history_store = HistoryStore(os.path.join(tmp, 'history.db'))
app.history_store.initialize()

client = app.app.test_client()
with client.session_transaction() as session:
    session['auth_user'] = _auth_username()

LETTERBOXD = (
    'Date,Name,Year,Letterboxd URI,Watched Date\n'
    '2024-01-01,Paddington 2,2017,https://letterboxd.com/x/,2024-01-01\n'
)

print('\n=== 1. Uploading a real file imports it ===')
resp = client.post(
    '/api/settings/history-import',
    data={'file': (io.BytesIO(LETTERBOXD.encode()), 'diary.csv')},
    content_type='multipart/form-data',
)
check('200 OK', resp.status_code == 200, resp.status_code)
payload = resp.get_json()
check('ok', payload.get('ok') is True, payload)
check('one record imported', payload.get('imported') == 1, payload)
check('format reported', payload.get('format') == 'letterboxd-csv', payload)
check('it actually landed in the store', app.history_store.count() == 1, app.history_store.count())

print('\n=== 2. Re-uploading the same file reports it as a duplicate, not an error ===')
resp2 = client.post(
    '/api/settings/history-import',
    data={'file': (io.BytesIO(LETTERBOXD.encode()), 'diary.csv')},
    content_type='multipart/form-data',
)
payload2 = resp2.get_json()
check('still 200 OK', resp2.status_code == 200, resp2.status_code)
check('nothing newly imported', payload2.get('imported') == 0, payload2)
check('reported as a duplicate', payload2.get('skipped_duplicate') == 1, payload2)
check('store count unchanged', app.history_store.count() == 1)

print('\n=== 3. No file at all ===')
resp3 = client.post('/api/settings/history-import', data={}, content_type='multipart/form-data')
check('400', resp3.status_code == 400, resp3.status_code)
check('reason given', resp3.get_json().get('error') == 'no_file', resp3.get_json())

print('\n=== 4. An empty file ===')
resp4 = client.post(
    '/api/settings/history-import',
    data={'file': (io.BytesIO(b''), 'empty.csv')},
    content_type='multipart/form-data',
)
check('400', resp4.status_code == 400, resp4.status_code)
check('reason given', resp4.get_json().get('error') == 'empty_file', resp4.get_json())

print('\n=== 5. A file that is neither JSON nor a recognisable CSV ===')
resp5 = client.post(
    '/api/settings/history-import',
    data={'file': (io.BytesIO(b'just some text, not a header row at all'), 'notes.csv')},
    content_type='multipart/form-data',
)
check('400', resp5.status_code == 400, resp5.status_code)
check(
    'a specific reason is given, not a generic failure',
    resp5.get_json().get('error') == 'unrecognised_format' and resp5.get_json().get('message'),
    resp5.get_json(),
)
check('nothing was imported from it', app.history_store.count() == 1)

print('\n=== 6. history-summary reflects what has actually been imported ===')
resp6 = client.get('/api/settings/history-summary')
summary = resp6.get_json()
check('200 OK', resp6.status_code == 200)
check('total matches', summary.get('total') == 1, summary)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
