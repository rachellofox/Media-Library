import os
import threading
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, redirect, render_template, request, url_for

from medialibrary import runtime, tmdb_state
from medialibrary.config import (
    DB_PATH,
    FFPROBE_EXE,
    POSTER_DIR,
)
from medialibrary.network import DEFAULT_SERVER_PORT
from medialibrary.qb_search import (
    QBSearch,
    SearchEngineError,
    configured_mirror_urls,
)
from medialibrary.quality import detect_quality_from_file
from medialibrary.storage import Storage
from medialibrary.trakt_client import TraktRequestError
from medialibrary.web.auth import bp as auth_bp
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


# What the server actually bound at startup. Host and port are only read when
# app.run() is called, so the UI compares these against the saved settings to
# tell the user a restart is needed.
RUNNING_PORT = DEFAULT_SERVER_PORT
RUNNING_PUBLIC = False

QBT_NOVA_PATH = os.environ.get(
    'QBT_NOVA_PATH',
    os.path.expandvars(r'%LOCALAPPDATA%\\qBittorrent\\nova3'),
)
QBT_WEBUI_URL = os.environ.get('QBT_WEBUI_URL', '').strip().rstrip('/')
QBT_WEBUI_USERNAME = os.environ.get('QBT_WEBUI_USERNAME', '').strip()
QBT_WEBUI_PASSWORD = os.environ.get('QBT_WEBUI_PASSWORD', '').strip()


def scan_folder(folder_path: str) -> list[str]:
    """Return sorted names of video files and subdirectories inside folder_path."""
    if not folder_path or not os.path.isdir(folder_path):
        return []
    entries = []
    try:
        for entry in os.scandir(folder_path):
            if entry.is_file():
                _, ext = os.path.splitext(entry.name)
                if ext.lower() in VIDEO_EXTENSIONS:
                    entries.append(entry.name)
            elif entry.is_dir() and not entry.name.startswith('.'):
                entries.append(entry.name + '/')
    except PermissionError:
        return []
    return sorted(entries)


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
    VIDEO_EXTENSIONS,
    _is_local_media_missing,
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
from medialibrary.items import (
    _ui_item_payload,
    _upgrade_available,
)

