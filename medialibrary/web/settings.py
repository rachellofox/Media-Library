"""Settings routes: credentials, folders, public access, and the connection tests.

The tests matter as much as the settings. "Reachable, 0 torrents" was once
reported for a qBittorrent that could not be contacted at all, which is the same
bug as a search failure looking like an empty result — so each test here
distinguishes "answered, and here is the answer" from "could not ask".

Everything is validated before anything is written: a bad port used to be
rejected only after the rest of the form had already been saved.
"""

import re
from datetime import timedelta

from flask import Blueprint, jsonify, redirect, request, url_for
from werkzeug.security import generate_password_hash

from medialibrary import qbt, runtime
from medialibrary.auth import _auth_password_hash, _auth_username
from medialibrary.importer import import_from_configured_folders
from medialibrary.qb_search import configured_mirror_urls
from medialibrary.qbt import _extract_btih_hash, _sanitize_qbt_webui_url
from medialibrary.settings_util import _load_json_setting, _save_json_setting, _utc_now
from medialibrary.tmdb_client import TmdbClient
from medialibrary.tmdb_state import _refresh_tmdb_client, _set_tmdb_api_key, _tmdb_api_key
from medialibrary.trakt_auth import (
    TRAKT_DEVICE_SETTING,
    TRAKT_PROFILE_SETTING,
    TRAKT_TOKEN_SETTING,
    _clear_trakt_auth,
    _serialize_trakt_token,
    _set_trakt_client_secret,
    _trakt_client_id,
    _trakt_client_secret,
    _trakt_device_flow,
)
from medialibrary.trakt_client import TraktClient, TraktRequestError

bp = Blueprint('settings', __name__)


@bp.route('/settings', methods=['POST'])
def save_settings():
    prev_movies = (runtime.store().get_setting('movies_path') or '').strip()
    prev_tv = (runtime.store().get_setting('tv_path') or '').strip()
    prev_trakt_client_id = _trakt_client_id()
    prev_trakt_secret = _trakt_client_secret()

    movies_path = request.form.get('movies_path')
    tv_path = request.form.get('tv_path')
    downloads_path = request.form.get('downloads_path')
    preferred_quality = request.form.get('preferred_quality')
    public_access_submitted = request.form.get('public_access_submitted')
    server_port_raw = request.form.get('server_port')
    auth_username_raw = request.form.get('auth_username')
    auth_password_raw = request.form.get('auth_password')
    auth_password_confirm = request.form.get('auth_password_confirm')
    mirror_urls_raw = request.form.get('mirror_urls')
    qbt_webui_url_raw = request.form.get('qbt_webui_url')
    tmdb_api_key_raw = request.form.get('tmdb_api_key')
    trakt_client_id_raw = request.form.get('trakt_client_id')
    trakt_username_raw = request.form.get('trakt_username')
    trakt_client_secret_raw = request.form.get('trakt_client_secret')

    if movies_path is not None:
        runtime.store().set_setting('movies_path', movies_path.strip())
    if tv_path is not None:
        runtime.store().set_setting('tv_path', tv_path.strip())
    if downloads_path is not None:
        runtime.store().set_setting('downloads_path', downloads_path.strip())
    if preferred_quality is not None:
        runtime.store().set_setting('preferred_quality', preferred_quality.strip() or '2160p')

    # An unticked checkbox submits nothing, so a companion hidden field marks
    # that this particular form was the one posted.
    if public_access_submitted:
        wants_public = bool(request.form.get('public_access'))

        port = None
        if server_port_raw is not None:
            try:
                port = int(server_port_raw.strip())
            except ValueError:
                port = -1
            if not 1024 <= port <= 65535:
                # Everything is validated before anything is written, so a
                # rejected form never leaves half the settings applied.
                return redirect(url_for('core.index', section='settings', status='port_invalid'))

        username = (auth_username_raw or '').strip()
        password = auth_password_raw or ''
        new_password_hash = None
        if password or auth_password_confirm:
            if password != (auth_password_confirm or ''):
                return redirect(
                    url_for('core.index', section='settings', status='password_mismatch')
                )
            if len(password) < 8:
                return redirect(
                    url_for('core.index', section='settings', status='password_too_short')
                )
            new_password_hash = generate_password_hash(password)

        will_have_username = username or _auth_username()
        will_have_hash = new_password_hash or _auth_password_hash()
        if wants_public and not (will_have_username and will_have_hash):
            # Refusing here is the whole point of the feature: exposing the
            # library to the network with no credentials set has no safe path.
            return redirect(
                url_for('core.index', section='settings', status='auth_required_for_public')
            )

        if username:
            runtime.store().set_setting('auth_username', username)
        if new_password_hash:
            runtime.store().set_setting('auth_password_hash', new_password_hash)
        runtime.store().set_setting('public_access', '1' if wants_public else '0')
        if port is not None:
            runtime.store().set_setting('server_port', str(port))
        return redirect(url_for('core.index', section='settings', status='public_access_saved'))
    if mirror_urls_raw is not None:
        mirror_urls = [
            line.strip().rstrip('/') for line in mirror_urls_raw.splitlines() if line.strip()
        ]
        runtime.store().set_setting('mirror_urls', '\n'.join(mirror_urls))

    if qbt_webui_url_raw is not None:
        sanitized_url = _sanitize_qbt_webui_url(qbt_webui_url_raw)
        if sanitized_url is None:
            return redirect(url_for('core.index', section='settings', status='qbt_url_invalid'))
        runtime.store().set_setting('qbt_webui_url', sanitized_url)
    if tmdb_api_key_raw is not None and tmdb_api_key_raw.strip():
        _set_tmdb_api_key(tmdb_api_key_raw)
        _refresh_tmdb_client()
    if trakt_client_id_raw is not None:
        runtime.store().set_setting('trakt_client_id', trakt_client_id_raw.strip())
    if trakt_username_raw is not None:
        runtime.store().set_setting('trakt_username', trakt_username_raw.strip())
    if trakt_client_secret_raw is not None and trakt_client_secret_raw.strip():
        _set_trakt_client_secret(trakt_client_secret_raw)

    changed_client_id = (
        trakt_client_id_raw is not None and trakt_client_id_raw.strip() != prev_trakt_client_id
    )
    changed_client_secret = (
        trakt_client_secret_raw is not None
        and trakt_client_secret_raw.strip()
        and trakt_client_secret_raw.strip() != prev_trakt_secret
    )
    if changed_client_id or changed_client_secret:
        _clear_trakt_auth()

    runtime.qb().set_mirror_urls(configured_mirror_urls())
    new_movies = (runtime.store().get_setting('movies_path') or '').strip()
    new_tv = (runtime.store().get_setting('tv_path') or '').strip()
    if new_movies != prev_movies or new_tv != prev_tv:
        imported = import_from_configured_folders()
        return redirect(
            url_for('core.index', section='settings', status=f'settings_saved_{imported}')
        )
    return redirect(url_for('core.index', section='settings', status='settings_saved'))


