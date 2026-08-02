"""Discover routes: what you could add, and what you have hidden.

Thin HTTP over medialibrary.discover, which does the work of comparing the
library against TMDB and Trakt. The ignore lists live here too — a title or
collection you have dismissed should stay dismissed, so Discover stops offering
it without pretending it does not exist.
"""

from flask import Blueprint, jsonify, request

from medialibrary import qbt, runtime
from medialibrary.discover import (
    _discover_incomplete_collections,
    _discover_missing_episodes,
    _discover_watchlist,
)
from medialibrary.downloads import _staging_path_for
from medialibrary.identify import _is_local_media_missing
from medialibrary.posters import _cache_meta_poster
from medialibrary.qb_search import SearchEngineError
from medialibrary.qbt import _extract_btih_hash
from medialibrary.storage import Storage
from medialibrary.torrents import _torrent_candidates_for
from medialibrary.trakt_auth import _trakt_context

bp = Blueprint('discover', __name__)


@bp.route('/api/discover')
def discover_data():
    trakt = _trakt_context()
    watchlist, watchlist_ok = _discover_watchlist()
    return jsonify(
        {
            'watchlist': watchlist,
            # False only when Trakt is connected and the request to it failed —
            # never for "not connected" or "genuinely empty", which the front
            # end already tells apart via trakt_connected.
            'watchlist_error': not watchlist_ok,
            'collections': _discover_incomplete_collections(),
            'trending': runtime.tmdb().trending() if runtime.tmdb() else [],
            'trakt_configured': trakt['configured'],
            'trakt_connected': trakt['connected'],
            'tmdb_configured': bool(runtime.tmdb()),
        }
    )