# Moved to medialibrary.network; imported back so existing callers keep working.
from medialibrary.network import (
    ALL_INTERFACES_HOST,
    DEFAULT_SERVER_PORT,
    LOOPBACK_HOST,
    _lan_ip_addresses,
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
from medialibrary.posters import (
    _cache_meta_poster,
    cache_poster,
)
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


def backfill_genres() -> dict:
    if not tmdb_state.client():
        return {'updated': 0, 'skipped': 0, 'error': 'tmdb_not_configured'}

    updated = 0
    skipped = 0
    for item in store.list_media_items():
        # sqlite3.Row has no __contains__, so `in item` would test the column
        # *values*, not the column names. .keys() is required here.
        has_genre_1 = 'genre_1' in item.keys()  # noqa: SIM118
        current_1 = (item['genre_1'] or '').strip() if has_genre_1 else ''
        # A missing *second* genre is normal - plenty of titles carry only one
        # on TMDB - so only a missing first genre means the item never resolved.
        # Treating a single-genre title as incomplete would re-query TMDB for it
        # on every pass, forever.
        if current_1:
            continue

        try:
            meta = _fetch_best_metadata(item)
            genre_1 = (meta.get('genre_1') or '').strip() or None
            genre_2 = (meta.get('genre_2') or '').strip() or None
            if not genre_1 and not genre_2:
                skipped += 1
                continue
            store.update_metadata(
                media_id=item['id'],
                imdb_id=None,
                tmdb_id=None,
                poster_url=item['poster_url'],
                synopsis=item['synopsis'],
                actors=item['actors'],
                genre_1=genre_1,
                genre_2=genre_2,
                rating=item['rating'],
                title=None,
                media_type=None,
                year=None,
                collection_id=None,
                collection_name=None,
            )
            updated += 1
        except Exception:
            skipped += 1
    return {'updated': updated, 'skipped': skipped}


def _run_genre_backfill_once() -> None:
    """Fill in genres for any item that never resolved one.

    Driven by the data rather than a completion flag: the old flag was set even
    when individual items had failed, which left them permanently stuck with no
    way to retry. Because an item is only a candidate when its first genre is
    missing, this settles at zero candidates and stops calling TMDB by itself.
    """
    if not tmdb_state.client():
        return
    if not any(not (item['genre_1'] or '').strip() for item in store.list_media_items()):
        return
    result = backfill_genres()
    if result.get('error'):
        return
    app.logger.info(
        'Genre backfill: %s updated, %s skipped.', result.get('updated'), result.get('skipped')
    )


@app.route('/')
def index():
    if not store.list_media_items():
        import_from_configured_folders()

    # Auto-clear only when qBittorrent itself reports completion.
    _auto_finalize_qb_completed_downloads()

    all_items = store.list_media_items()

    _active_dl = {'starting', 'handed_off', 'downloading'}
    _missing_by_path: dict[str, bool] = {}

    def with_flags(items):
        out = []
        for i in all_items if items is all_items else items:
            d = dict(i)
            p = d.get('path') or ''
            status = (d.get('download_status') or '').strip().lower()
            normalized_path = p.strip()
            if normalized_path not in _missing_by_path:
                _missing_by_path[normalized_path] = _is_local_media_missing(normalized_path)
            d['file_missing'] = _missing_by_path[normalized_path] and status not in _active_dl
            d['upgrade_available'] = _upgrade_available(d)
            out.append(d)
        return out

    all_flagged = with_flags(all_items)
    local_flagged = [
        i
        for i in all_flagged
        if (i.get('path') or '').strip() or i.get('download_status') in _active_dl
    ]
    movies = [i for i in local_flagged if i['media_type'] == 'movie']
    tv_shows = [i for i in local_flagged if i['media_type'] == 'tv']
    _run_genre_backfill_once()
    favourites = [i for i in all_flagged if i['favourite']]

    movies_path = store.get_setting('movies_path') or ''
    tv_path = store.get_setting('tv_path') or ''
    downloads_path = store.get_setting('downloads_path') or ''
    preferred_quality = store.get_setting('preferred_quality') or '2160p'
    public_access = _public_access_enabled()
    server_port = _server_port()
    auth_username = _auth_username()
    auth_configured = _auth_configured()
    mirror_urls_text = '\n'.join(configured_mirror_urls())
    initial_section = request.args.get('section', 'movies')
    if initial_section not in {'discover', 'movies', 'tv', 'favourites', 'settings'}:
        initial_section = 'movies'

    trakt = _trakt_context()
    return render_template(
        'index.html',
        movies=movies,
        tv_shows=tv_shows,
        favourites=favourites,
        movies_path=movies_path,
        tv_path=tv_path,
        downloads_path=downloads_path,
        public_access=public_access,
        server_port=server_port,
        auth_username=auth_username,
        auth_configured=auth_configured,
        signed_in=_is_signed_in(),
        lan_ips=_lan_ip_addresses(),
        running_port=RUNNING_PORT,
        running_public=RUNNING_PUBLIC,
        movie_files=scan_folder(movies_path),
        tv_files=scan_folder(tv_path),
        tmdb_api_configured=bool(_tmdb_api_key()),
        trakt_configured=trakt['configured'],
        trakt_client_id=trakt['client_id'],
        trakt_username=trakt['username'],
        trakt_saved_username=trakt['saved_username'],
        trakt_oauth_available=trakt['oauth_available'],
        trakt_connected=trakt['connected'],
        trakt_secret_configured=trakt['secret_configured'],
        trakt_profile=trakt['profile'],
        trakt_device_flow=trakt['device_flow'],
        qbt_path=QBT_NOVA_PATH,
        qbt_webui_url=medialibrary.qbt._qbt_webui_url(),
        status=request.args.get('status', ''),
        preferred_quality=preferred_quality,
        mirror_urls_text=mirror_urls_text,
        initial_section=initial_section,
    )


@app.route('/api/search-imdb')
def search_imdb():
    query = request.args.get('q', '').strip()
    results = tmdb_state.client().search(query) if (query and tmdb_state.client()) else []
    library_tmdb_ids = {
        item['tmdb_id'] for item in store.list_media_items() if item['tmdb_id'] is not None
    }
    payload = []
    for result in results:
        item = dict(result)
        item['in_library'] = item.get('tmdb_id') in library_tmdb_ids
        payload.append(item)
    return jsonify({'query': query, 'results': payload})


@app.route('/add', methods=['POST'])
def add():
    tmdb_id = request.form['tmdb_id']
    media_type = request.form.get('media_type') or 'movie'
    current_quality = request.form.get('current_quality') or None
    path = request.form.get('path') or None

    meta = tmdb_state.client().metadata_by_tmdb_id(tmdb_id, media_type)
    if not meta.get('imdb_id'):
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'missing_imdb_id'}), 400
        return redirect(
            url_for(
                'index', section=request.form.get('return_section', 'discover'), status='add_failed'
            )
        )

    store.add_media_item(
        imdb_id=meta['imdb_id'],
        tmdb_id=meta.get('tmdb_id'),
        title=meta['title'],
        year=meta['year'],
        media_type=meta['media_type'],
        collection_id=meta.get('collection_id'),
        collection_name=meta.get('collection_name'),
        current_quality=current_quality,
        path=path,
        poster_url=_cache_meta_poster(meta['imdb_id'], meta),
        synopsis=meta.get('synopsis'),
        actors=meta.get('actors'),
        genre_1=meta.get('genre_1'),
        genre_2=meta.get('genre_2'),
        rating=meta.get('rating'),
        subtitles=scan_subtitles(path),
    )
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify({'ok': True, 'imdb_id': meta['imdb_id'], 'title': meta['title']})
    return redirect(url_for('index', section=request.form.get('return_section', 'movies')))


