"""Auth tests against a throwaway DB. Never touches the real library.db."""
import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import app
from storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'{"PASS" if cond else "FAIL"}  {name}{("  -- " + str(detail)) if detail and not cond else ""}')


tmp = tempfile.mkdtemp()
app.store = Storage(os.path.join(tmp, 'auth.db'))
app.store.initialize()
app.app.config['TESTING'] = True
app._failed_logins.clear()


def client():
    return app.app.test_client()


def set_state(public, username=None, password=None):
    app.store.set_setting('public_access', '1' if public else '0')
    if username is not None:
        app.store.set_setting('auth_username', username)
    if password is not None:
        from werkzeug.security import generate_password_hash
        app.store.set_setting('auth_password_hash', generate_password_hash(password))


print('\n=== 1. Public access OFF: no login required ===')
set_state(False, 'rachel', 'correct-horse')
c = client()
check('library reachable without signing in', c.get('/?section=movies').status_code == 200)
check('auth not enforced', app._auth_required() is False)

print('\n=== 2. Public access ON: everything is gated ===')
set_state(True, 'rachel', 'correct-horse')
c = client()
r = c.get('/?section=movies')
check('page redirects to login', r.status_code == 302 and '/login' in r.headers.get('Location', ''), r.headers.get('Location'))
check('api returns 401 JSON, not a redirect',
      c.get('/api/library/retry-download/1').status_code == 401)
check('fetch-style request returns 401',
      c.post('/check/1', headers={'X-Requested-With': 'fetch'}).status_code == 401)
check('login page itself is reachable', c.get('/login').status_code == 200)

print('\n=== 3. Media streaming is protected (the actual content) ===')
for path in ('/stream', '/api/video/1/strategy', '/api/video/compatibility-report'):
    r = c.get(path)
    check(f'{path} is gated', r.status_code in (302, 401), r.status_code)

print('\n=== 4. Wrong credentials rejected ===')
app._failed_logins.clear()
c = client()
r = c.post('/login', data={'username': 'rachel', 'password': 'wrong'})
check('bad password rejected', r.status_code == 401)
check('still signed out', c.get('/?section=movies').status_code == 302)
r = c.post('/login', data={'username': 'nobody', 'password': 'correct-horse'})
check('bad username rejected', r.status_code == 401)

print('\n=== 5. Correct credentials sign in ===')
app._failed_logins.clear()
c = client()
r = c.post('/login', data={'username': 'rachel', 'password': 'correct-horse'})
check('redirects after sign-in', r.status_code == 302)
check('library now reachable', c.get('/?section=movies').status_code == 200)
check('api now reachable', c.get('/api/library/retry-download/1').status_code != 401)

print('\n=== 6. Sign out ends the session ===')
c.post('/logout')
check('signed out again', c.get('/?section=movies').status_code == 302)

print('\n=== 7. Brute-force lockout ===')
app._failed_logins.clear()
c = client()
codes = [c.post('/login', data={'username': 'rachel', 'password': 'nope'}).status_code
         for _ in range(app.AUTH_MAX_ATTEMPTS)]
check(f'first {app.AUTH_MAX_ATTEMPTS} attempts return 401', all(x == 401 for x in codes), codes)
r = c.post('/login', data={'username': 'rachel', 'password': 'nope'})
check('further attempts locked out (429)', r.status_code == 429, r.status_code)
r = c.post('/login', data={'username': 'rachel', 'password': 'correct-horse'})
check('correct password ALSO locked out while cooling down', r.status_code == 429, r.status_code)

print('\n=== 8. Open-redirect protection on ?next= ===')
with app.app.test_request_context():  # _safe_next_target calls url_for
    check('absolute url rejected', app._safe_next_target('https://evil.example/x') == '/')
    check('protocol-relative rejected', app._safe_next_target('//evil.example/x') == '/')
    check('backslash trick rejected', app._safe_next_target('/\\evil.example') == '/\\evil.example')
    check('relative path allowed', app._safe_next_target('/?section=tv') == '/?section=tv')
    check('empty falls back to index', app._safe_next_target('') == '/')

