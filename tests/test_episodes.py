"""Episode scanning, resolution and playback plumbing. Throwaway dirs only."""
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app

PASS, FAIL = [], []


def same(a, b):
    """Compare paths regardless of separator style and case."""
    if not a or not b:
        return False
    def norm(v):
        return os.path.normcase(os.path.normpath(v))

    return norm(a) == norm(b)


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}{("  -- " + str(detail)) if detail and not cond else ""}')


tmp = tempfile.mkdtemp()
show = os.path.join(tmp, 'Some Show')


def make(rel, size=1024):
    full = os.path.join(show, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'wb') as fh:
        fh.write(b'\x00' * size)
    return full


print('\n=== 1. SxxExx parsing ===')
make('Season 01/Show - S01E01 - Pilot.mkv')
make('Season 01/Show.S01E02.Second.mkv')
make('Season 1/Show S01E03 - Third.mp4')          # unpadded folder, same season
make('Season 02/Show_s02e01_Return.mkv')          # lowercase
make('Season 02/Show S02E10.mkv')
make('Extras/Behind the Scenes.mkv')              # no marker
make('Season 02/Season 2 Outtakes.mkv')           # no marker
matched, unmatched = app.scan_local_episodes(show)
check('finds all 5 marked episodes', len(matched) == 5, sorted(matched))
check('season 1 has 3 episodes', sum(1 for s, _ in matched if s == 1) == 3)
check('lowercase s02e01 parsed', (2, 1) in matched)
check('3-digit-safe E10 parsed', (2, 10) in matched)
check('unpadded folder does not affect season', (1, 3) in matched)
check('2 unmatched files kept aside', len(unmatched) == 2, unmatched)
check('extras are not given episode numbers',
      not any('Behind' in p for p in matched.values()))

print('\n=== 2. Folder is ignored; the filename wins ===')
mislabelled = make('Season 09/Show - S03E07 - Wrong Folder.mkv')
matched, _ = app.scan_local_episodes(show)
check('S03E07 indexed from its name, not Season 09', same(matched.get((3, 7)), mislabelled))

print('\n=== 3. Duplicate rips keep the largest ===')
small = make('Season 01/dupe/Show S01E01 small.mkv', 10)
big = make('Season 01/dupe/Show S01E01 big.mkv', 999999)
matched, _ = app.scan_local_episodes(show)
check('largest duplicate wins', same(matched[(1, 1)], big), matched[(1, 1)])
check('smaller duplicate discarded', not same(matched[(1, 1)], small))

print('\n=== 4. Path traversal is refused ===')
outside = os.path.join(tmp, 'secret.mkv')
with open(outside, 'wb') as fh:
    fh.write(b'\x00')
cases = [
    ('..\\secret.mkv', 'parent escape'),
    ('../secret.mkv', 'parent escape (posix)'),
    ('Season 01/../../secret.mkv', 'nested escape'),
    (os.path.abspath(outside), 'absolute path'),
]
for rel, label in cases:
    check(f'refuses {label}', app._resolve_episode_file(show, rel) is None, rel)
check('accepts a real episode',
      app._resolve_episode_file(show, 'Season 01/Show - S01E01 - Pilot.mkv') is not None)
make('Season 01/notes.txt')
check('refuses a non-video file inside the show',
      app._resolve_episode_file(show, 'Season 01/notes.txt') is None)
check('refuses a file that does not exist',
      app._resolve_episode_file(show, 'Season 01/nope.mkv') is None)

print('\n=== 5. Playback resolution honours ?episode= ===')
app.app.config['TESTING'] = True
item = {'path': show}
with app.app.test_request_context('/video/1'):
    check('no episode falls back to the largest file',
          same(app._request_video_file(item), big), app._request_video_file(item))
    check('cache key is the bare media id', app._playback_cache_key(1) == '1')

rel = 'Season 02/Show S02E10.mkv'
with app.app.test_request_context(f'/video/1?episode={rel}'):
    check('explicit episode is played',
          os.path.basename(app._request_video_file(item) or '') == 'Show S02E10.mkv')
    key = app._playback_cache_key(1)
    check('cache key differs per episode', key != '1' and key.startswith('1-'), key)
    check('segment suffix carries the episode',
          'episode=' in app._segment_query_suffix(), app._segment_query_suffix())

with app.app.test_request_context('/video/1?episode=..\\secret.mkv'):
    check('an unresolvable episode fails rather than playing something else',
          app._request_video_file(item) is None)

print('\n=== 6. Cache keys are stable and distinct ===')
keys = {}
for rel in ('Season 01/Show - S01E01 - Pilot.mkv', 'Season 02/Show S02E10.mkv'):
    with app.app.test_request_context(f'/video/7?episode={rel}'):
        keys[rel] = app._playback_cache_key(7)
