"""Sign-in and sign-out.

Only reachable when public access is on; the mechanics are in medialibrary.auth.
The endpoint is named `login` because AUTH_EXEMPT_ENDPOINTS names it — the page
you sign in on cannot itself require signing in.
"""

import secrets
from datetime import datetime, timezone

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from medialibrary.auth import (
    _auth_configured,
    _auth_password_hash,
    _auth_username,
    _client_address,
    _failed_logins,
    _is_signed_in,
    _login_locked_until,
    _record_failed_login,
    _safe_next_target,
)

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if not _auth_configured():
        return redirect(url_for('core.index'))
    if _is_signed_in():
        return redirect(_safe_next_target(request.args.get('next')))

    address = _client_address()
    locked_until = _login_locked_until(address)
    if locked_until:
        wait_seconds = int((locked_until - datetime.now(timezone.utc)).total_seconds())
        return render_template(
            'login.html',
            error=f'Too many attempts. Try again in {wait_seconds // 60 + 1} minute(s).',
            next_target=request.args.get('next', ''),
        ), 429

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        username_ok = secrets.compare_digest(username, _auth_username())
        password_ok = check_password_hash(_auth_password_hash(), password)

        if username_ok and password_ok:
            _failed_logins.pop(address, None)
            # Start a clean session so nothing from the signed-out state carries over.
            session.clear()
            session['auth_user'] = _auth_username()
            session.permanent = bool(request.form.get('stay_signed_in'))
            return redirect(_safe_next_target(request.form.get('next')))

        _record_failed_login(address)
        current_app.logger.warning('Failed sign-in for %r from %s', username, address)
        return render_template(
            'login.html',
            error='Incorrect username or password.',
            next_target=request.form.get('next', ''),
        ), 401

    return render_template('login.html', error=None, next_target=request.args.get('next', ''))


@bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
