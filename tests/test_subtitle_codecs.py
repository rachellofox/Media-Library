"""B-0108.01: only text subtitle codecs are offered, never bitmap ones.

Found live against a real file: an X-Men rip's CC menu listed six embedded
tracks, but three of them — DVD-sourced French, Spanish and a second English —
are `dvd_subtitle`, a bitmap format. ffmpeg's `-c:s webvtt` has nothing to
convert a bitmap to; picking one of those tracks always failed extraction, and
the browser just never got a track — indistinguishable from "subtitles do not
work" unless you check the server log.

Stubs subprocess.run with a canned ffprobe payload rather than needing a real
file encoded with a bitmap subtitle codec, which is impractical to fabricate
for a test. The real X-Men file was used to find and confirm this by hand; see
the commit message for that verification.
"""

import json
import os
import sys
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from medialibrary.subtitles import _probe_embedded_subtitles

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def stream(index, codec, lang, title=None):
    tags = {'language': lang}
    if title:
        tags['title'] = title
    return {'index': index, 'codec_type': 'subtitle', 'codec_name': codec, 'tags': tags}


# The real X-Men shape, reduced: three bitmap tracks (unusable) and one usable
# text track, matching the file the bug was found on.
FFPROBE_PAYLOAD = json.dumps(
    {
        'streams': [
            stream(4, 'dvd_subtitle', 'fre'),
            stream(5, 'dvd_subtitle', 'spa'),
            stream(6, 'dvd_subtitle', 'eng'),
            stream(9, 'subrip', 'eng'),
        ]
    }
)


def fake_result(payload):
    return type('R', (), {'returncode': 0, 'stdout': payload})()


print('\n=== 1. Bitmap tracks are left out, the text track is offered ===')
with mock.patch('subprocess.run', return_value=fake_result(FFPROBE_PAYLOAD)):
    subs = _probe_embedded_subtitles('irrelevant.mkv')

check('only one track is offered, not four', len(subs) == 1, subs)
check('it is the subrip stream (index 9)', subs and subs[0]['stream_index'] == 9, subs)
check(
    'the three dvd_subtitle tracks are gone, including the English-tagged one',
    all(s['stream_index'] != 6 for s in subs),
    subs,
)

print('\n=== 2. A file with only bitmap subtitles offers none, not a broken one ===')
only_bitmap = json.dumps({'streams': [stream(4, 'hdmv_pgs_subtitle', 'eng')]})
with mock.patch('subprocess.run', return_value=fake_result(only_bitmap)):
    subs = _probe_embedded_subtitles('irrelevant.mkv')
check('no unusable track is offered', subs == [], subs)

print('\n=== 3. Text codecs beyond subrip are still recognised ===')
for codec in ('ass', 'ssa', 'mov_text', 'webvtt'):
    payload = json.dumps({'streams': [stream(0, codec, 'eng')]})
    with mock.patch('subprocess.run', return_value=fake_result(payload)):
        subs = _probe_embedded_subtitles('irrelevant.mkv')
    check(f'{codec} is offered', len(subs) == 1, subs)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
