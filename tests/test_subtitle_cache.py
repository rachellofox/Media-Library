"""B-0108.01: two episodes of a show must not share one subtitle cache slot.

_subtitle_cache was keyed by media_id alone. A show's episodes all share a
media_id, and each /video/<id>?episode=... request repopulates that one slot
wholesale — so loading episode 2 could leave episode 1's own <track> fetch
(still in flight, or from a second tab open on the same show) reading episode
2's subtitle mapping instead of its own. Keyed on (media_id, episode) now.

Reproduces it with two real, distinguishable subtitle files rather than
asserting on internals, so this fails the way a user would actually see it —
the wrong captions.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from medialibrary.auth import _auth_username
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


SRT = '1\n00:00:01,000 --> 00:00:02,000\n{text}\n'


def make(path, content=b'\0' * 4096):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(content)


with tempfile.TemporaryDirectory() as tmp:
    show = os.path.join(tmp, 'A Show')
    season = os.path.join(show, 'Season 01')
    ep1_video = os.path.join(season, 'A Show - S01E01 - First.mkv')
    ep2_video = os.path.join(season, 'A Show - S01E02 - Second.mkv')
    make(ep1_video)
    make(ep2_video)
    make(
        os.path.join(season, 'A Show - S01E01 - First.srt'),
        SRT.format(text='episode one caption').encode(),
    )
    make(
        os.path.join(season, 'A Show - S01E02 - Second.srt'),
        SRT.format(text='episode two caption').encode(),
    )

    app.store = Storage(os.path.join(tmp, 't.db'))
    app.store.initialize()
    app.store.add_media_item(
        imdb_id='tt1',
        tmdb_id=None,
        title='A Show',
        year=2020,
        media_type='tv',
        collection_id=None,
        collection_name=None,
        current_quality=None,
        path=show,
    )
    media_id = app.store.get_media_item_by_imdb_id('tt1')['id']

    client = app.app.test_client()
    with client.session_transaction() as session:
        session['auth_user'] = _auth_username()

    ep1_rel = os.path.join('Season 01', 'A Show - S01E01 - First.mkv')
    ep2_rel = os.path.join('Season 01', 'A Show - S01E02 - Second.mkv')

    print('\n=== 1. Loading episode 1 then episode 2 does not cross-contaminate ===')
    r1 = client.get(f'/video/{media_id}?episode={ep1_rel}')
    check('episode 1 page loads', r1.status_code == 200, r1.status_code)
    r2 = client.get(f'/video/{media_id}?episode={ep2_rel}')
    check('episode 2 page loads', r2.status_code == 200, r2.status_code)

    # Episode 1's cache slot must still exist and still be its own, even though
    # episode 2's page load ran afterwards and populated the same media_id.
    sub1 = client.get(f'/api/video/{media_id}/subtitle/0?episode={ep1_rel}')
    sub2 = client.get(f'/api/video/{media_id}/subtitle/0?episode={ep2_rel}')
    body1 = sub1.get_data(as_text=True)
    body2 = sub2.get_data(as_text=True)
    check('episode 1 subtitle request succeeds', sub1.status_code == 200, sub1.status_code)
    check('episode 2 subtitle request succeeds', sub2.status_code == 200, sub2.status_code)
    check('episode 1 gets its own caption', 'episode one caption' in body1, body1)
    check('episode 2 gets its own caption', 'episode two caption' in body2, body2)
    check("episode 1 does NOT get episode 2's caption", 'episode two caption' not in body1, body1)
    check("episode 2 does NOT get episode 1's caption", 'episode one caption' not in body2, body2)

    print('\n=== 2. Re-requesting episode 1 after episode 2 still works ===')
    # The scenario a real session hits: browse to episode 2, come back to
    # episode 1 — its cache slot must not have been evicted by episode 2's.
    r1_again = client.get(f'/video/{media_id}?episode={ep1_rel}')
    sub1_again = client.get(f'/api/video/{media_id}/subtitle/0?episode={ep1_rel}')
    check('episode 1 reloads fine', r1_again.status_code == 200)
    check(
        'and still serves its own caption',
        'episode one caption' in sub1_again.get_data(as_text=True),
    )

    print('\n=== 3. A request for a never-cached episode 404s rather than guessing ===')
    stray = client.get(f'/api/video/{media_id}/subtitle/0?episode=Season 01/nonexistent.mkv')
    check("unrecognised episode gets 404, not someone else's captions", stray.status_code == 404)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
