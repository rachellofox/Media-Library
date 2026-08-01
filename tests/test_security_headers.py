"""Security headers must be present, and must not break what the app needs.

A Content-Security-Policy is easy to add and easy to get wrong in a way nobody
notices until a page half-works. So this asserts both directions: the headers are
there, and every source the app actually loads from is allowed.
"""

import os
import re
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


app._auth_required = lambda: False
client = app.app.test_client()
response = client.get('/')
csp = response.headers.get('Content-Security-Policy', '')

print('\n=== the headers are present ===')
for header, expected in (
    ('X-Content-Type-Options', 'nosniff'),
    ('Referrer-Policy', 'same-origin'),
    ('X-Frame-Options', 'DENY'),
):
    check(
        f'{header} is {expected}',
        response.headers.get(header) == expected,
        response.headers.get(header),
    )
check('Content-Security-Policy is set', bool(csp))

print('\n=== the policy closes what it should ===')
for directive in (
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "default-src 'self'",
):
    check(f'{directive}', directive in csp, csp)

print('\n=== and does not block what the app loads ===')
# hls.js is fetched from a CDN chain; if the policy misses one, playback falls
# back silently to a worse strategy rather than failing loudly.
with open(os.path.join(REPO_ROOT, 'static', 'js', 'player.js'), encoding='utf-8') as fh:
    player_js = fh.read()
cdns = sorted(
    {m.group(0) for m in re.finditer(r'https://[a-z0-9.-]+(?=/[^\'"]*hls[^\'"]*\.js)', player_js)}
)
check('the player loads hls.js from at least one CDN', bool(cdns), cdns)
for host in cdns:
    check(f'script-src allows {host}', host in csp, csp)

# The artwork picker renders TMDB thumbnails directly.
with open(os.path.join(REPO_ROOT, 'static', 'js', 'library.js'), encoding='utf-8') as fh:
    library_js = fh.read()
if 'thumb_url' in library_js:
    check('img-src allows TMDB images', 'https://image.tmdb.org' in csp, csp)

# The markup carries inline handlers and style attributes, and the pages start
# with an inline bootstrap. Until those go, the policy has to permit inline.
page = response.get_data(as_text=True)
if re.search(r'on(?:click|error|change)\s*=', page) or '<script>' in page:
    check(
        'script-src permits inline, which the inline handlers still require',
        "'unsafe-inline'" in csp.split('script-src')[1].split(';')[0],
        csp,
    )

print(f'\n{"=" * 62}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
for name in FAIL:
    print('  -', name)
sys.exit(1 if FAIL else 0)