@app.route('/check/<int:media_id>', methods=['POST'])
def check_quality(media_id: int):
    item = store.get_media_item(media_id)
    if not item:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        return redirect(url_for('index'))

    qb.set_mirror_urls(configured_mirror_urls())
    try:
        outcome = qb.check_for_higher_quality(
            title=item['title'],
            year=item['year'],
            current_quality=item['current_quality'],
            preferred_quality=store.get_setting('preferred_quality') or '2160p',
        )
    except SearchEngineError as exc:
        # Record nothing: a failed search is not evidence that no upgrade
        # exists, and storing it would leave a misleading "checked" timestamp.
        app.logger.warning('Quality check for media %s failed: %s', media_id, exc)
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'search_unavailable', 'message': str(exc)}), 503
        return redirect(url_for('index', status='search_unavailable'))

    best = outcome['best'] if outcome['found'] else None
    store.add_quality_check(
        media_item_id=media_id,
        best_found_quality=(best or {}).get('quality'),
        best_found_name=(best or {}).get('name'),
        best_found_desc_link=(best or {}).get('desc_link'),
        found=outcome['found'],
        raw_result_count=outcome['result_count'],
    )

    if request.headers.get('X-Requested-With') == 'fetch':
        payload = _ui_item_payload(media_id)
        return jsonify({'ok': True, 'item': payload, 'outcome': outcome})

    return redirect(url_for('index'))


@app.route('/refresh/<int:media_id>', methods=['POST'])
def refresh_metadata(media_id: int):
    item = store.get_media_item(media_id)
    if not item:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        return redirect(url_for('index'))
    meta = _fetch_best_metadata(item)
    poster_key = meta.get('imdb_id') or item['imdb_id'] or str(media_id)
    poster_url = cache_poster(
        poster_key, meta.get('poster_url') or '', force_replace=True
    ) or meta.get('poster_url')
    store.update_metadata(
        media_id=media_id,
        imdb_id=meta.get('imdb_id'),
        tmdb_id=meta.get('tmdb_id'),
        poster_url=poster_url,
        synopsis=meta.get('synopsis'),
        actors=meta.get('actors'),
        genre_1=meta.get('genre_1'),
        genre_2=meta.get('genre_2'),
        rating=meta.get('rating'),
        title=meta.get('title') or None,
        media_type=meta.get('media_type') or None,
        year=meta.get('year') or None,
        collection_id=meta.get('collection_id'),
        collection_name=meta.get('collection_name'),
    )
    store.update_subtitles(media_id, scan_subtitles(item['path']))

    if request.headers.get('X-Requested-With') == 'fetch':
        payload = _ui_item_payload(media_id) or {}
        return jsonify({'ok': True, 'item': payload})

    return redirect(url_for('index'))


@app.route('/favourite/<int:media_id>', methods=['POST'])
def toggle_favourite(media_id: int):
    favourite = store.toggle_favourite(media_id)
    if request.headers.get('X-Requested-With') == 'fetch':
        payload = _ui_item_payload(media_id) or {'id': media_id}
        payload['favourite'] = favourite
        return jsonify({'ok': True, 'item': payload})
    return redirect(url_for('index'))