@bp.route('/api/discover/hero')
def discover_hero_data():
    if not runtime.tmdb():
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503

    tmdb_id_raw = request.args.get('tmdb_id', '').strip()
    media_type = request.args.get('media_type', '').strip().lower()
    if media_type not in {'movie', 'tv'}:
        return jsonify({'ok': False, 'error': 'invalid_media_type'}), 400
    try:
        tmdb_id = int(tmdb_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_tmdb_id'}), 400

    meta = runtime.tmdb().metadata_by_tmdb_id(tmdb_id, media_type)
    return jsonify(
        {
            'ok': True,
            'item': {
                'tmdb_id': meta.get('tmdb_id') or tmdb_id,
                'imdb_id': meta.get('imdb_id'),
                'title': meta.get('title') or request.args.get('title') or str(tmdb_id),
                'year': meta.get('year'),
                'media_type': meta.get('media_type') or media_type,
                'poster_url': meta.get('poster_url'),
                'synopsis': meta.get('synopsis'),
                'actors': meta.get('actors'),
                'genre_1': meta.get('genre_1'),
                'genre_2': meta.get('genre_2'),
                'rating': meta.get('rating'),
                'current_quality': None,
                'subtitles': None,
                'found': 0,
                'favourite': 0,
            },
        }
    )


@bp.route('/api/discover/add-and-search', methods=['POST'])
def discover_add_and_search():
    if not runtime.tmdb():
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503

    payload = request.get_json(silent=True) or {}
    tmdb_id_raw = str(request.form.get('tmdb_id') or payload.get('tmdb_id') or '').strip()
    media_type = (
        str(request.form.get('media_type') or payload.get('media_type') or 'movie').strip().lower()
    )
    if media_type not in {'movie', 'tv'}:
        return jsonify({'ok': False, 'error': 'invalid_media_type'}), 400

    try:
        tmdb_id = int(tmdb_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_tmdb_id'}), 400

    meta = runtime.tmdb().metadata_by_tmdb_id(tmdb_id, media_type)
    if not meta.get('imdb_id'):
        return jsonify({'ok': False, 'error': 'missing_imdb_id'}), 400

    runtime.store().add_media_item(
        imdb_id=meta['imdb_id'],
        tmdb_id=meta.get('tmdb_id'),
        title=meta['title'],
        year=meta['year'],
        media_type=meta['media_type'],
        collection_id=meta.get('collection_id'),
        collection_name=meta.get('collection_name'),
        current_quality=None,
        path=None,
        poster_url=_cache_meta_poster(meta['imdb_id'], meta),
        synopsis=meta.get('synopsis'),
        actors=meta.get('actors'),
        genre_1=meta.get('genre_1'),
        genre_2=meta.get('genre_2'),
        rating=meta.get('rating'),
        subtitles=None,
    )
    row = runtime.store().get_media_item_by_imdb_id(meta['imdb_id'])
    media_id = row['id'] if row else None
    try:
        candidates = _torrent_candidates_for(meta)
    except SearchEngineError as exc:
        return jsonify(
            {
                'ok': False,
                'error': 'search_unavailable',
                'message': str(exc),
                'media_id': media_id,
            }
        ), 503

    return jsonify(
        {
            'ok': True,
            'media_id': media_id,
            'title': meta.get('title') or '',
            'year': meta.get('year'),
            'candidates': candidates,
        }
    )


@bp.route('/api/discover/start-download', methods=['POST'])
def discover_start_download():
    payload = request.get_json(silent=True) or {}
    link = (request.form.get('link') or payload.get('link') or '').strip()
    desc_link = (request.form.get('desc_link') or payload.get('desc_link') or '').strip()
    media_id_raw = request.form.get('media_id') or payload.get('media_id')
    if not link and not desc_link:
        return jsonify({'ok': False, 'error': 'missing_link'}), 400

    launch = link or desc_link
    media_item = None
    try:
        if media_id_raw not in (None, ''):
            media_item = runtime.store().get_media_item(int(media_id_raw))
    except Exception:
        media_item = None

    if qbt._qbt_webui_enabled() and media_item:
        media_type = media_item['media_type'] or 'movie'
        target_setting = 'tv_path' if media_type == 'tv' else 'movies_path'
        library_path = (runtime.store().get_setting(target_setting) or '').strip()
        # Download into staging so a part-finished release is never visible to
        # the library scanner; it only enters the library once finalised.
        target_path = _staging_path_for(media_type) or library_path
        if target_path:
            # Captured before submitting: an item that already plays is being
            # upgraded, so finalisation must replace rather than just adopt.
            existing_path = (media_item['path'] or '').strip()
            is_upgrade = bool(existing_path) and not _is_local_media_missing(existing_path)
            try:
                runtime.store().set_download_state(
                    media_item_id=int(media_item['id']),
                    status='starting',
                    source='qb_webui',
                    message='Submitting to qBittorrent',
                    mode='upgrade' if is_upgrade else 'fill',
                    previous_path=existing_path or None,
                )
                submitted = qbt._qbt_webui_add_download(launch, target_path)
                if submitted:
                    torrent_hash = _extract_btih_hash(launch)
                    runtime.store().set_download_state(
                        media_item_id=int(media_item['id']),
                        status='downloading',
                        source='qb_webui',
                        message=f'Destination: {target_path}',
                        torrent_hash=torrent_hash,
                    )
                    return jsonify(
                        {
                            'ok': True,
                            'mode': 'qbittorrent',
                            'save_path': target_path,
                            'section': 'tv' if media_type == 'tv' else 'movies',
                            'torrent_hash': torrent_hash,
                        }
                    )
            except Exception:
                pass

    # Fallback: frontend opens the URL and lets OS/client handle destination.
    if media_item:
        runtime.store().set_download_state(
            media_item_id=int(media_item['id']),
            status='handed_off',
            source='external_client',
            message='Sent to torrent client',
        )
    return jsonify({'ok': True, 'mode': 'fallback', 'launch_url': launch, 'section': 'movies'})


@bp.route('/api/discover/ignore-title', methods=['POST'])
def discover_ignore_title():
    payload = request.get_json(silent=True) or {}
    tmdb_id_raw = request.form.get('tmdb_id') or payload.get('tmdb_id')
    collection_id_raw = request.form.get('collection_id') or payload.get('collection_id')
    title = (request.form.get('title') or payload.get('title') or '').strip() or None
    collection_name = (
        request.form.get('collection_name') or payload.get('collection_name') or ''
    ).strip() or None
    try:
        tmdb_id = int(tmdb_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_tmdb_id'}), 400

    collection_id = None
    try:
        if collection_id_raw not in (None, ''):
            collection_id = int(collection_id_raw)
    except Exception:
        collection_id = None

    runtime.store().ignore_discover_title(
        tmdb_id=tmdb_id,
        collection_id=collection_id,
        title=title,
        collection_name=collection_name,
    )
    return jsonify({'ok': True, 'tmdb_id': tmdb_id, 'collection_id': collection_id})


@bp.route('/api/discover/ignore-collection', methods=['POST'])
def discover_ignore_collection():
    payload = request.get_json(silent=True) or {}
    collection_id_raw = request.form.get('collection_id') or payload.get('collection_id')
    collection_name = (
        request.form.get('collection_name') or payload.get('collection_name') or ''
    ).strip() or None
    try:
        collection_id = int(collection_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_collection_id'}), 400

    runtime.store().ignore_discover_collection(
        collection_id=collection_id, collection_name=collection_name
    )
    return jsonify({'ok': True, 'collection_id': collection_id})


@bp.route('/api/discover/ignored')
def discover_ignored():
    return jsonify(
        {
            'titles': runtime.store().list_discover_ignored_titles(),
            'collections': runtime.store().list_discover_ignored_collections(),
        }
    )


@bp.route('/api/discover/unignore-title', methods=['POST'])
def discover_unignore_title():
    payload = request.get_json(silent=True) or {}
    tmdb_id_raw = request.form.get('tmdb_id') or payload.get('tmdb_id')
    try:
        tmdb_id = int(tmdb_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_tmdb_id'}), 400

    runtime.store().unignore_discover_title(tmdb_id)
    return jsonify({'ok': True, 'tmdb_id': tmdb_id})


@bp.route('/api/discover/unignore-collection', methods=['POST'])
def discover_unignore_collection():
    payload = request.get_json(silent=True) or {}
    collection_id_raw = request.form.get('collection_id') or payload.get('collection_id')
    try:
        collection_id = int(collection_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_collection_id'}), 400

    runtime.store().unignore_discover_collection(collection_id)
    return jsonify({'ok': True, 'collection_id': collection_id})


@bp.route('/api/discover/unignore-all-titles', methods=['POST'])
def discover_unignore_all_titles():
    runtime.store().unignore_all_discover_titles()
    return jsonify({'ok': True})


@bp.route('/api/discover/unignore-all-collections', methods=['POST'])
def discover_unignore_all_collections():
    runtime.store().unignore_all_discover_collections()
    return jsonify({'ok': True})


@bp.route('/api/discover/missing-episodes')
def discover_missing_episodes():
    if not runtime.tmdb():
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503
    shows = _discover_missing_episodes()
    return jsonify(
        {
            'ok': True,
            'shows': shows,
            'show_count': len(shows),
            'episode_count': sum(show['missing_count'] for show in shows),
        }
    )


@bp.route('/api/discover/ignore-tv', methods=['POST'])
def discover_ignore_tv():
    payload = request.get_json(silent=True) or {}
    try:
        tmdb_id = int(request.form.get('tmdb_id') or payload.get('tmdb_id'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'invalid_tmdb_id'}), 400

    raw_season = request.form.get('season') or payload.get('season')
    if raw_season in (None, '', 'all'):
        season_number = Storage.IGNORE_WHOLE_SHOW
    else:
        try:
            season_number = int(raw_season)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'invalid_season'}), 400

    title = (request.form.get('title') or payload.get('title') or '').strip() or None
    runtime.store().ignore_tv_season(tmdb_id, season_number, title)
    return jsonify({'ok': True, 'tmdb_id': tmdb_id, 'season': season_number})


@bp.route('/api/discover/unignore-tv', methods=['POST'])
def discover_unignore_tv():
    payload = request.get_json(silent=True) or {}
    try:
        tmdb_id = int(request.form.get('tmdb_id') or payload.get('tmdb_id'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'invalid_tmdb_id'}), 400
    raw_season = request.form.get('season') or payload.get('season')
    season_number = None
    if raw_season not in (None, '', 'all'):
        try:
            season_number = int(raw_season)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'invalid_season'}), 400
    runtime.store().unignore_tv(tmdb_id, season_number)
    return jsonify({'ok': True})
