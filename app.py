import os
import threading
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, redirect, request, url_for

from medialibrary import network, runtime, tmdb_state
from medialibrary.config import (
    DB_PATH,
    POSTER_DIR,
)
from medialibrary.qb_search import (
    QBSearch,
)
from medialibrary.storage import Storage
from medialibrary.web.auth import bp as auth_bp
from medialibrary.web.core import bp as core_bp
from medialibrary.web.discover import bp as discover_bp
from medialibrary.web.library import bp as library_bp
from medialibrary.web.settings import bp as settings_bp
from medialibrary.web.tv import bp as tv_bp
from medialibrary.web.video import bp as video_bp

try:
    from send2trash import send2trash
except ImportError:  # pragma: no cover - optional until requirements installed
    send2trash = None


__version__ = '0.1.0'


# Paths and tool locations come from medialibrary.config so the modules split
# out of this file do not have to import the application to find them. Re-bound
# here because much of app.py, the scripts and the tests refer to them by these
# names.
POSTERS_DIR = POSTER_DIR


QBT_NOVA_PATH = os.environ.get(
    'QBT_NOVA_PATH',
    os.path.expandvars(r'%LOCALAPPDATA%\\qBittorrent\\nova3'),
)
QBT_WEBUI_URL = os.environ.get('QBT_WEBUI_URL', '').strip().rstrip('/')
QBT_WEBUI_USERNAME = os.environ.get('QBT_WEBUI_USERNAME', '').strip()
QBT_WEBUI_PASSWORD = os.environ.get('QBT_WEBUI_PASSWORD', '').strip()


# Playback lives in medialibrary.playback. The names are imported rather than
# reached through the module so that every existing caller — including the
# `app.X` references in the maintenance scripts and tests — keeps working. Some
# are re-exported for those callers rather than used here, hence the F401.
# Moved to medialibrary.identify; imported by name so every existing
# caller, including app.X in the scripts and tests, keeps working.
# Moved to medialibrary.qbt; imported by name so every existing
# caller, including app.X in the scripts and tests, keeps working. The module
# itself is imported too, so its configure() can be called once the store exists.
import medialibrary.discover
import medialibrary.downloads
import medialibrary.qbt

# Moved to medialibrary.auth; imported back so existing callers keep working.
from medialibrary.auth import (  # noqa: F401
    AUTH_LOCKOUT,
    AUTH_MAX_ATTEMPTS,
    AUTH_SECRET_ACCOUNT,
    AUTH_SECRET_SERVICE,
    SESSION_LIFETIME_DAYS,
    _auth_configured,
    _auth_password_hash,
    _auth_required,
    _auth_username,
    _client_address,
    _failed_logins,
    _is_signed_in,
    _login_locked_until,
    _record_failed_login,
    _safe_next_target,
    _session_secret_key,
)

# Moved to medialibrary.discover; imported by name so every existing
# caller, including app.X in the scripts and tests, keeps working.
from medialibrary.discover import (  # noqa: F401
    _TMDB_SEASON_CACHE,
    TMDB_SEASON_CACHE_TTL,
    _cached_season_episodes,
    _cached_tv_status,
    _discover_incomplete_collections,
    _discover_missing_episodes,
    _discover_watchlist,
    _missing_episodes_for_show,
)

# Moved to medialibrary.downloads; imported by name so every existing
# caller, including app.X in the scripts and tests, keeps working.
from medialibrary.downloads import (  # noqa: F401
    _FINALIZE_LOCK,
    _auto_finalize_qb_completed_downloads,
    _finalize_completed_download,
    _finalize_tv_episode,
    _is_download_state_stale,
    _library_root_for,
    _place_video_in_library,
    _readopt_orphaned_downloads,
    _refresh_local_media_signals,
    _retire_path,
    _staging_path_for,
)
from medialibrary.identify import (
    _resolve_episode_file,  # noqa: F401
)

# Moved to medialibrary.importer; imported back so existing callers keep working.
from medialibrary.importer import (  # noqa: F401
    _fetch_best_metadata,
    import_from_configured_folders,
    import_media_from_paths,
    scan_media_entries,
)