# End to end: a crafted next must not send the browser off-site after sign-in.
app._failed_logins.clear()
set_state(True, 'rachel', 'correct-horse')
c = client()
r = c.post('/login', data={'username': 'rachel', 'password': 'correct-horse',
                           'next': 'https://evil.example/steal'})
check('sign-in ignores an off-site next', r.headers.get('Location', '').endswith('/'),
      r.headers.get('Location'))

print('\n=== 9. Public access cannot be enabled without credentials ===')
app.store.set_setting('auth_username', '')
app.store.set_setting('auth_password_hash', '')
app.store.set_setting('public_access', '0')
c = client()
r = c.post('/settings', data={'public_access_submitted': '1', 'public_access': '1', 'server_port': '5100'})
check('rejected with auth_required_for_public',
      'auth_required_for_public' in r.headers.get('Location', ''), r.headers.get('Location'))
check('public access stayed OFF', app._public_access_enabled() is False)

print('\n=== 10. Password rules ===')
c = client()
r = c.post('/settings', data={'public_access_submitted': '1', 'auth_username': 'rachel',
                              'auth_password': 'short', 'auth_password_confirm': 'short',
                              'server_port': '5100'})
check('too-short password rejected', 'password_too_short' in r.headers.get('Location', ''))
check('nothing saved', not app._auth_password_hash())
r = c.post('/settings', data={'public_access_submitted': '1', 'auth_username': 'rachel',
                              'auth_password': 'longenough1', 'auth_password_confirm': 'different1',
                              'server_port': '5100'})
check('mismatched passwords rejected', 'password_mismatch' in r.headers.get('Location', ''))
check('still nothing saved', not app._auth_password_hash())
r = c.post('/settings', data={'public_access_submitted': '1', 'public_access': '1',
                              'auth_username': 'rachel', 'auth_password': 'longenough1',
                              'auth_password_confirm': 'longenough1', 'server_port': '5100'})
check('valid credentials + public access accepted',
      'public_access_saved' in r.headers.get('Location', ''), r.headers.get('Location'))
check('public access now ON', app._public_access_enabled() is True)
check('password stored hashed, not plaintext',
      app._auth_password_hash() and 'longenough1' not in app._auth_password_hash())

print('\n=== 11. Enabling public access immediately gates the next request ===')
# Section 10 just turned public access on, so this client is no longer trusted.
r = c.get('/?section=settings')
check('settings now require signing in', r.status_code == 302 and '/login' in r.headers.get('Location', ''))
r = c.post('/settings', data={'public_access_submitted': '1', 'auth_username': 'hacker'})
check('settings POST is gated too', r.status_code == 302 and '/login' in r.headers.get('Location', ''))
check('username not changed by the gated POST', app._auth_username() == 'rachel')

print('\n=== 12. Changing username without a password keeps the old one ===')
app._failed_logins.clear()
c.post('/login', data={'username': 'rachel', 'password': 'longenough1'})
check('signed in', c.get('/?section=settings').status_code == 200)
old_hash = app._auth_password_hash()
c.post('/settings', data={'public_access_submitted': '1', 'public_access': '1',
                          'auth_username': 'newname', 'auth_password': '',
                          'auth_password_confirm': '', 'server_port': '5100'})
check('username updated', app._auth_username() == 'newname', app._auth_username())
check('password hash unchanged', app._auth_password_hash() == old_hash)

print('\n=== 13. Renaming the user invalidates the old session ===')
check('old session no longer valid after username change',
      c.get('/?section=settings').status_code == 302)
app._failed_logins.clear()
c.post('/login', data={'username': 'newname', 'password': 'longenough1'})
check('new username signs in', c.get('/?section=settings').status_code == 200)

print(f'\n{"=" * 60}\nPASSED {len(PASS)}   FAILED {len(FAIL)}')
if FAIL:
    print('Failures:')
    for f in FAIL:
        print('  -', f)
sys.exit(1 if FAIL else 0)
