"""The library page itself, and the actions on it.

Rendering the grid, adding a title, and the sweeps you can trigger by hand —
refresh metadata, scan quality, fetch subtitles, sync. Everything here either
renders the page or acts on the library as a whole; anything scoped to one
existing item lives in web/library.py.
"""

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from medialibrary import network, qbt, runtime, tmdb_state
from medialibrary.auth import _auth_configured, _auth_username, _is_signed_in
from medialibrary.config import FFPROBE_EXE, QBT_NOVA_PATH
from medialibrary.downloads import _auto_finalize_qb_completed_downloads
from medialibrary.identify import _is_local_media_missing
from medialibrary.importer import _fetch_best_metadata, import_from_configured_folders
from medialibrary.items import _ui_item_payload, _upgrade_available
from medialibrary.maintenance import _run_genre_backfill_once, scan_folder
from medialibrary.network import (
    _lan_ip_addresses,
    _public_access_enabled,
    _server_port,
)
from medialibrary.posters import _cache_meta_poster, cache_poster
from medialibrary.qb_search import (
    SearchEngineError,
    configured_main_url,
    configured_mirror_urls,
    configured_search_urls,
)
from medialibrary.quality import detect_quality_from_file
from medialibrary.subtitles import _find_video_file, scan_subtitles
from medialibrary.tmdb_state import _tmdb_api_key
from medialibrary.trakt_auth import _trakt_client, _trakt_context
from medialibrary.trakt_client import TraktRequestError

bp = Blueprint('core', __name__)


@bp.route('/')
def index():
    if not runtime.store().list_media_items():
        import_from_configured_folders()

    # Auto-clear only when qBittorrent itself reports completion.
    _auto_finalize_qb_completed_downloads()

    all_items = runtime.store().list_media_items()

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

    movies_path = runtime.store().get_setting('movies_path') or ''
    tv_path = runtime.store().get_setting('tv_path') or ''
    downloads_path = runtime.store().get_setting('downloads_path') or ''
    preferred_quality = runtime.store().get_setting('preferred_quality') or '2160p'
    public_access = _public_access_enabled()
    server_port = _server_port()
    auth_username = _auth_username()
    auth_configured = _auth_configured()
    mirror_urls_text = '\n'.join(configured_mirror_urls())
    main_url = configured_main_url()
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
        running_port=network.RUNNING_PORT,
        running_public=network.RUNNING_PUBLIC,
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
        qbt_webui_url=qbt._qbt_webui_url(),
        status=request.args.get('status', ''),
        preferred_quality=preferred_quality,
        mirror_urls_text=mirror_urls_text,
        main_url=main_url,
        initial_section=initial_section,
    )


@bp.route('/api/search-imdb')
def search_imdb():
    query = request.args.get('q', '').strip()
    results = tmdb_state.client().search(query) if (query and tmdb_state.client()) else []
    library_tmdb_ids = {
        item['tmdb_id']
        for item in runtime.store().list_media_items()
        if item['tmdb_id'] is not None
    }
    payload = []
    for result in results:
        item = dict(result)
        item['in_library'] = item.get('tmdb_id') in library_tmdb_ids
        payload.append(item)
    return jsonify({'query': query, 'results': payload})


