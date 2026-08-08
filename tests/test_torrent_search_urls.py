"""F-0108.08: a main torrent URL, tried first, with mirrors as fallback only.

kickasstorrents.to used to be down, which is why mirrors existed at all; now
it's back, search should prefer it and only fall through to mirrors if it
fails. Covers the three functions in medialibrary.qb_search that decide what
gets searched, and the Settings route that saves the main URL.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary import qb_search, runtime
from medialibrary.auth import _auth_username
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


tmp = tempfile.mkdtemp()
app.store = Storage(os.path.join(tmp, 't.db'))
app.store.initialize()


print('\n=== 1. Nothing configured: defaults are main URL first, then mirrors ===')
check('default main URL', qb_search.configured_main_url() == qb_search.DEFAULT_MAIN_URL)
check(
    'default mirrors do not repeat the main URL',
    qb_search.DEFAULT_MAIN_URL not in qb_search.DEFAULT_MIRROR_URLS,
)
search_urls = qb_search.configured_search_urls()
check('main URL is first', search_urls[0] == qb_search.DEFAULT_MAIN_URL, search_urls)
check(
    'defaults mirrors follow',
    search_urls[1:] == qb_search.DEFAULT_MIRROR_URLS,
    search_urls,
)

print('\n=== 2. A configured main URL is tried first, ahead of the default mirrors ===')
app.store.set_setting('torrent_main_url', 'https://kickasstorrents.to/')
check(
    'trailing slash stripped',
    qb_search.configured_main_url() == 'https://kickasstorrents.to',
)
search_urls = qb_search.configured_search_urls()
check(
    'configured main URL still first',
    search_urls[0] == 'https://kickasstorrents.to',
    search_urls,
)
check('mirrors still follow', search_urls[1:] == qb_search.DEFAULT_MIRROR_URLS, search_urls)

print('\n=== 3. Configured mirrors replace the defaults, main URL still leads ===')
app.store.set_setting('mirror_urls', 'https://example-mirror.one\nhttps://example-mirror.two')
check(
    'configured_mirror_urls reflects the saved list',
    qb_search.configured_mirror_urls() == ['https://example-mirror.one', 'https://example-mirror.two'],
)
search_urls = qb_search.configured_search_urls()
check(
    'main URL leads, then the configured mirrors, nothing dropped',
    search_urls == [
        'https://kickasstorrents.to',
        'https://example-mirror.one',
        'https://example-mirror.two',
    ],
    search_urls,
)

print('\n=== 4. The main URL is never duplicated if it also appears in mirrors ===')
app.store.set_setting(
    'mirror_urls', 'https://kickasstorrents.to\nhttps://example-mirror.one'
)
search_urls = qb_search.configured_search_urls()
check(
    'main URL appears exactly once, still first',
    search_urls == ['https://kickasstorrents.to', 'https://example-mirror.one'],
    search_urls,
)

print('\n=== 5. Blanking the main URL setting falls back to the default ===')
app.store.set_setting('torrent_main_url', '')
check(
    'blank main URL resets to default',
    qb_search.configured_main_url() == qb_search.DEFAULT_MAIN_URL,
)

print('\n=== 6. Settings page: saving a main torrent URL persists it ===')
app.store.set_setting('mirror_urls', '')
client = app.app.test_client()
with client.session_transaction() as session:
    session['auth_user'] = _auth_username()
resp = client.post(
    '/settings',
    data={'torrent_main_url': 'https://kat.example.org/'},
    follow_redirects=False,
)
check('save redirects', resp.status_code in (301, 302), resp.status_code)
check(
    'saved with the trailing slash stripped',
    runtime.store().get_setting('torrent_main_url') == 'https://kat.example.org',
)
check(
    'search order now leads with the saved URL',
    qb_search.configured_search_urls()[0] == 'https://kat.example.org',
)

print('\n=== 7. The Settings page renders the saved main URL back ===')
home = client.get('/')
check(
    'main torrent URL field carries the saved value',
    'value="https://kat.example.org"' in home.get_data(as_text=True),
)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
