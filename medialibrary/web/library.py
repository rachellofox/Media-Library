"""Library routes: acting on a title you already have.

Retrying a download, marking one done, scanning quality, refreshing metadata,
fetching subtitles, and offering alternative artwork. Everything here operates
on an existing media item, which is what separates it from Discover.
"""

import re

from flask import Blueprint, jsonify, request

# Imported as a module, not by name: the tests stub cache_poster, and a
# from-import would bind it here and ignore the stub.
from medialibrary import posters, qbt, runtime
from medialibrary.config import FFPROBE_EXE
from medialibrary.downloads import _refresh_local_media_signals
from medialibrary.identify import _best_local_video_path
from medialibrary.items import _ui_item_payload
from medialibrary.posters import TMDB_IMAGE_PREFIX
from medialibrary.qb_search import SearchEngineError
from medialibrary.quality import detect_quality_from_file
from medialibrary.subtitles import _find_video_file, scan_subtitles
from medialibrary.torrents import _torrent_candidates_for

bp = Blueprint('library', __name__)


@bp.route('/api/library/retry-download/<int:media_id>')
def library_retry_download(media_id: int):
    item = _ui_item_payload(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    status = (item['download_status'] or '').strip().lower()
    has_upgrade = bool(item.get('upgrade_available'))
    if (
        status not in {'starting', 'handed_off', 'downloading'}
        and not item.get('file_missing')
        and not has_upgrade
    ):
        return jsonify({'ok': False, 'error': 'not_missing_or_active'}), 409

    meta = {
        'title': item['title'] or item['imdb_id'] or '',
        'year': item['year'],
    }
    try:
        candidates = _torrent_candidates_for(meta)
    except SearchEngineError as exc:
        return jsonify(
            {
                'ok': False,
                'error': 'search_unavailable',
                'message': str(exc),
                'media_id': int(item['id']),
            }
        ), 503
    return jsonify(
        {
            'ok': True,
            'media_id': int(item['id']),
            'title': item['title'] or item['imdb_id'] or 'Title',
            'candidates': candidates,
        }
    )


@bp.route('/api/library/download-progress/<int:media_id>')
def library_download_progress(media_id: int):
    item = _ui_item_payload(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    status = (item.get('download_status') or '').strip().lower()
    if status not in {'starting', 'handed_off', 'downloading'}:
        return jsonify({'ok': False, 'error': 'download_not_active'}), 409

    torrent_hash = (item.get('download_torrent_hash') or '').strip().upper()
    source = (item.get('download_source') or '').strip()

    # Always use qB status as source of truth when WebUI is available.
    if qbt._qbt_webui_enabled():
        try:
            if source == 'qb_webui' and re.fullmatch(r'[0-9A-F]{40}', torrent_hash):
                torrent = qbt._qbt_webui_torrent_info(torrent_hash)
            else:
                torrent = qbt._qbt_match_torrent_for_item(item)
        except Exception:
            torrent = None
        if torrent:
            progress_raw = torrent.get('progress')
            try:
                progress = float(progress_raw)
            except Exception:
                progress = 0.0
            qbt_state = (torrent.get('state') or '').lower()
            done_states = {
                'uploading',
                'stalledup',
                'seeding',
                'pausedup',
                'forcedup',
                'checkingup',
            }
            return jsonify(
                {
                    'ok': True,
                    'source': 'qbittorrent',
                    'state': qbt_state,
                    'progress': progress,
                    'progress_percent': round(progress * 100, 2),
                    'eta': torrent.get('eta'),
                    'name': torrent.get('name') or '',
                    'dlspeed': torrent.get('dlspeed'),
                    'is_complete': qbt_state in done_states,
                }
            )
        if source == 'qb_webui' and torrent_hash:
            return jsonify(
                {
                    'ok': True,
                    'source': 'qbittorrent',
                    'state': 'queued',
                    'progress': 0.0,
                    'progress_percent': 0.0,
                    'is_complete': False,
                }
            )

    return jsonify(
        {
            'ok': True,
            'source': source or 'external',
            'state': status,
            'progress': None,
            'progress_percent': None,
            'is_complete': False,
        }
    )


@bp.route('/api/library/mark-downloaded/<int:media_id>', methods=['POST'])
def library_mark_downloaded(media_id: int):
    item = runtime.store().get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    runtime.store().clear_download_state(media_id)

    # Run a targeted folder scan so quality/subtitles are picked up immediately.
    _refresh_local_media_signals(media_id, item['path'])

    payload = _ui_item_payload(media_id) or {'id': media_id}
    return jsonify({'ok': True, 'item': payload})


@bp.route('/api/library/fetch-subtitles/<int:media_id>', methods=['POST'])
def fetch_subtitles_api(media_id: int):
    """Fetch and save English subtitles for a library item via subliminal."""
    item_row = runtime.store().get_media_item(media_id)
    item = dict(item_row) if item_row else None
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    if not item.get('path') or item.get('file_missing'):
        return jsonify({'ok': False, 'error': 'no_file'}), 409

    video_path = _best_local_video_path(item['path'])
    if not video_path:
        return jsonify({'ok': False, 'error': 'no_video_file'}), 409

    try:
        from medialibrary.subtitle_client import fetch_subtitles

        found = fetch_subtitles(
            video_path,
            title=item.get('title'),
            year=item.get('year'),
        )
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500

    if not found:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    runtime.store().update_subtitles(media_id, scan_subtitles(item['path']))
    payload = _ui_item_payload(media_id) or {}
    return jsonify({'ok': True, 'item': payload})


@bp.route('/api/library/<int:media_id>/posters')
def library_poster_options(media_id: int):
    """Alternative artwork TMDB holds for a title."""
    item = runtime.store().get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    if not runtime.tmdb():
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503
    if not item['tmdb_id']:
        return jsonify({'ok': False, 'error': 'no_tmdb_id'}), 409

    posters = runtime.tmdb().poster_options(item['tmdb_id'], item['media_type'] or 'movie')
    wanted = (request.args.get('language') or '').strip().lower()
    if wanted and wanted != 'all':
        target = None if wanted == 'none' else wanted
        posters = [p for p in posters if (p['language'] or None) == target]

    return jsonify(
        {
            'ok': True,
            'media_id': media_id,
            'current_poster_url': item['poster_url'],
            'posters': posters[:60],
        }
    )


@bp.route('/api/library/<int:media_id>/poster', methods=['POST'])
def library_set_poster(media_id: int):
    """Adopt a chosen TMDB poster as this title's artwork."""
    item = runtime.store().get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    payload = request.get_json(silent=True) or {}
    poster_url = (request.form.get('poster_url') or payload.get('poster_url') or '').strip()
    if not poster_url.startswith(TMDB_IMAGE_PREFIX):
        # Only TMDB's own image host is accepted, so this cannot be pointed at
        # an arbitrary URL for the server to fetch.
        return jsonify({'ok': False, 'error': 'invalid_poster_url'}), 400

    cache_key = item['imdb_id'] or str(media_id)
    cached = posters.cache_poster(cache_key, poster_url, force_replace=True)
    if not cached:
        return jsonify({'ok': False, 'error': 'poster_download_failed'}), 502

    runtime.store().update_poster(media_id, cached)
    return jsonify({'ok': True, 'media_id': media_id, 'poster_url': cached})


@bp.route('/api/quality/<int:media_id>/scan', methods=['POST'])
def scan_quality_for_item(media_id: int):
    """Scan a single library item's video file to detect and store its local quality."""
    item = runtime.store().get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    path = item['path']
    if not path:
        return jsonify({'ok': False, 'error': 'no_path'}), 400

    target = _find_video_file(path)

    if not target:
        return jsonify({'ok': False, 'error': 'no_video_file'}), 400

    quality = detect_quality_from_file(target, ffprobe_exe=FFPROBE_EXE)
    if quality:
        runtime.store().update_quality(media_id, quality)

    payload = _ui_item_payload(media_id)
    return jsonify({'ok': True, 'item': payload})
