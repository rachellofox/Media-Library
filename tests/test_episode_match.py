"""Identifying episodes by title. Pure logic — no filesystem, no network."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from episode_match import (
    is_extras_path,
    match_episode_files,
    match_episode_title,
    normalise_episode_title,
)

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}{("  -- " + str(detail)) if detail and not cond else ""}')


def ep(season, number, title):
    return {'season_number': season, 'episode_number': number, 'title': title}


print('\n=== 1. Normalising titles ===')
cases = [
    ('EP01 - Night of the Sentinels.mkv', 'night of the sentinels'),
    ('Night of the Sentinels Pt. 2.mkv', 'night of the sentinels (2)'),
    ('Something (Part 3).mkv', 'something (3)'),
    ('Story, Part II The Subtitle.mkv', 'story (2) the subtitle'),
    ('Story Part Two.mkv', 'story (2)'),
    ('Beyond Good and Evil (1)', 'beyond good and evil (1)'),
    ('Ep 5 - Title.mkv', 'title'),
]
for raw, expected in cases:
    got = normalise_episode_title(raw)
    check(f'{raw[:34]!r} -> {expected!r}', got == expected, got)
check('a part word is required, so "I Am Legend" keeps its I',
      normalise_episode_title('I Am Legend.mkv') == 'i am legend',
      normalise_episode_title('I Am Legend.mkv'))

print('\n=== 2. Exact and unambiguous matches ===')
episodes = [ep(1, 3, 'Enter Magneto'), ep(1, 1, 'Night of the Sentinels (1)'),
            ep(1, 2, 'Night of the Sentinels (2)')]
got, reason = match_episode_title('EP03 - Enter Magneto.mkv', episodes)
check('exact title match', got and got['episode_number'] == 3 and reason == 'exact', reason)
got, reason = match_episode_title('EP02 - Night of the Sentinels Pt. 2.mkv', episodes)
check('Pt. 2 resolves to part (2)', got and got['episode_number'] == 2, (got, reason))

print('\n=== 3. Ambiguity is refused, never guessed ===')
got, reason = match_episode_title('EP01 - Night of the Sentinels.mkv', episodes)
check('a bare title matching two parts is refused', got is None and reason == 'ambiguous', (got, reason))
same_title = [ep(1, 5, 'Reunion'), ep(2, 7, 'Reunion')]
got, reason = match_episode_title('Reunion.mkv', same_title)
check('two episodes sharing a title are refused', got is None and reason == 'ambiguous', reason)
got, reason = match_episode_title('Completely Unrelated Thing.mkv', episodes)
check('no plausible match reports no-match', got is None and reason == 'no-match', reason)

print('\n=== 4. Reordered subtitles (token match) ===')
saga = [ep(3, 3, 'The Phoenix Saga: Sacrifice (1)'), ep(3, 4, 'The Phoenix Saga: The Dark Shroud (2)')]
got, reason = match_episode_title('EP29 - The Phoenix Saga, Part I Sacrifice.mkv', saga)
check('same words, different order', got and got['episode_number'] == 3, (got, reason))
check('reported as a token match', reason == 'tokens', reason)
got, _r = match_episode_title('EP30 - The Phoenix Saga, Part II The Dark Shroud.mkv', saga)
check('part 2 goes to part 2', got and got['episode_number'] == 4, got)

print('\n=== 5. A release adding its own subtitle (prefix match) ===')
beyond = [ep(4, 8, 'Beyond Good and Evil (1)'), ep(4, 9, 'Beyond Good and Evil (2)')]
got, reason = match_episode_title('EP63 - Beyond Good and Evil (Part 1) The End of Time.mkv', beyond)
check('TMDB title as a leading phrase', got and got['episode_number'] == 8, (got, reason))
check('reported as a prefix match', reason == 'prefix', reason)
got, _r = match_episode_title('EP64 - Beyond Good and Evil (Part 2) Promise.mkv', beyond)
check('the part number keeps (1) and (2) apart', got and got['episode_number'] == 9, got)
short = [ep(1, 1, 'Reunion')]
got, reason = match_episode_title('Reunion Special Behind The Scenes.mkv', short)
check('a short title is not used as a prefix', got is None, (got, reason))

print('\n=== 6. Resolution by elimination ===')
files = ['EP01 - Night of the Sentinels.mkv', 'EP02 - Night of the Sentinels Pt. 2.mkv']
resolved = match_episode_files(files, episodes)
check('both files identified', len(resolved) == 2, resolved)
check('part 2 from its own marker', resolved[files[1]]['episode_number'] == 2)
check('part 1 deduced once part 2 is claimed', resolved[files[0]]['episode_number'] == 1)
check('no episode assigned twice',
      len({(e['season_number'], e['episode_number']) for e in resolved.values()}) == 2)

print('\n=== 7. Duplicate formats share one episode ===')
dupes = ['EP03 - Enter Magneto.mkv', 'EP03 - Enter Magneto.mp4']
resolved = match_episode_files(dupes, episodes)
check('both copies identified', len(resolved) == 2, resolved)
check('both point at the same episode',
      resolved[dupes[0]]['episode_number'] == resolved[dupes[1]]['episode_number'] == 3)

print('\n=== 8. Elimination cannot invent an answer ===')
three_parts = [ep(1, 1, 'Story (1)'), ep(1, 2, 'Story (2)'), ep(1, 3, 'Story (3)')]
resolved = match_episode_files(['Story.mkv'], three_parts)
check('one bare title against three unclaimed parts stays unresolved',
      'Story.mkv' not in resolved, resolved)
resolved = match_episode_files(['Story.mkv', 'Story Pt. 2.mkv', 'Story Pt. 3.mkv'], three_parts)
check('but resolves once the others are claimed',
      resolved.get('Story.mkv', {}).get('episode_number') == 1, resolved.get('Story.mkv'))

print('\n=== 9. Bonus material is never episode-matched ===')
for path, expected in [
    ('Featurettes\\Season 4\\Campaign Ads.mkv', True),
    ('Featurettes/Specials/Thing.mkv', True),
    ('Extras\\Thing.mkv', True),
    ('Behind The Scenes\\Thing.mkv', True),
    ('Deleted Scenes\\Thing.mkv', True),
    ('Season 04\\Show - S04E12 - Campaign Ad.mkv', False),
    ('Season 4\\Episode.mkv', False),
]:
    check(f'{path[:38]!r} extras={expected}', is_extras_path(path) is expected, is_extras_path(path))

print(f'\n{"=" * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
if FAIL:
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