@app.route('/refresh-all', methods=['POST'])
def refresh_all_metadata():
    """Bulk-refresh metadata and posters for every item in the library via TMDB."""
    if not tmdb_state.client():
        return redirect(url_for('index', status='tmdb_not_configured'))
    force_refresh = request.form.get('force') == '1'
    all_items = store.list_media_items()
    refreshed = 0
    for item in all_items:
        try:
            # Skip items that already have complete metadata
            title_ok = (
                item['title']
                and not item['title'].startswith('tt')
                and not item['title'].startswith('nm')
                and item['title'] != '/spotlight/'
            )
            if (
                not force_refresh
                and title_ok
                and item['poster_url']
                and item['synopsis']
                and item['year']
            ):
                continue
            meta = _fetch_best_metadata(item)
            if not meta.get('poster_url') and not meta.get('title'):
                continue
            poster_key = meta.get('imdb_id') or item['imdb_id'] or str(item['id'])
            poster_url = cache_poster(
                poster_key,
                meta.get('poster_url') or '',
                force_replace=force_refresh,
            ) or meta.get('poster_url')
            store.update_metadata(
                media_id=item['id'],
                imdb_id=meta.get('imdb_id'),
                tmdb_id=meta.get('tmdb_id'),
                poster_url=poster_url,
                synopsis=meta.get('synopsis'),
                actors=meta.get('actors'),
                genre_1=meta.get('genre_1'),
                genre_2=meta.get('genre_2'),
                rating=meta.get('rating'),
                title=meta.get('title') or None,
                media_type=meta.get('media_type') or None,
                year=meta.get('year') or None,
                collection_id=meta.get('collection_id'),
                collection_name=meta.get('collection_name'),
            )
            store.update_subtitles(item['id'], scan_subtitles(item['path']))
            refreshed += 1
        except Exception:
            continue
    return redirect(url_for('index', status=f'refreshed_all_{refreshed}'))


@app.route('/sync-library', methods=['POST'])
def sync_library():
    imported = import_from_configured_folders()
    return redirect(url_for('index', status=f'folder_sync_{imported}'))


@app.route('/scan-quality', methods=['POST'])
def scan_quality():
    """Scan each library item's actual video file to detect and store its quality."""
    all_items = store.list_media_items()
    updated = 0
    for item in all_items:
        path = item['path']
        if not path:
            continue
        target = _find_video_file(path)
        if not target:
            continue
        quality = detect_quality_from_file(target, ffprobe_exe=FFPROBE_EXE)
        if quality and quality != item['current_quality']:
            store.update_quality(item['id'], quality)
            updated += 1
    return redirect(url_for('index', status=f'quality_scanned_{updated}'))


@app.route('/scan-subtitles', methods=['POST'])
def scan_subtitles_route():
    """Rescan every library item for English subtitles (embedded or external)."""
    all_items = store.list_media_items()
    updated = 0
    for item in all_items:
        result = scan_subtitles(item['path'])
        store.update_subtitles(item['id'], result)
        if result != item['subtitles']:
            updated += 1
    return redirect(url_for('index', status=f'subtitles_scanned_{updated}'))


@app.route('/check-all', methods=['POST'])
def check_all():
    """Run a quality search for all items, or a specific media_type if supplied."""
    media_type = request.form.get('media_type') or None
    all_items = store.list_media_items()
    items = (
        [i for i in all_items if i['media_type'] == media_type] if media_type else list(all_items)
    )

    qb.set_mirror_urls(configured_mirror_urls())

    checked = 0
    failed = 0
    for item in items:
        try:
            outcome = qb.check_for_higher_quality(
                title=item['title'],
                year=item['year'],
                current_quality=item['current_quality'],
                preferred_quality=store.get_setting('preferred_quality') or '2160p',
            )
            best = outcome['best'] if outcome['found'] else None
            store.add_quality_check(
                media_item_id=item['id'],
                best_found_quality=(best or {}).get('quality'),
                best_found_name=(best or {}).get('name'),
                best_found_desc_link=(best or {}).get('desc_link'),
                found=outcome['found'],
                raw_result_count=outcome['result_count'],
            )
            checked += 1
        except SearchEngineError as exc:
            failed += 1
            if failed == 1:
                app.logger.warning('Quality check failed for "%s": %s', item['title'], exc)
            # Every title uses the same engine, so once it is unreachable the
            # rest will fail identically - stop rather than retry hundreds of
            # times against a dead mirror.
            break
        except Exception as exc:
            failed += 1
            app.logger.warning('Quality check failed for "%s": %s', item['title'], exc)

    if failed and not checked:
        return redirect(url_for('index', status='search_unavailable'))
    if failed:
        return redirect(url_for('index', status=f'quality_checked_partial_{checked}_{failed}'))
    return redirect(url_for('index', status='quality_checked'))