# Moved to medialibrary.identify_extra; imported back so existing callers keep working.
# Moved to medialibrary.items; imported back so existing callers keep working.
# Moved to medialibrary.maintenance; imported back so existing callers keep working.
from medialibrary.maintenance import (  # noqa: F401
    _run_genre_backfill_once,
    _scan_missing_quality_items,
    _startup_library_sync,
    backfill_genres,
    scan_folder,
)

# Moved to medialibrary.network; imported back so existing callers keep working.
from medialibrary.network import (
    ALL_INTERFACES_HOST,
    LOOPBACK_HOST,
    _public_access_enabled,
    _server_port,
)
from medialibrary.playback import (  # noqa: F401
    _DIRECT_PLAY_AUDIO_CODECS,
    _DIRECT_PLAY_VIDEO_CODECS,
    _HLS_SEGMENT_LENGTH,
    _SEGMENT_FILE_RE,
    _build_vod_playlist,
    _cleanup_finished_direct_stream_job,
    _cleanup_finished_transcode_job,
    _cleanup_hls_cache,
    _direct_play_issues,
    _direct_stream_jobs,
    _get_video_info,
    _get_video_mime_type,
    _highest_completed_segment,
    _is_hls_complete,
    _playback_cache_key,
    _request_episode,
    _rewrite_playlist_segments,
    _segment_lengths_for,
    _segment_query_suffix,
    _start_direct_stream,
    _start_hls_transcode,
    _start_job_kill_timer,
    _stop_hls_transcode_job,
    _stream_file,
    _stream_file_chunk,
    _transcode_jobs,
)

# Moved to medialibrary.posters; imported back so existing callers keep working.
from medialibrary.qbt import (  # noqa: F401
    QBT_DONE_STATES,
    QbtUnavailableError,
    _extract_btih_hash,
    _norm_match_text,
    _qbt_match_torrent_for_item,
    _qbt_webui_add_download,
    _qbt_webui_build_opener,
    _qbt_webui_enabled,
    _qbt_webui_open,
    _qbt_webui_torrent_info,
    _qbt_webui_torrents_info,
    _qbt_webui_try_login,
    _qbt_webui_url,
    _qbt_webui_username,
    _torrent_is_complete,
)

# Moved to medialibrary.settings_util; imported back so existing callers keep working.
from medialibrary.settings_util import (  # noqa: F401
    _load_json_setting,
    _parse_iso_datetime,
    _save_json_setting,
    _utc_now,
)

# Moved to medialibrary.subtitles; imported by name so every existing
# caller, including app.X in the scripts and tests, keeps working.
from medialibrary.subtitles import (  # noqa: F401
    _ENGLISH_TAGS,
    SUBTITLE_EXTENSIONS,
    _extract_embedded_subtitle_to_vtt,
    _ffprobe_embedded_english,
    _ffprobe_has_embedded_subtitles,
    _find_subtitle_files,
    _find_video_file,
    _has_english_tag,
    _probe_embedded_subtitles,
    _srt_is_english,
    _srt_to_vtt,
    _subtitle_cache,
    scan_subtitles,
)

# Moved to medialibrary.tmdb_state; imported back so existing callers keep working.
from medialibrary.tmdb_state import (  # noqa: F401
    TMDB_SECRET_ACCOUNT,
    TMDB_SECRET_SERVICE,
    _refresh_tmdb_client,
    _set_tmdb_api_key,
    _tmdb_api_key,
)

# Moved to medialibrary.torrents; imported back so existing callers keep working.
# Moved to medialibrary.trakt_auth; imported back so existing callers keep working.
from medialibrary.trakt_auth import (  # noqa: F401
    TRAKT_DEVICE_SETTING,
    TRAKT_PROFILE_SETTING,
    TRAKT_SECRET_ACCOUNT,
    TRAKT_SECRET_SERVICE,
    TRAKT_TOKEN_SETTING,
    _clear_trakt_auth,
    _connected_trakt_profile,
    _refresh_trakt_token_if_needed,
    _serialize_trakt_token,
    _set_trakt_client_secret,
    _trakt_client,
    _trakt_client_id,
    _trakt_client_secret,
    _trakt_context,
    _trakt_device_flow,
    _trakt_display_username,
    _trakt_username,
)