@bp.route('/api/settings/qbt-test')
def settings_qbt_test():
    if not qbt._qbt_webui_url():
        return jsonify({'ok': False, 'error': 'qbt_webui_not_configured'}), 409
    try:
        torrents = qbt._qbt_webui_torrents_info()
    except Exception:
        return jsonify({'ok': False, 'error': 'qbt_connection_failed'}), 502
    return jsonify({'ok': True, 'reachable': True, 'torrents_seen': len(torrents)})


@bp.route('/api/settings/tmdb-test', methods=['POST'])
def settings_tmdb_test():
    api_key = (request.form.get('tmdb_api_key') or '').strip() or _tmdb_api_key()
    if not api_key:
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 409
    try:
        client = TmdbClient(api_key=api_key)
        payload = client.ping() or {}
    except Exception:
        return jsonify({'ok': False, 'error': 'tmdb_connection_failed'}), 502
    return jsonify(
        {
            'ok': True,
            'reachable': True,
            'has_images_config': bool((payload.get('images') or {}).get('base_url')),
        }
    )


@bp.route('/api/settings/trakt-test', methods=['POST'])
def settings_trakt_test():
    client_id = (request.form.get('trakt_client_id') or '').strip() or _trakt_client_id()
    client_secret = (
        request.form.get('trakt_client_secret') or ''
    ).strip() or _trakt_client_secret()
    if not client_id or not client_secret:
        return jsonify({'ok': False, 'error': 'trakt_oauth_not_configured'}), 409
    try:
        trakt = TraktClient(client_id=client_id, client_secret=client_secret)
        flow = trakt.device_code()
        device_code = (flow or {}).get('device_code', '')
        if not device_code:
            raise TraktRequestError('http_error')
        try:
            trakt.poll_device_token(device_code)
        except TraktRequestError as exc:
            if exc.code not in {'pending', 'slow_down'}:
                raise
    except TraktRequestError as exc:
        error = 'trakt_error_network'
        if exc.code in {'forbidden', 'unauthorized', 'http_error'}:
            error = 'trakt_error_forbidden'
        return jsonify({'ok': False, 'error': error}), 502
    return jsonify(
        {
            'ok': True,
            'reachable': True,
            'verification_url': flow.get('verification_url', 'https://trakt.tv/activate'),
        }
    )