@app.route('/sync-trakt', methods=['POST'])
def sync_trakt():
    trakt = _trakt_client()
    if not trakt:
        return redirect(url_for('index', section='settings', status='trakt_not_configured'))
    synced = 0
    try:
        for item in trakt.collection_movies() + trakt.collection_shows():
            try:
                meta = (
                    tmdb_state.client().metadata_by_imdb_id(item['imdb_id'])
                    if tmdb_state.client()
                    else {}
                )
                store.add_media_item(
                    imdb_id=item['imdb_id'],
                    tmdb_id=meta.get('tmdb_id'),
                    title=meta.get('title') or item['title'],
                    year=meta.get('year') or item['year'],
                    media_type=item['media_type'],
                    collection_id=meta.get('collection_id'),
                    collection_name=meta.get('collection_name'),
                    current_quality=None,
                    path=None,
                    poster_url=_cache_meta_poster(item['imdb_id'], meta),
                    synopsis=meta.get('synopsis'),
                    actors=meta.get('actors'),
                    genre_1=meta.get('genre_1'),
                    genre_2=meta.get('genre_2'),
                    rating=meta.get('rating'),
                )
                synced += 1
            except Exception:
                continue
    except TraktRequestError as exc:
        status = 'trakt_error'
        if exc.code == 'unauthorized':
            status = 'trakt_error_unauthorized'
        if exc.code == 'forbidden':
            status = 'trakt_error_forbidden'
        elif exc.code == 'not_found':
            status = 'trakt_error_not_found'
        elif exc.code == 'network_error':
            status = 'trakt_error_network'
        return redirect(url_for('index', section='settings', status=status))
    except Exception:
        return redirect(url_for('index', section='settings', status='trakt_error'))

    return redirect(url_for('index', section='settings', status=f'trakt_synced_{synced}'))


# Per-media lock guards segment-endpoint restarts so concurrent hls.js requests cooperate.


def _startup_library_sync():
    # Runs once in the background after startup so new files are picked up
    # without requiring a manual sync-library trigger.
    try:
        imported = import_from_configured_folders()
        if imported:
            app.logger.info('Startup library sync: %d new item(s) imported.', imported)

        # Also heal items that were imported before their downloads completed.
        scanned = _scan_missing_quality_items()
        if scanned:
            app.logger.info('Startup quality scan: %d missing-quality item(s) updated.', scanned)
    except Exception as exc:
        app.logger.warning('Startup library sync failed: %s', exc)


def _scan_missing_quality_items() -> int:
    """Detect local quality for items that currently have no stored quality."""
    updated = 0
    for item in store.list_media_items():
        if str(item.get('current_quality') or '').strip():
            continue
        path = item.get('path')
        if not path:
            continue

        target = _find_video_file(path)
        if not target:
            continue

        quality = detect_quality_from_file(target, ffprobe_exe=FFPROBE_EXE)
        if not quality:
            continue

        store.update_quality(item['id'], quality)
        updated += 1
    return updated


if __name__ == '__main__':
    t = threading.Thread(target=_startup_library_sync, daemon=True, name='startup-library-sync')
    t.start()

    RUNNING_PUBLIC = _public_access_enabled()
    RUNNING_PORT = _server_port()
    bind_host = ALL_INTERFACES_HOST if RUNNING_PUBLIC else LOOPBACK_HOST

    # WARNING: the Werkzeug debugger exposes an interactive console that can run
    # arbitrary code, so it must never be reachable from another machine. The
    # app has no authentication of its own either — see the Public Access
    # section in Settings.
    debug_enabled = not RUNNING_PUBLIC

    if RUNNING_PUBLIC:
        app.logger.warning(
            'Public access is ON: listening on %s:%s with no authentication. '
            'Anyone who can reach this port can browse, stream and control downloads.',
            bind_host,
            RUNNING_PORT,
        )

    app.run(debug=debug_enabled, host=bind_host, port=RUNNING_PORT)
