"""Sign-in: credentials, sessions, and the lockout.

Only required when public access is on — local-only use needs no sign-in, and
demanding one would be friction for nothing. Passwords are stored hashed, the
signing key lives in the OS keyring so sessions survive a restart, and repeated
failures from one address lock out for five minutes.

Failed attempts are counted in memory rather than the database on purpose: a
restart clearing the counters costs an attacker more time than it saves them.
"""

import secrets
from datetime import datetime, timedelta, timezone

import keyring
from flask import request, session, url_for

from medialibrary import runtime
from medialibrary.network import _public_access_enabled

AUTH_SECRET_SERVICE = 'MediaLibrary'

AUTH_SECRET_ACCOUNT = 'session_secret_key'

SESSION_LIFETIME_DAYS = 30

AUTH_MAX_ATTEMPTS = 5

AUTH_LOCKOUT = timedelta(minutes=5)


def _session_secret_key() -> str:
    """Signing key for session cookies, stable across restarts.

    Kept in the OS keyring like the other secrets. A regenerated key would
    silently sign everyone out, so it is created once and reused.
    """
    try:
        stored = keyring.get_password(AUTH_SECRET_SERVICE, AUTH_SECRET_ACCOUNT)
    except Exception:
        stored = None
    if stored:
        return stored

    generated = secrets.token_hex(32)
    try:
        keyring.set_password(AUTH_SECRET_SERVICE, AUTH_SECRET_ACCOUNT, generated)
    except Exception:
        # Without a keyring the key lives only for this process, so sessions
        # end on restart rather than failing outright.
        pass
    return generated


def _auth_username() -> str:
    return (runtime.store().get_setting('auth_username') or '').strip()


def _auth_password_hash() -> str:
    return (runtime.store().get_setting('auth_password_hash') or '').strip()


def _auth_configured() -> bool:
    return bool(_auth_username() and _auth_password_hash())


def _auth_required() -> bool:
    """Whether the current request must carry a signed-in session.

    Only enforced once the server is exposed to the network; a loopback-only
    server is already limited to whoever is sitting at this machine.
    """
    return _public_access_enabled() and _auth_configured()


def _is_signed_in() -> bool:
    return session.get('auth_user') == _auth_username() and _auth_configured()


# Failed sign-ins per client address. In-memory is enough: a restart clearing
# the counters costs an attacker more time than it saves them.
_failed_logins: dict[str, tuple[int, datetime]] = {}


def _client_address() -> str:
    return request.remote_addr or 'unknown'


def _login_locked_until(address: str) -> datetime | None:
    attempts, last_failure = _failed_logins.get(address, (0, None))
    if attempts < AUTH_MAX_ATTEMPTS or last_failure is None:
        return None
    unlock_at = last_failure + AUTH_LOCKOUT
    return unlock_at if unlock_at > datetime.now(timezone.utc) else None


def _record_failed_login(address: str) -> None:
    attempts, _ = _failed_logins.get(address, (0, None))
    _failed_logins[address] = (attempts + 1, datetime.now(timezone.utc))


def _safe_next_target(raw: str | None) -> str:
    """Only allow same-site relative paths, so ?next= cannot bounce elsewhere."""
    target = (raw or '').strip()
    if not target.startswith('/') or target.startswith('//'):
        return url_for('core.index')
    return target