@bp.route('/api/trakt/connect/start', methods=['POST'])
def trakt_connect_start():
    client_id = _trakt_client_id()
    client_secret = _trakt_client_secret()
    if not client_id or not client_secret:
        return jsonify({'ok': False, 'error': 'trakt_oauth_not_configured'}), 400
    try:
        trakt = TraktClient(client_id=client_id, client_secret=client_secret)
        flow = trakt.device_code()
        expires_at = _utc_now() + timedelta(seconds=int(flow.get('expires_in') or 0))
        payload = {
            'device_code': flow.get('device_code', ''),
            'user_code': flow.get('user_code', ''),
            'verification_url': flow.get('verification_url', 'https://trakt.tv/activate'),
            'interval': int(flow.get('interval') or 5),
            'expires_at': expires_at.isoformat(),
        }
        _save_json_setting(TRAKT_DEVICE_SETTING, payload)
        return jsonify({'ok': True, 'flow': payload})
    except TraktRequestError as exc:
        error = 'trakt_error_forbidden' if exc.code == 'forbidden' else 'trakt_error_network'
        return jsonify({'ok': False, 'error': error}), 502


@bp.route('/api/trakt/connect/poll', methods=['POST'])
def trakt_connect_poll():
    flow = _trakt_device_flow()
    if not flow or not flow.get('device_code'):
        return jsonify({'ok': False, 'error': 'trakt_oauth_missing_flow'}), 400
    client_id = _trakt_client_id()
    client_secret = _trakt_client_secret()
    try:
        trakt = TraktClient(client_id=client_id, client_secret=client_secret)
        token = _serialize_trakt_token(trakt.poll_device_token(flow['device_code']))
        authed = TraktClient(
            client_id=client_id,
            client_secret=client_secret,
            access_token=token.get('access_token', ''),
        )
        profile_data = authed.current_user() or {}
        profile = {
            'username': profile_data.get('username', ''),
            'slug': (profile_data.get('ids') or {}).get('slug') or profile_data.get('username', ''),
            'name': profile_data.get('name', ''),
        }
        _save_json_setting(TRAKT_TOKEN_SETTING, token)
        _save_json_setting(TRAKT_PROFILE_SETTING, profile)
        _save_json_setting(TRAKT_DEVICE_SETTING, None)
        return jsonify({'ok': True, 'status': 'connected', 'profile': profile})
    except TraktRequestError as exc:
        if exc.code in {'pending', 'slow_down'}:
            return jsonify(
                {
                    'ok': True,
                    'status': 'pending',
                    'interval': int(flow.get('interval') or 5)
                    + (5 if exc.code == 'slow_down' else 0),
                }
            )
        if exc.code in {'expired', 'denied', 'already_used', 'not_found'}:
            _save_json_setting(TRAKT_DEVICE_SETTING, None)
            return jsonify({'ok': False, 'error': f'trakt_oauth_{exc.code}'}), 400
        return jsonify({'ok': False, 'error': f'trakt_{exc.code}'}), 502


@bp.route('/api/trakt/disconnect', methods=['POST'])
def trakt_disconnect():
    token = _load_json_setting(TRAKT_TOKEN_SETTING) or {}
    access_token = token.get('access_token', '')
    client_id = _trakt_client_id()
    client_secret = _trakt_client_secret()
    if access_token and client_id and client_secret:
        try:
            TraktClient(client_id=client_id, client_secret=client_secret).revoke_token(access_token)
        except Exception:
            pass
    _clear_trakt_auth()
    return jsonify({'ok': True})


@bp.route('/api/debug/qbt-status')
def debug_qbt_status():
    if not qbt._qbt_webui_enabled():
        return jsonify({'ok': False, 'error': 'qbt_webui_not_configured'}), 409

    info_hash = (request.args.get('hash') or '').strip().upper()
    magnet = (request.args.get('magnet') or '').strip()

    if not info_hash and magnet:
        info_hash = _extract_btih_hash(magnet) or ''
    if not re.fullmatch(r'[0-9A-F]{40}', info_hash):
        return jsonify({'ok': False, 'error': 'missing_or_invalid_hash'}), 400

    try:
        torrent = qbt._qbt_webui_torrent_info(info_hash)
    except Exception:
        return jsonify({'ok': False, 'error': 'qbt_query_failed'}), 502

    if not torrent:
        return jsonify({'ok': True, 'found': False, 'hash': info_hash})

    progress_raw = torrent.get('progress')
    try:
        progress = float(progress_raw)
    except Exception:
        progress = 0.0

    return jsonify(
        {
            'ok': True,
            'found': True,
            'hash': info_hash,
            'name': torrent.get('name') or '',
            'state': torrent.get('state') or '',
            'progress': progress,
            'progress_percent': round(progress * 100, 2),
            'eta': torrent.get('eta'),
            'save_path': torrent.get('save_path') or '',
            'dlspeed': torrent.get('dlspeed'),
            'upspeed': torrent.get('upspeed'),
            'num_seeds': torrent.get('num_seeds'),
            'num_leechs': torrent.get('num_leechs'),
        }
    )
