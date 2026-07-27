"""Playback routes must not serve files from outside the transcode cache.

The direct-stream route once did. It rejected '..' and a leading '/', which
looks sufficient but is not: os.path.join discards the base when the second
part is absolute, so "C:/anywhere/file.mp4" walked straight out of the cache
directory and was served with HTTP 200.

Everything here uses files this test creates under a temporary directory.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


CANARY = b'CANARY-outside-the-cache-directory'

# Auth is a separate control and is asserted on its own below; switch it off so
# these assertions are about path handling rather than the front door.
app._auth_required = lambda: False
client = app.app.test_client()

# A media id that does not exist. The filename filters on both routes run before
# the item is looked up, so they are still exercised — but nothing resolves to a
# real video, and nothing starts a transcode.
#
# This matters: an earlier version used a real library id, and requesting a
# well-formed segment name is a legitimate playback request. It started ffmpeg
# against an actual film, which then ran unattended at full CPU. A test must not
# be able to do that.
media_id = 999999999

with tempfile.TemporaryDirectory() as tmp:
    bait = os.path.join(tmp, 'bait.mp4')
    with open(bait, 'wb') as handle:
        handle.write(CANARY)

    drive_relative = bait.split(':', 1)[1].lstrip('/\\').replace('\\', '/')
    attempts = [
        ('absolute path with forward slashes', bait.replace('\\', '/')),
        ('absolute path with backslashes', bait),
        ('dot-dot escape', '../' * 12 + drive_relative),
        ('url-encoded dot-dot', '..%2f' * 12 + drive_relative),
    ]

    print('\n=== direct-stream route ===')
    for label, filename in attempts:
        response = client.get(f'/api/video/{media_id}/direct-stream/{filename}')
        body = response.get_data()
        check(
            f'{label} is refused',
            CANARY not in body and response.status_code != 200,
            f'HTTP {response.status_code}, leaked={CANARY in body}',
        )

    print('\n=== hls route ===')
    for label, filename in attempts:
        response = client.get(f'/api/video/{media_id}/hls/{filename}')
        body = response.get_data()
        check(
            f'{label} is refused',
            CANARY not in body and response.status_code != 200,
            f'HTTP {response.status_code}, leaked={CANARY in body}',
        )

    print('\n=== a legitimate segment name is still accepted as a name ===')
    # It will 404 because no transcode is running, but it must not be rejected
    # as invalid — a fix that broke normal playback would be no fix at all.
    response = client.get(f'/api/video/{media_id}/direct-stream/segment_001.m4s')
    check(
        'ordinary segment name is not treated as an attack',
        response.status_code == 404,
        f'HTTP {response.status_code}',
    )
    check(
        'and it was not rejected by the path filter',
        b'Invalid filename' not in response.get_data(),
    )
    response = client.get(f'/api/video/{media_id}/hls/segment_00000.ts')
    check(
        'ordinary hls segment name is not treated as an attack',
        response.status_code != 400,
        f'HTTP {response.status_code}',
    )

print('\n=== ?episode= cannot escape the show folder ===')
with tempfile.TemporaryDirectory() as tmp:
    show = os.path.join(tmp, 'Show')
    os.makedirs(show)
    inside = os.path.join(show, 'Show - S01E01.mkv')
    with open(inside, 'wb') as handle:
        handle.write(b'x')
    outside = os.path.join(tmp, 'outside.mkv')
    with open(outside, 'wb') as handle:
        handle.write(CANARY)

    check(
        'an episode inside the show folder resolves',
        app._resolve_episode_file(show, 'Show - S01E01.mkv') == os.path.realpath(inside),
    )
    check(
        '..\\ escape is refused',
        app._resolve_episode_file(show, os.path.join('..', 'outside.mkv')) is None,
    )
    check('an absolute path is refused', app._resolve_episode_file(show, outside) is None)
    check(
        'a non-video inside the folder is refused',
        app._resolve_episode_file(show, 'Show - S01E01.mkv.txt') is None,
    )

print(f'\n{"=" * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
for name in FAIL:
    print('  -', name)
sys.exit(1 if FAIL else 0)