@bp.route('/add', methods=['POST'])
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

    runtime.store().add_media_item(
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
    return redirect(url_for('core.index', section=request.form.get('return_section', 'movies')))


@bp.route('/check/<int:media_id>', methods=['POST'])
def check_quality(media_id: int):
    item = runtime.store().get_media_item(media_id)
    if not item:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        return redirect(url_for('core.index'))

    # A title-and-year search finds one release for one file, which is a
    # movie's shape, not a show's — see _upgrade_available for why the badge
    # that would normally lead here is never offered for TV in the first
    # place. Rejected explicitly rather than run a search that means nothing,
    # for whatever reaches this route despite that (a stale client, a direct
    # call).
    if (item['media_type'] or 'movie') == 'tv':
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'not_a_movie'}), 400
        return redirect(url_for('core.index'))

    runtime.qb().set_mirror_urls(configured_search_urls())
    try:
        outcome = runtime.qb().check_for_higher_quality(
            title=item['title'],
            year=item['year'],
            current_quality=item['current_quality'],
            preferred_quality=runtime.store().get_setting('preferred_quality') or '2160p',
        )
    except SearchEngineError as exc:
        # Record nothing: a failed search is not evidence that no upgrade
        # exists, and storing it would leave a misleading "checked" timestamp.
        current_app.logger.warning('Quality check for media %s failed: %s', media_id, exc)
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'search_unavailable', 'message': str(exc)}), 503
        return redirect(url_for('core.index', status='search_unavailable'))

    best = outcome['best'] if outcome['found'] else None
    runtime.store().add_quality_check(
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

    return redirect(url_for('core.index'))


@bp.route('/refresh/<int:media_id>', methods=['POST'])
def refresh_metadata(media_id: int):
    item = runtime.store().get_media_item(media_id)
    if not item:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        return redirect(url_for('core.index'))
    meta = _fetch_best_metadata(item)
    poster_key = meta.get('imdb_id') or item['imdb_id'] or str(media_id)
    poster_url = cache_poster(
        poster_key, meta.get('poster_url') or '', force_replace=True
    ) or meta.get('poster_url')
    runtime.store().update_metadata(
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
    runtime.store().update_subtitles(media_id, scan_subtitles(item['path']))

    if request.headers.get('X-Requested-With') == 'fetch':
        payload = _ui_item_payload(media_id) or {}
        return jsonify({'ok': True, 'item': payload})

    return redirect(url_for('core.index'))


@bp.route('/favourite/<int:media_id>', methods=['POST'])
def toggle_favourite(media_id: int):
    favourite = runtime.store().toggle_favourite(media_id)
    if request.headers.get('X-Requested-With') == 'fetch':
        payload = _ui_item_payload(media_id) or {'id': media_id}
        payload['favourite'] = favourite
        return jsonify({'ok': True, 'item': payload})
    return redirect(url_for('core.index'))


@bp.route('/refresh-all', methods=['POST'])
def refresh_all_metadata():
    """Bulk-refresh metadata and posters for every item in the library via TMDB."""
    if not tmdb_state.client():
        return redirect(url_for('core.index', status='tmdb_not_configured'))
    force_refresh = request.form.get('force') == '1'
    all_items = runtime.store().list_media_items()
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
            runtime.store().update_metadata(
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
            runtime.store().update_subtitles(item['id'], scan_subtitles(item['path']))
            refreshed += 1
        except Exception:
            continue
    return redirect(url_for('core.index', status=f'refreshed_all_{refreshed}'))


@bp.route('/sync-library', methods=['POST'])
def sync_library():
    imported = import_from_configured_folders()
    return redirect(url_for('core.index', status=f'folder_sync_{imported}'))


@bp.route('/scan-quality', methods=['POST'])
def scan_quality():
    """Scan each library item's actual video file to detect and store its quality."""
    all_items = runtime.store().list_media_items()
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
            runtime.store().update_quality(item['id'], quality)
            updated += 1
    return redirect(url_for('core.index', status=f'quality_scanned_{updated}'))


@bp.route('/scan-subtitles', methods=['POST'])
def scan_subtitles_route():
    """Rescan every library item for English subtitles (embedded or external)."""
    all_items = runtime.store().list_media_items()
    updated = 0
    for item in all_items:
        result = scan_subtitles(item['path'])
        runtime.store().update_subtitles(item['id'], result)
        if result != item['subtitles']:
            updated += 1
    return redirect(url_for('core.index', status=f'subtitles_scanned_{updated}'))


@bp.route('/check-all', methods=['POST'])
def check_all():
    """Run a quality search for all items, or a specific media_type if supplied."""
    media_type = request.form.get('media_type') or None
    all_items = runtime.store().list_media_items()
    items = (
        [i for i in all_items if i['media_type'] == media_type] if media_type else list(all_items)
    )

    runtime.qb().set_mirror_urls(configured_search_urls())

    checked = 0
    failed = 0
    for item in items:
        try:
            outcome = runtime.qb().check_for_higher_quality(
                title=item['title'],
                year=item['year'],
                current_quality=item['current_quality'],
                preferred_quality=runtime.store().get_setting('preferred_quality') or '2160p',
            )
            best = outcome['best'] if outcome['found'] else None
            runtime.store().add_quality_check(
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
                current_app.logger.warning('Quality check failed for "%s": %s', item['title'], exc)
            # Every title uses the same engine, so once it is unreachable the
            # rest will fail identically - stop rather than retry hundreds of
            # times against a dead mirror.
            break
        except Exception as exc:
            failed += 1
            current_app.logger.warning('Quality check failed for "%s": %s', item['title'], exc)

    if failed and not checked:
        return redirect(url_for('core.index', status='search_unavailable'))
    if failed:
        return redirect(url_for('core.index', status=f'quality_checked_partial_{checked}_{failed}'))
    return redirect(url_for('core.index', status='quality_checked'))


@bp.route('/sync-trakt', methods=['POST'])
def sync_trakt():
    trakt = _trakt_client()
    if not trakt:
        return redirect(url_for('core.index', section='settings', status='trakt_not_configured'))
    synced = 0
    try:
        for item in trakt.collection_movies() + trakt.collection_shows():
            try:
                meta = (
                    tmdb_state.client().metadata_by_imdb_id(item['imdb_id'])
                    if tmdb_state.client()
                    else {}
                )
                runtime.store().add_media_item(
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
        return redirect(url_for('core.index', section='settings', status=status))
    except Exception:
        return redirect(url_for('core.index', section='settings', status='trakt_error'))

    return redirect(url_for('core.index', section='settings', status=f'trakt_synced_{synced}'))
