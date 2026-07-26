"""Detecting which episode numbering a download actually uses."""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PLAN = os.path.join(REPO_ROOT, 'scripts', '_plan_tv_naming.py')
PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


# The script runs its whole plan at import, so pull the two functions out of the
# source rather than importing it.
import app

with open(PLAN, encoding='utf-8') as handle:
    source = handle.read()
start = source.index('DURATION_TOLERANCE = 0.5')
end = source.index("SUBTITLE_EXTENSIONS = {'.srt'")
from episode_match import normalise_episode_title

namespace = {
    'app': app,
    'os': __import__('os'),
    'json': __import__('json'),
    'subprocess': __import__('subprocess'),
    'normalise_episode_title': normalise_episode_title,
}
exec(compile(source[start:end], 'ordering', 'exec'), namespace)
resolve, score = namespace['_resolve_ordering'], namespace['_score_ordering']


def ep(season, number, title, runtime):
    return {'season_number': season, 'episode_number': number, 'title': title, 'runtime': runtime}


# TMDB's default Firefly: broadcast order, the 87-minute pilot last at E11.
BROADCAST = [
    ep(1, 1, 'The Train Job', 43),
    ep(1, 2, 'Bushwhacked', 44),
    ep(1, 3, 'Our Mrs. Reynolds', 44),
    ep(1, 11, 'Serenity', 87),
]
# The DVD ordering: the pilot first, everything else shifted along.
DVD = [
    ep(1, 1, 'Serenity', 87),
    ep(1, 2, 'The Train Job', 43),
    ep(1, 3, 'Bushwhacked', 44),
    ep(1, 6, 'Our Mrs. Reynolds', 44),
]

# The files on disk, numbered the way the DVD release numbers them.
PLANNED = {'e01.mkv': [(1, 1)], 'e02.mkv': [(1, 2)], 'e03.mkv': [(1, 3)]}
PROBES = {'e01.mkv': (86.7, ''), 'e02.mkv': (42.7, ''), 'e03.mkv': (43.9, '')}

print('\n=== 1. Scoring an ordering against the files ===')
_t, _c, agree, disagree = score(PLANNED, BROADCAST, PROBES)
check('the default cannot explain the 87-minute first file', disagree >= 1, (agree, disagree))
_t, _c, agree, disagree = score(PLANNED, DVD, PROBES)
check('the DVD ordering explains all three', (agree, disagree) == (3, 0), (agree, disagree))

print('\n=== 2. A two-parter is measured against both its episodes ===')
two_part = {'e01e02.mkv': [(1, 1), (1, 2)]}
episodes = [ep(1, 1, 'London', 22), ep(1, 2, 'London (2)', 22)]
_t, _c, agree, disagree = score(two_part, episodes, {'e01e02.mkv': (43.1, '')})
check('43 minutes of a 22+22 two-parter agrees', (agree, disagree) == (1, 0), (agree, disagree))

print('\n=== 3. The right ordering is adopted ===')
namespace['_probe'] = lambda path: PROBES.get(path, (0.0, ''))


class FakeTmdb:
    def __init__(self, orderings):
        self.orderings = orderings

    def episode_orderings(self, _tv_id):
        return self.orderings


namespace['app'] = type(
    'A',
    (),
    {
        'tmdb': FakeTmdb(
            [
                {'id': 'x', 'name': 'Intended Order', 'kind': 'Absolute', 'episodes': DVD},
                {'id': 'y', 'name': 'DVD Order', 'kind': 'DVD', 'episodes': DVD},
            ]
        )
    },
)()

notes, left = [], []
chosen, untitled = resolve('Firefly', 1437, PLANNED, BROADCAST, notes, left)
check('an ordering was adopted', chosen is not BROADCAST)
check('no season had its titles withheld', untitled == set(), untitled)
check(
    'E01 is now Serenity, not The Train Job',
    next(e['title'] for e in chosen if e['episode_number'] == 1) == 'Serenity',
)
check(
    'DVD wins the tie over Absolute, so the choice is repeatable',
    notes and notes[0][1] == 'DVD Order',
    notes,
)

print('\n=== 3b. The title inside the file decides where runtimes cannot ===')
# Batman's shape: every episode runs 22 minutes, so duration tells the orderings
# apart not at all, and only the embedded title distinguishes them.
flat_default = [ep(1, 1, 'The Cat and the Claw (1)', 22), ep(1, 2, 'On Leather Wings', 22)]
flat_dvd = [ep(1, 1, 'On Leather Wings', 22), ep(1, 2, 'Christmas with the Joker', 22)]
flat_planned = {'a.mp4': [(1, 1)], 'b.mp4': [(1, 2)]}
flat_probes = {'a.mp4': (22.0, 'On Leather Wings'), 'b.mp4': (22.0, 'Christmas with the Joker')}
titles, _c, _a, disagree = score(flat_planned, flat_default, flat_probes)
check('the default matches no embedded title', (titles, disagree) == (0, 0), (titles, disagree))
titles, _c, _a, disagree = score(flat_planned, flat_dvd, flat_probes)
check('the DVD ordering matches both', (titles, disagree) == (2, 0), (titles, disagree))

namespace['_probe'] = lambda path: flat_probes.get(path, (0.0, ''))
namespace['app'] = type(
    'A',
    (),
    {'tmdb': FakeTmdb([{'id': 'y', 'name': 'DVD Order', 'kind': 'DVD', 'episodes': flat_dvd}])},
)()
notes, left = [], []
chosen, untitled = resolve('Batman', 2098, flat_planned, flat_default, notes, left)
check('the ordering the files vouch for is adopted', chosen is flat_dvd)
check(
    'and the reason given is the embedded titles',
    notes and 'carry the episode title' in notes[0][2],
    notes,
)

print('\n=== 3c. A container title that is not an episode title stays silent ===')
# Real examples from the library. Read as disagreement these condemned correct
# orderings and proposed 545 needless renames.
for junk in [
    'WENTWORTH Series 2 Disc 2',
    'S01D01title_t02',
    'movieddl.me_Dollhouse.S01E01.1080p.Bluray',
    'Mr Robot S01E01 hellofriend.mov',
]:
    titles, _c, _a, disagree = score(
        {'x.mkv': [(1, 1)]}, [ep(1, 1, 'Ghost', 50)], {'x.mkv': (50.0, junk)}
    )
    check(
        f'{junk[:34]!r} neither matches nor contradicts',
        (titles, disagree) == (0, 0),
        (titles, disagree),
    )

print('\n=== 4. The default is kept when it already fits ===')
namespace['_probe'] = lambda path: {'a.mkv': (43.0, '')}.get(path, (0.0, ''))
notes, left = [], []
chosen, untitled = resolve('Show', 1, {'a.mkv': [(1, 1)]}, BROADCAST, notes, left)
check('nothing is swapped in', chosen is BROADCAST)
check('and nothing is reported', notes == [] and left == [], (notes, left))

print('\n=== 5. Where nothing fits, titles are withheld rather than guessed ===')
namespace['app'] = type('A', (), {'tmdb': FakeTmdb([])})()
namespace['_probe'] = lambda path: (21.6, '')
notes, left = [], []
chosen, untitled = resolve(
    'Parks', 8592, {'s06e20.mkv': [(6, 20)]}, [ep(6, 20, 'Moving Up', 44)], notes, left
)
check('the season is marked untitled', untitled == {6}, untitled)
check('reported as left alone, not as a detected ordering', left and not notes, (notes, left))

print(f'\n{"=" * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
for f in FAIL:
    print('  -', f)
sys.exit(1 if FAIL else 0)
