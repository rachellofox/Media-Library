"""An item that is downloading must not be offered a quality upgrade.

Renders the real page against a throwaway DB and inspects the card badges.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


tmp = tempfile.mkdtemp()
movies = os.path.join(tmp, 'Movies')
os.makedirs(movies, exist_ok=True)
app.store = Storage(os.path.join(tmp, 't.db'))
app.store.initialize()
app.store.set_setting('movies_path', movies)
app.store.set_setting('tv_path', os.path.join(tmp, 'TV'))
app.store.set_setting('preferred_quality', '2160p')
app.app.config['TESTING'] = True
app._public_access_enabled = lambda: False


def make_film(imdb, title, quality):
    folder = os.path.join(movies, f'{title} (2001)')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f'{title} (2001).mkv'), 'wb') as fh:
        fh.write(b'\x00' * 4096)
    app.store.add_media_item(
        imdb_id=imdb,
        tmdb_id=None,
        title=title,
        year=2001,
        media_type='movie',
        collection_id=None,
        collection_name=None,
        current_quality=quality,
        path=folder,
        poster_url='/x.jpg',
        synopsis='s',
    )
    return app.store.get_media_item_by_imdb_id(imdb)['id']


# Both own a 1080p file and have a 2160p release on offer, so both are genuinely
# upgradeable — the only difference is that one is mid-download.
idle_id = make_film('tt1000', 'Idle Film', '1080p')
busy_id = make_film('tt2000', 'Busy Film', '1080p')
for media_id in (idle_id, busy_id):
    app.store.add_quality_check(
        media_item_id=media_id,
        best_found_quality='2160p',
        best_found_name='rel',
        best_found_desc_link='x',
        found=True,
        raw_result_count=1,
    )
app.store.set_download_state(
    media_item_id=busy_id,
    status='downloading',
    source='qb_webui',
    message='x',
    torrent_hash='A' * 40,
)

rows = {r['id']: r for r in app.store.list_media_items()}
print('\n=== the data both cards are rendered from ===')
for label, media_id in (('idle', idle_id), ('downloading', busy_id)):
    row = rows[media_id]
    print(
        f'  {label:12} upgrade_available={app._upgrade_available(row)} '
        f'status={row["download_status"]!r}'
    )
check(
    'both items genuinely have an upgrade available',
    app._upgrade_available(rows[idle_id]) and app._upgrade_available(rows[busy_id]),
)

html = app.app.test_client().get('/?section=movies').get_data(as_text=True)

# The card markup is server-rendered, but the behaviour now lives in a static
# script rather than inline, so assertions about it read the file.
with open(os.path.join(REPO_ROOT, 'static', 'js', 'library.js'), encoding='utf-8') as _f:
    script = _f.read()


def card_html(media_id):
    """Just this card's markup — cards are adjacent, so a fixed window would
    spill into the next one and read its badges."""
    start = html.find(f'id="card-{media_id}"')
    if start < 0:
        return ''
    nxt = html.find('id="card-', start + 10)
    return html[start:nxt] if nxt > start else html[start : start + 1400]


idle_card = card_html(idle_id)
busy_card = card_html(busy_id)

print('\n=== server-rendered card badges ===')
check('both cards rendered', bool(idle_card) and bool(busy_card))
check('idle film shows the upgrade badge', 'card-badge-upgrade' in idle_card)
check(
    'downloading film does NOT show the upgrade badge',
    'card-badge-upgrade' not in busy_card,
    'upgrade badge still present while downloading',
)
check('downloading film shows the download badge', 'card-badge-download' in busy_card)
check('idle film has no download badge', 'card-badge-download' not in idle_card)

print('\n=== hero gate in the shipped script ===')
condition = script[script.find('const shouldShowUpgrade') :]
condition = condition[: condition.find(';') + 1]
check(
    'hero excludes downloading items',
    'libraryItemDownloading(item)' in condition,
    condition.replace('\n', ' ')[:120],
)
check('hero still excludes missing files', 'item.file_missing' in condition)

print('\n=== client-rendered card badge was already correct ===')
marker = 'card-badge-upgrade" title="Higher quality available">&#x2191;</div>\' : \'\''
client = script[script.find(marker) - 260 :]
check('client card badge excludes downloading', 'libraryItemDownloading(item)' in client[:400])

print(f'\n{"=" * 60}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
if FAIL:
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
