"""A file from another show must never be indexed as this show's episode."""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from medialibrary.episode_match import names_other_show

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


print('\n=== 1. The reported case is refused ===')
check(
    'Chernobyl file in the Parks folder',
    names_other_show('Chernobyl.S01E01.1080p.WEB-DL.mkv', 'Parks and Recreation') is True,
)
check(
    'the real Parks episode is kept',
    names_other_show('Parks and Recreation - S01E01 - Pilot.mkv', 'Parks and Recreation') is False,
)

print('\n=== 2. Abbreviated and noisy release names are kept ===')
for filename, title in [
    ('Parks.and.Rec.S01E01.720p.mkv', 'Parks and Recreation'),
    ('Parks_and_Recreation_S01E01.mkv', 'Parks and Recreation'),
    ('parks.and.recreation.s01e01.internal.mkv', 'Parks and Recreation'),
    ('Mr.Robot.S02E01-E02.mkv', 'Mr. Robot'),
    ('X-Men.S01E05.mkv', 'X-Men The Animated Series'),
    ('Prison.Break.2005.S04E01.mkv', 'Prison Break'),
    ('The Office (US) - S03E01.mkv', 'The Office'),
]:
    check(f'{filename[:38]!r} under {title[:22]!r}', names_other_show(filename, title) is False)

print('\n=== 3. Bare and marker-first names are kept ===')
for filename in ['S01E01.mkv', 's01e01.mkv', 'S01E01 - Pilot.mkv', '- S01E01.mkv']:
    check(
        f'{filename!r} has no show name to disagree with',
        names_other_show(filename, 'Parks and Recreation') is False,
    )

print('\n=== 4. Other real intruders are refused ===')
for filename, title in [
    ('Breaking.Bad.S02E03.mkv', 'Better Call Saul'),
    ('The.Wire.S01E01.mkv', 'The Sopranos'),
]:
    check(f'{filename[:30]!r} under {title[:20]!r}', names_other_show(filename, title) is True)

print('\n=== 4b. A file with no marker is never judged here ===')
for filename, title in [
    ('EP01 - Night of the Sentinels.mkv', 'X-Men The Animated Series'),
    ('Night of the Sentinels.mkv', 'X-Men The Animated Series'),
    ('Campaign Ads.mkv', 'Parks and Recreation'),
    ('Agatha Assembled_H.264.mp4', 'Agatha All Along'),
]:
    check(
        f'title-only {filename[:32]!r} is not refused', names_other_show(filename, title) is False
    )

print('\n=== 5. Nothing is refused when the title is unknown ===')
check('no show title means no opinion', names_other_show('Chernobyl.S01E01.mkv', '') is False)

print('\n=== 6. End to end: the scan reports it as unmatched, not as an episode ===')
from medialibrary.identify import scan_local_episodes

with tempfile.TemporaryDirectory() as root:
    show = os.path.join(root, 'Parks and Recreation')
    season = os.path.join(show, 'Season 01')
    os.makedirs(season)
    real = os.path.join(season, 'Parks and Recreation - S01E01 - Pilot.mkv')
    stray = os.path.join(season, 'Chernobyl.S01E01.1080p.mkv')
    with open(real, 'wb') as fh:
        fh.write(b'x' * 1000)
    # Deliberately the larger file: largest-wins is what made it win before.
    with open(stray, 'wb') as fh:
        fh.write(b'x' * 50000)

    matched, unmatched = scan_local_episodes(show, 'Parks and Recreation')
    check(
        'S01E01 resolves to the real Pilot even though the stray is bigger',
        matched.get((1, 1)) == real,
        matched.get((1, 1)),
    )
    check('the stray is reported as unmatched', stray in unmatched, unmatched)
    check('the stray claimed no episode number', stray not in matched.values(), matched)

    # And with the title left out, the folder name is used instead.
    matched, _u = scan_local_episodes(show)
    check(
        'folder name works as the fallback title', matched.get((1, 1)) == real, matched.get((1, 1))
    )

print(f'\n{"=" * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
for f in FAIL:
    print('  -', f)
sys.exit(1 if FAIL else 0)