check('two episodes get two cache dirs', len(set(keys.values())) == 2, keys)
with app.app.test_request_context('/video/7?episode=Season 02/Show S02E10.mkv'):
    check('same episode gives the same key repeatedly',
          app._playback_cache_key(7) == keys['Season 02/Show S02E10.mkv'])

print('\n=== 7. Playlist segments carry the episode ===')
with app.app.test_request_context('/x?episode=Season 02/Show S02E10.mkv'):
    pl = app._build_vod_playlist(30.0, segment_query=app._segment_query_suffix())
    seg_lines = [line for line in pl.splitlines() if line.startswith('segment_')]
    check('every segment line has the query',
          all('?episode=' in line for line in seg_lines), seg_lines[:2])
    rewritten = app._rewrite_playlist_segments('#EXTM3U\n#EXTINF:4,\nsegment_00000.ts\n')
    check('ffmpeg playlists are rewritten too', '?episode=' in rewritten, rewritten)
with app.app.test_request_context('/x'):
    pl = app._build_vod_playlist(30.0, segment_query=app._segment_query_suffix())
    check('films get clean segment names',
          all('?' not in line for line in pl.splitlines() if line.startswith('segment_')))
    check('rewrite is a no-op without an episode',
          app._rewrite_playlist_segments('#EXTM3U\nsegment_00000.ts\n') == '#EXTM3U\nsegment_00000.ts\n')

print('\n=== 8. Outside a request context nothing explodes ===')
check('episode reads as empty', app._request_episode() == '')
check('cache key still resolves', app._playback_cache_key(3) == '3')


print('\n=== 9. Featurette labels are readable ===')
label_cases = [
    ('Angel And The Apocalypse (1080p AI Upscale DVD x265 HEVC 10bit AC3 Vertag)_H.264.mkv',
     'Angel And The Apocalypse'),
    ('Agatha Assembled_H.264.mp4', 'Agatha Assembled'),
    ('Ask the Creators Featurette.mkv', 'Ask the Creators Featurette'),
    ('Season 4 Outtakes (1080p x265).mkv', 'Season 4 Outtakes'),
    ('Behind.The.Scenes.1080p.WEB-DL.mkv', 'Behind The Scenes'),
]
for raw, expected in label_cases:
    got = app._featurette_label(raw)
    check(f'{expected!r} from a release filename', got == expected, got)
check('a name that is entirely junk still returns something',
      app._featurette_label('1080p.x265.mkv') != '')
check('extension alone does not empty the label',
      app._featurette_label('.mkv') != '')

print('\n=== 10. Featurettes are scoped by their folder ===')
season_cases = [
    ('Season 4/Prophecies - Season 4 Overview.mkv', 4,
     'folder wins over a season named in the filename'),
    ('Season 1/Angel And The Apocalypse.mkv', 1, 'exact Season folder'),
    ('Season 01/Agatha Assembled.mp4', 1, 'zero padded folder'),
    ('Specials/Thing.mkv', 0, 'Specials is season 0'),
    ('Harley Quinn (2019) Season 3 S03 (1080p)/Featurettes/x.mkv', 3,
     'season embedded in a longer release folder'),
    ('Hawkeye (2021) S01 (1080p)/Featurettes/x.mkv', 1, 'SNN embedded'),
    ('Wentworth (2013) Season 1-9 S01-S09 (1080p)/Featurettes/x.mkv', None,
     'a season RANGE must not resolve to one season'),
    ('Show S01-S05 Complete/Featurettes/x.mkv', None, 'SNN range rejected at both ends'),
    ('Wentworth (2013) Season 1-9 S01-S09 (1080p)/Featurettes/Season 9/x.mkv', 9,
     'deepest folder wins over the range above it'),
    ('x-men-the-animated-series_202204/EP01.mkv', None, 'no season anywhere'),
    ('Featurettes/x.mkv', None, 'show-level Featurettes'),
    ('Season 2/Featurettes/x.mkv', 2, 'nested inside a season'),
]
for rel, expected, why in season_cases:
    got = app._infer_season_from_path(show, os.path.join(show, rel.replace('/', os.sep)))
    check(f'{why} -> {expected}', got == expected, got)

print('\n=== 11. The unmatched endpoint filters by season ===')
os.makedirs(os.path.join(show, 'Season 03'), exist_ok=True)
make('Season 03/A Bonus Feature (1080p x265).mkv')
make('Featurettes/A Show Wide Extra.mkv')
_m, unmatched_all = app.scan_local_episodes(show)
by_season = {}
for path in unmatched_all:
    key = app._infer_season_from_path(show, path)
    by_season.setdefault(key, []).append(path)
check('season 3 featurette scoped to 3', len(by_season.get(3, [])) == 1, by_season.get(3))
check('show-wide extra has no season', any('Show Wide' in p for p in by_season.get(None, [])),
      by_season.get(None))
check('season 3 list excludes the show-wide extra',
      not any('Show Wide' in p for p in by_season.get(3, [])))
print(f'\n{"=" * 60}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
if FAIL:
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