app = Flask(__name__)
store = Storage(DB_PATH)
# Schema creation belongs here rather than under __main__: anything that imports
# this module — a WSGI server, the maintenance scripts, the tests — otherwise
# meets a database with no tables and fails on the first query. The statements
# are all CREATE TABLE IF NOT EXISTS, so running them every import costs nothing.
store.initialize()
# medialibrary.qbt reads its URL and username from settings, but must not import
# this module to get at the store — that would be circular. It is handed a getter
# instead, once the store exists.
#
# The lambda matters: passing `store.get_setting` directly would bind the store
# object that exists right now, and anything that later rebinds `store` — the
# tests swap in a throwaway database — would be ignored, leaving this module
# reading the real settings while the rest of the app reads the test ones.
medialibrary.qbt.configure(lambda key: store.get_setting(key))

# Same reasoning for Discover, and the point is sharper here: `tmdb` is rebuilt
# by _refresh_tmdb_client() whenever the API key changes, so handing over the
# client itself would leave this module holding a stale one.
medialibrary.discover.configure(
    get_store=lambda: store,
    get_tmdb=tmdb_state.client,
    get_trakt_client=_trakt_client,
)

# Finalisation moves and deletes files, so it gets the same treatment: the store
# by getter, and the app's logger so its decisions stay in the one log.
medialibrary.downloads.configure(
    get_store=lambda: store,
    get_logger=lambda: app.logger,
)

# One place the route modules can reach the long-lived objects without importing
# this one. Getters for the same reason as above: tmdb is rebuilt when the API
# key changes and the tests replace the store.
runtime.configure(
    store=lambda: store,
    tmdb=tmdb_state.client,
    qb=lambda: qb,
)
app.secret_key = _session_secret_key()
app.permanent_session_lifetime = timedelta(days=SESSION_LIFETIME_DAYS)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # Secure is deliberately not set: this is served over plain HTTP on a LAN,
    # and the flag would stop the cookie being sent at all.
)
_refresh_tmdb_client()
qb = QBSearch(nova_path=QBT_NOVA_PATH)

# Registered after the singletons exist, since runtime.configure() above is
# what the blueprint reads them through.
app.register_blueprint(video_bp)
app.register_blueprint(tv_bp)
app.register_blueprint(discover_bp)
app.register_blueprint(library_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(core_bp)

# Clean up old HLS cache on startup
_cleanup_hls_cache()


# Blueprint endpoints are qualified, so this is 'auth.login' not 'login'.
AUTH_EXEMPT_ENDPOINTS = {'auth.login', 'static'}


def _wants_json_response() -> bool:
    return (
        request.path.startswith('/api/')
        or request.headers.get('X-Requested-With') == 'fetch'
        or 'application/json' in (request.headers.get('Accept') or '')
    )


@app.before_request
def _require_login():
    if not _auth_required() or request.endpoint in AUTH_EXEMPT_ENDPOINTS:
        return None
    if _is_signed_in():
        return None
    if _wants_json_response():
        return jsonify({'ok': False, 'error': 'auth_required'}), 401
    return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))


# Per-media lock guards segment-endpoint restarts so concurrent hls.js requests cooperate.


if __name__ == '__main__':
    t = threading.Thread(target=_startup_library_sync, daemon=True, name='startup-library-sync')
    t.start()

    running_public = _public_access_enabled()
    running_port = _server_port()
    network.set_running(running_port, running_public)
    bind_host = ALL_INTERFACES_HOST if network.RUNNING_PUBLIC else LOOPBACK_HOST

    # WARNING: the Werkzeug debugger exposes an interactive console that can run
    # arbitrary code, so it must never be reachable from another machine. The
    # app has no authentication of its own either — see the Public Access
    # section in Settings.
    debug_enabled = not network.RUNNING_PUBLIC

    if network.RUNNING_PUBLIC:
        app.logger.warning(
            'Public access is ON: listening on %s:%s with no authentication. '
            'Anyone who can reach this port can browse, stream and control downloads.',
            bind_host,
            network.RUNNING_PORT,
        )

    app.run(debug=debug_enabled, host=bind_host, port=network.RUNNING_PORT)
