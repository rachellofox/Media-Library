"""The scripts and tests reach into `app`; those attributes have to exist.

Nothing else covers the maintenance scripts — they act on a real library, so
they cannot be run in the suite — which means a name they rely on can be removed
from under them and nobody finds out until someone runs one. That happened:
DISCOVER_COLLECTION_CACHE_HOURS moved into medialibrary.config, ruff removed the
now-unused import from app.py, and _plan_tv_naming.py broke silently.

This is a static check, so it costs nothing and needs no library.
"""

import ast
import glob
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


print('\n=== every app.X the scripts and tests use exists ===')
missing = []
sources = sorted(glob.glob(os.path.join(REPO_ROOT, 'scripts', '*.py')))
sources += sorted(glob.glob(os.path.join(REPO_ROOT, 'tests', '*.py')))
for path in sources:
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == 'app'
            and not hasattr(app, node.attr)
        ):
            missing.append(f'{os.path.relpath(path, REPO_ROOT)}:{node.lineno} app.{node.attr}')
check('no script or test reaches for a name app does not have', not missing, missing)

print('\n=== the package holds the modules, and the root does not ===')
root_modules = {
    os.path.basename(p)
    for p in glob.glob(os.path.join(REPO_ROOT, '*.py'))
    if os.path.basename(p) != 'app.py'
}
check('app.py is the only Python file at the repo root', not root_modules, sorted(root_modules))

expected = {
    'config.py',
    'discover.py',
    'downloads.py',
    'episode_match.py',
    'identify.py',
    'naming.py',
    'playback.py',
    'qb_search.py',
    'qbt.py',
    'quality.py',
    'storage.py',
    'subtitle_client.py',
    'subtitles.py',
    'tmdb_client.py',
    'trakt_client.py',
}
present = {
    os.path.basename(p)
    for p in glob.glob(os.path.join(REPO_ROOT, 'medialibrary', '*.py'))
    if not os.path.basename(p).startswith('__')
}
check('every expected module is in medialibrary/', expected <= present, sorted(expected - present))

print('\n=== medialibrary never imports the application ===')
offenders = []
for path in sorted(glob.glob(os.path.join(REPO_ROOT, 'medialibrary', '*.py'))):
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split('.')[0] == 'app' for a in node.names):
            offenders.append(f'{os.path.basename(path)}:{node.lineno}')
        if isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] == 'app':
            offenders.append(f'{os.path.basename(path)}:{node.lineno}')
check('no module in medialibrary imports app', not offenders, offenders)

print(f'\n{"=" * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
for name in FAIL:
    print('  -', name)
sys.exit(1 if FAIL else 0)
