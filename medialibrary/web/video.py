"""Playback routes: choosing a strategy, and serving what it produces.

Three strategies per request — direct play, direct stream, HLS transcode — with
the machinery for each in medialibrary.playback. What lives here is the HTTP
surface: picking the strategy for a file, serving segments and playlists, and
reporting progress back to the player.

The filename filters on the two segment routes are load-bearing. They ran before
the item lookup, which is what let a crafted absolute path walk out of the
transcode cache and read any .mp4 on the machine — see tests/test_path_traversal.py.
"""

import os
import threading
import time
from datetime import datetime

from flask import Blueprint, Response, jsonify, render_template, request

from medialibrary import runtime
from medialibrary.config import HLS_CACHE_DIR
from medialibrary.identify import _resolve_episode_file
from medialibrary.playback import (
    _DIRECT_PLAY_AUDIO_CODECS,
    _DIRECT_PLAY_VIDEO_CODECS,
    _HLS_SEGMENT_LENGTH,
    _SEGMENT_FILE_RE,
    _build_vod_playlist,
    _cleanup_finished_direct_stream_job,
    _cleanup_finished_transcode_job,
    _direct_play_issues,
    _direct_stream_jobs,
    _get_video_info,
    _get_video_mime_type,
    _highest_completed_segment,
    _playback_cache_key,
    _request_episode,
    _rewrite_playlist_segments,
    _segment_query_suffix,
    _start_direct_stream,
    _start_hls_transcode,
    _stream_file,
    _stream_file_chunk,
    _transcode_jobs,
)
from medialibrary.subtitles import (
    _extract_embedded_subtitle_to_vtt,
    _find_subtitle_files,
    _find_video_file,
    _srt_to_vtt,
    _subtitle_cache,
)

bp = Blueprint('video', __name__)

# One lock per media item, so two requests for the same segment wait rather
# than starting two transcodes of the same file.
_hls_segment_locks: dict[int, threading.Lock] = {}
_hls_segment_locks_guard = threading.Lock()


def _request_video_file(item) -> str | None:
    """The video file this playback request refers to.

    A TV item's path is the whole show folder, so `?episode=` selects which file
    to play. An episode that was asked for but cannot be resolved returns None
    rather than falling back to the largest file — quietly playing a different
    episode than the one clicked would be worse than a clear failure.
    """
    path = (item.get('path') if isinstance(item, dict) else item['path']) or ''
    episode = _request_episode()
    if episode:
        return _resolve_episode_file(path, episode)
    return _find_video_file(path)


@bp.route('/video/<int:media_id>')
def watch_video(media_id: int):
    """Serve the video player page for a media item."""
    item = runtime.store().get_media_item(media_id)
    if not item:
        return 'Not found', 404

    item = dict(item)
    video_file = _request_video_file(item)
    if not video_file:
        return 'Video file not found', 404

    # Get playback position. Keyed by episode so a show remembers where you
    # stopped in each episode separately, not one position for the whole show.
    playback = runtime.store().get_playback_position(media_id, _request_episode())
    playback = dict(playback) if playback else None
    position_seconds = playback.get('position_seconds', 0) if playback else 0
    duration_seconds = playback.get('duration_seconds') if playback else None

    # Prefer the actual media duration from ffprobe so the player can render a full
    # timeline even when HLS is still being generated and native controls omit it.
    video_info = _get_video_info(video_file)
    if video_info and video_info.get('duration'):
        duration_seconds = video_info['duration']

    # Resolve subtitles from the episode being played, not the show folder, or a
    # series would offer every episode's sidecars at once. Keyed by episode too,
    # so loading episode 2 cannot leave episode 1's tab reading episode 2's
    # subtitle mapping if its track fetch arrives afterwards.
    subtitles = _find_subtitle_files(video_file, media_id, episode_key=_request_episode())

    return render_template(
        'player.html',
        media_id=media_id,
        episode=_request_episode(),
        title=item.get('title') or 'Video Player',
        position_seconds=position_seconds,
        duration_seconds=duration_seconds,
        subtitles=subtitles,
    )


def _hls_lock_for(media_id: int) -> threading.Lock:
    """Return (creating if needed) the per-media lock used by segment requests."""
    with _hls_segment_locks_guard:
        lock = _hls_segment_locks.get(media_id)
        if lock is None:
            lock = threading.Lock()
            _hls_segment_locks[media_id] = lock
        return lock


def _is_hls_playable(manifest_path: str, min_segments: int = 3) -> bool:
    """Return True when an HLS manifest has enough segments to start playback."""
    if not os.path.isfile(manifest_path):
        return False
    try:
        with open(manifest_path, encoding='utf-8', errors='replace') as f:
            content = f.read()
        if not content.startswith('#EXTM3U'):
            return False
        segment_count = content.count('#EXTINF:')
        if segment_count == 0:
            segment_count = content.count('.ts') + content.count('.m4s')
        return segment_count >= min_segments
    except Exception:
        return False


def _is_direct_play_compatible(info: dict) -> bool:
    """Return True when the file can be served directly without any transcoding.

    Requires H.264 video and a browser-native audio codec. Container is not
    the limiting factor for modern Chromium/Firefox which handle MKV fine.
    """
    return (
        info.get('video_codec') in _DIRECT_PLAY_VIDEO_CODECS
        and info.get('audio_codec') in _DIRECT_PLAY_AUDIO_CODECS
    )


@bp.route('/api/video/<int:media_id>/stream')
def stream_video(media_id: int):
    """Stream a video file with range request support (for seeking)."""
    item = runtime.store().get_media_item(media_id)
    if not item:
        return 'Not found', 404

    item = dict(item)
    video_file = _request_video_file(item)
    if not video_file or not os.path.isfile(video_file):
        return 'Video file not found', 404

    try:
        file_size = os.path.getsize(video_file)
    except Exception:
        return 'Cannot access file', 403

    # Detect MIME type from file extension
    mime_type = _get_video_mime_type(video_file)

    # Parse Range header for seeking support
    range_header = request.headers.get('Range')
    if range_header:
        try:
            start_byte = 0
            end_byte = file_size - 1

            # Parse "bytes=0-1023" or "bytes=512-"
            if range_header.startswith('bytes='):
                range_spec = range_header[6:]
                parts = range_spec.split('-')
                if len(parts) == 2:
                    if parts[0]:
                        start_byte = int(parts[0])
                    if parts[1]:
                        end_byte = int(parts[1])

                    # Validate range
                    if start_byte > file_size - 1 or end_byte < start_byte:
                        return 'Range not satisfiable', 416

                    content_length = end_byte - start_byte + 1
                    resp = Response(
                        _stream_file_chunk(video_file, start_byte, end_byte),
                        status=206,
                        mimetype=mime_type,
                    )
                    resp.headers['Content-Range'] = f'bytes {start_byte}-{end_byte}/{file_size}'
                    resp.headers['Content-Length'] = str(content_length)
                    resp.headers['Content-Type'] = mime_type
                    resp.headers['Accept-Ranges'] = 'bytes'
                    return resp
        except Exception:
            pass

    # No range request — serve whole file
    resp = Response(
        _stream_file(video_file),
        mimetype=mime_type,
    )
    resp.headers['Content-Length'] = str(file_size)
    resp.headers['Accept-Ranges'] = 'bytes'
    return resp


@bp.route('/api/video/<int:media_id>/subtitle/<int:index>')
def subtitle_file(media_id: int, index: int):
    """Serve a subtitle file by media_id, episode and subtitle index.

    ?episode= is the same selector the <track> src already carries — read here
    too, or a show's episodes would all draw from whichever one's mapping was
    cached most recently under this media_id, regardless of which is playing.
    """
    subtitle_entries = _subtitle_cache.get((media_id, _request_episode()))
    if not subtitle_entries or index >= len(subtitle_entries):
        return 'Not found', 404

    entry = subtitle_entries[index]
    sub_path = entry.get('path')
    if not sub_path:
        return 'Not found', 404

    if entry.get('type') == 'embedded' and not os.path.isfile(sub_path):
        video_file = entry.get('video_file')
        stream_index = entry.get('stream_index')
        if not video_file or stream_index is None:
            return 'Not found', 404
        if not _extract_embedded_subtitle_to_vtt(video_file, int(stream_index), sub_path):
            return 'Subtitle extraction failed', 500

    if not os.path.isfile(sub_path):
        return 'Not found', 404

    _, ext = os.path.splitext(sub_path)
    ext = ext.lower()

    try:
        with open(sub_path, encoding='utf-8', errors='replace') as f:
            content = f.read()

        if ext == '.vtt':
            return content, 200, {'Content-Type': 'text/vtt; charset=utf-8'}
        if ext == '.srt':
            return _srt_to_vtt(content), 200, {'Content-Type': 'text/vtt; charset=utf-8'}

        return content, 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception:
        return 'Error reading file', 500


@bp.route('/api/video/<int:media_id>/strategy')
def video_strategy(media_id: int):
    """Return the recommended playback strategy for a media item.

    direct_play — file can be served as-is; player should use /stream with range requests.
    hls         — file needs re-encoding; player should use the HLS transcode pipeline.
    """
    item = runtime.store().get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    item = dict(item)
    video_file = _request_video_file(item)
    if not video_file or not os.path.isfile(video_file):
        return jsonify({'ok': False, 'error': 'video_not_found'}), 404

    info = _get_video_info(video_file)
    if not info:
        # ffprobe failed — fall back to HLS so the user still gets something.
        return jsonify({'ok': True, 'strategy': 'hls', 'reason': 'probe_failed'})

    if _is_direct_play_compatible(info):
        return jsonify(
            {
                'ok': True,
                'strategy': 'direct_play',
                'video_codec': info['video_codec'],
                'audio_codec': info['audio_codec'],
            }
        )

    if info.get('video_codec') in _DIRECT_PLAY_VIDEO_CODECS:
        return jsonify(
            {
                'ok': True,
                'strategy': 'direct_stream',
                'video_codec': info['video_codec'],
                'audio_codec': info['audio_codec'],
                'reason': 'audio_codec_incompatible',
            }
        )

    return jsonify(
        {
            'ok': True,
            'strategy': 'hls',
            'video_codec': info['video_codec'],
            'audio_codec': info['audio_codec'],
            'reason': 'codec_incompatible',
        }
    )


@bp.route('/api/video/<int:media_id>/direct-stream/status')
def direct_stream_status(media_id: int):
    """Report whether a direct-stream HLS manifest is ready to start playback."""
    item = runtime.store().get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    item = dict(item)
    video_file = _request_video_file(item)
    if not video_file or not os.path.isfile(video_file):
        return jsonify({'ok': False, 'error': 'video_not_found'}), 404

    manifest_path = _start_direct_stream(media_id, video_file)
    if not manifest_path:
        return jsonify({'ok': False, 'error': 'direct_stream_failed'}), 500

    ready = _is_hls_playable(manifest_path, min_segments=2)
    return jsonify({'ok': True, 'ready': ready})


@bp.route('/api/video/<int:media_id>/direct-stream/master.m3u8')
def direct_stream_master_playlist(media_id: int):
    """Serve direct-stream HLS playlist (video copy + audio transcode)."""

    item = runtime.store().get_media_item(media_id)
    if not item:
        return 'Not found', 404

    item = dict(item)
    video_file = _request_video_file(item)
    if not video_file or not os.path.isfile(video_file):
        return 'Video file not found', 404

    manifest_path = _start_direct_stream(media_id, video_file)
    if not manifest_path:
        return 'Direct streaming failed', 500

    max_wait = 10
    wait_interval = 0.25
    elapsed = 0

    while elapsed < max_wait:
        if _is_hls_playable(manifest_path, min_segments=1):
            try:
                with open(manifest_path) as f:
                    content = f.read()
                if content and content.startswith('#EXTM3U'):
                    return (
                        _rewrite_playlist_segments(content),
                        200,
                        {
                            'Content-Type': 'application/vnd.apple.mpegurl',
                            'Cache-Control': 'no-store, max-age=0',
                        },
                    )
            except Exception:
                pass

        job = _direct_stream_jobs.get(media_id)
        if job and job['process'].poll() is not None:
            _cleanup_finished_direct_stream_job(media_id)
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path) as f:
                        content = f.read()
                    if content and content.startswith('#EXTM3U'):
                        return (
                            _rewrite_playlist_segments(content),
                            200,
                            {
                                'Content-Type': 'application/vnd.apple.mpegurl',
                                'Cache-Control': 'no-store, max-age=0',
                            },
                        )
                except Exception:
                    pass
            return 'Direct streaming error', 500

        time.sleep(wait_interval)
        elapsed += wait_interval

    return 'Stream preparing', 425


@bp.route('/api/video/<int:media_id>/direct-stream/<path:filename>')
def direct_stream_segment(media_id: int, filename: str):
    """Serve direct-stream HLS segments and playlists."""
    cache_dir = os.path.realpath(
        os.path.join(HLS_CACHE_DIR, _playback_cache_key(media_id), 'direct_stream')
    )

    # Rejecting '..' is not enough on its own: os.path.join discards the base
    # when the second part is absolute, so a filename like "C:/…/anything.mp4"
    # walked straight out of the cache and the file was served. Resolve first,
    # then require the result to still sit inside the cache directory — the same
    # containment check _resolve_episode_file uses for ?episode=.
    file_path = os.path.realpath(os.path.join(cache_dir, filename))
    if file_path != cache_dir and not file_path.startswith(cache_dir + os.sep):
        return 'Invalid filename', 400

    if not os.path.isfile(file_path):
        return 'File not found', 404

    try:
        if filename.endswith('.m3u8'):
            with open(file_path) as f:
                content = f.read()
            return (
                content,
                200,
                {
                    'Content-Type': 'application/vnd.apple.mpegurl',
                    'Cache-Control': 'no-store, max-age=0',
                },
            )
        if filename.endswith('.m4s'):
            with open(file_path, 'rb') as f:
                content = f.read()
            return (
                content,
                200,
                {
                    'Content-Type': 'video/iso.segment',
                    'Cache-Control': 'no-store, max-age=0',
                    'Pragma': 'no-cache',
                    'Expires': '0',
                },
            )
        if filename.endswith('.mp4'):
            with open(file_path, 'rb') as f:
                content = f.read()
            return (
                content,
                200,
                {
                    'Content-Type': 'video/mp4',
                    'Cache-Control': 'no-store, max-age=0',
                    'Pragma': 'no-cache',
                    'Expires': '0',
                },
            )
        return 'Unsupported file type', 400
    except Exception:
        return 'Error reading file', 500


@bp.route('/api/video/compatibility-report')
def video_compatibility_report():
    """Return a playback compatibility report for the full media library."""
    only_issues = request.args.get('only_issues', '0').strip().lower() in {'1', 'true', 'yes'}
    limit_param = (request.args.get('limit') or '').strip()
    limit = 0

    if limit_param:
        try:
            limit = int(limit_param)
        except ValueError:
            return jsonify({'ok': False, 'error': 'invalid_limit'}), 400
        if limit < 1 or limit > 5000:
            return jsonify({'ok': False, 'error': 'invalid_limit'}), 400

    rows = runtime.store().list_media_items()
    report_items: list[dict] = []

    counts = {
        'total_items': 0,
        'direct_play_compatible': 0,
        'direct_stream_recommended': 0,
        'transcode_needed': 0,
        'video_missing': 0,
        'probe_failed': 0,
    }

    for row in rows:
        item = dict(row)
        counts['total_items'] += 1

        media_id = item.get('id')
        title = item.get('title')
        year = item.get('year')
        library_path = item.get('path')

        video_file = _find_video_file(library_path)
        if not video_file or not os.path.isfile(video_file):
            counts['video_missing'] += 1
            entry = {
                'media_id': media_id,
                'title': title,
                'year': year,
                'status': 'video_missing',
                'strategy': 'unavailable',
                'issues': ['video_not_found'],
                'path': library_path,
            }
            if not only_issues:
                report_items.append(entry)
            continue

        info = _get_video_info(video_file)
        if not info:
            counts['probe_failed'] += 1
            entry = {
                'media_id': media_id,
                'title': title,
                'year': year,
                'status': 'probe_failed',
                'strategy': 'hls',
                'issues': ['probe_failed'],
                'video_file': video_file,
            }
            report_items.append(entry)
            if limit and len(report_items) >= limit:
                break
            continue

        issues = _direct_play_issues(info)
        compatible = len(issues) == 0

        if compatible:
            counts['direct_play_compatible'] += 1
            if only_issues:
                continue
            status = 'direct_play_compatible'
            strategy = 'direct_play'
        elif info.get('video_codec') in _DIRECT_PLAY_VIDEO_CODECS:
            counts['direct_stream_recommended'] += 1
            status = 'audio_codec_incompatible'
            strategy = 'direct_stream'
        else:
            counts['transcode_needed'] += 1
            status = 'codec_incompatible'
            strategy = 'hls'

        report_items.append(
            {
                'media_id': media_id,
                'title': title,
                'year': year,
                'status': status,
                'strategy': strategy,
                'issues': issues,
                'video_file': video_file,
                'container': info.get('container') or '',
                'video_codec': info.get('video_codec') or '',
                'audio_codec': info.get('audio_codec') or '',
                'width': info.get('width'),
                'height': info.get('height'),
                'duration': info.get('duration'),
            }
        )

        if limit and len(report_items) >= limit:
            break

    return jsonify(
        {
            'ok': True,
            'summary': counts,
            'total_returned': len(report_items),
            'filters': {
                'only_issues': only_issues,
                'limit': limit,
            },
            'items': report_items,
        }
    )


@bp.route('/api/video/<int:media_id>/hls/ping', methods=['POST'])
def hls_ping(media_id: int):
    """Keep-alive ping from the client during active HLS playback.

    Resets the idle kill timer for the running FFmpeg job. The optional
    position_seconds field lets the server track playback progress for
    future throttling or segment cleanup.
    """
    job = _transcode_jobs.get(media_id)
    if not job:
        return jsonify({'ok': False, 'error': 'no_active_job'}), 404
    job['last_ping'] = datetime.now()
    payload = request.get_json(silent=True) or {}
    position = payload.get('position_seconds')
    if position is not None:
        try:
            job['last_position'] = float(position)
        except Exception:
            pass
    return jsonify({'ok': True})


@bp.route('/api/video/<int:media_id>/hls/status')
def hls_status(media_id: int):
    """Report whether the video can be served via HLS (always immediately ready).

    The Jellyfin pattern generates the playlist from probed duration, so
    readiness is purely a function of being able to probe the source.
    Segments are produced on demand by the segment endpoint.
    """
    item = runtime.store().get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    item = dict(item)
    video_file = _request_video_file(item)
    if not video_file or not os.path.isfile(video_file):
        return jsonify({'ok': False, 'error': 'video_not_found'}), 404

    info = _get_video_info(video_file)
    if not info or not info.get('duration'):
        return jsonify({'ok': False, 'error': 'probe_failed'}), 500

    return jsonify(
        {
            'ok': True,
            'ready': True,
            'duration': info.get('duration'),
            'segment_length': _HLS_SEGMENT_LENGTH,
        }
    )


@bp.route('/api/video/<int:media_id>/hls/master.m3u8')
def hls_master_playlist(media_id: int):
    """Return a complete VOD playlist for the entire source duration.

    Segments are not produced here; they are transcoded on demand by the
    segment endpoint when hls.js requests them. This lets the player seek
    anywhere in the timeline without restarting the player.
    """
    item = runtime.store().get_media_item(media_id)
    if not item:
        return 'Not found', 404

    item = dict(item)
    video_file = _request_video_file(item)
    if not video_file or not os.path.isfile(video_file):
        return 'Video file not found', 404

    info = _get_video_info(video_file)
    duration = float((info or {}).get('duration') or 0.0)
    if duration <= 0:
        return 'Probe failed', 500

    playlist = _build_vod_playlist(duration, segment_query=_segment_query_suffix())
    return (
        playlist,
        200,
        {
            'Content-Type': 'application/vnd.apple.mpegurl',
            'Cache-Control': 'no-store, max-age=0',
        },
    )


@bp.route('/api/video/<int:media_id>/hls/<path:filename>')
def hls_segment(media_id: int, filename: str):
    """Serve HLS segments, transcoding on demand using the Jellyfin pattern.

    For segment_NNNNN.ts: if the file is already on disk and complete,
    serve it. Otherwise check whether the running transcoder is close
    enough to catch up; if so, wait. Otherwise kill the old job and
    start a new FFmpeg run beginning at this segment.
    """

    if '..' in filename or filename.startswith('/') or '\\' in filename:
        return 'Invalid filename', 400

    cache_dir = os.path.join(HLS_CACHE_DIR, _playback_cache_key(media_id))
    file_path = os.path.join(cache_dir, filename)

    # Non-segment file: only allow internal playlist for debugging; reject everything else.
    seg_match = _SEGMENT_FILE_RE.match(filename)
    if not seg_match:
        return 'Not found', 404

    requested_index = int(seg_match.group(1))

    # Look up the source file once for restart decisions.
    item = runtime.store().get_media_item(media_id)
    if not item:
        return 'Not found', 404
    video_file = _request_video_file(item)
    if not video_file or not os.path.isfile(video_file):
        return 'Video not found', 404

    # Threshold matches Jellyfin's 24/segmentLength rule of thumb.
    gap_threshold = max(1, int(24 / _HLS_SEGMENT_LENGTH))

    lock = _hls_lock_for(media_id)
    deadline = time.monotonic() + 30.0
    poll_interval = 0.1

    def _segment_ready() -> bool:
        # A segment file is safe to serve when either the transcoder has finished
        # entirely, or when the next segment file already exists (meaning FFmpeg
        # has closed this one and moved on).
        if not os.path.isfile(file_path):
            return False
        next_path = os.path.join(cache_dir, f'segment_{requested_index + 1:05d}.ts')
        if os.path.isfile(next_path):
            return True
        job = _transcode_jobs.get(media_id)
        if not job:
            return True
        proc = job.get('process')
        if proc and proc.poll() is not None:
            _cleanup_finished_transcode_job(media_id)
            return True
        return False

    if not _segment_ready():
        with lock:
            if not _segment_ready():
                _cleanup_finished_transcode_job(media_id)
                job = _transcode_jobs.get(media_id)
                current_segment = _highest_completed_segment(cache_dir)

                need_restart = False
                if not job:
                    need_restart = True
                else:
                    job_start = int(job.get('start_segment') or 0)
                    if requested_index < job_start:
                        # We seeked backwards before the current job's range.
                        need_restart = True
                    elif current_segment is None:
                        # Job hasn't produced a complete segment yet; wait briefly.
                        need_restart = False
                    elif requested_index < current_segment:
                        # Already past it but file got deleted somehow; restart.
                        need_restart = True
                    elif requested_index - current_segment > gap_threshold:
                        # Player jumped far ahead of the transcoder.
                        need_restart = True

                if need_restart:
                    _start_hls_transcode(media_id, video_file, start_segment=requested_index)

                # Wait for FFmpeg to write our segment.
                while time.monotonic() < deadline:
                    if _segment_ready():
                        break
                    job = _transcode_jobs.get(media_id)
                    if job:
                        proc = job.get('process')
                        if proc and proc.poll() is not None and not os.path.isfile(file_path):
                            _cleanup_finished_transcode_job(media_id)
                            break
                    time.sleep(poll_interval)

    if not os.path.isfile(file_path):
        return 'Segment not ready', 504

    try:
        with open(file_path, 'rb') as f:
            content = f.read()
    except Exception:
        return 'Error reading segment', 500

    return (
        content,
        200,
        {
            'Content-Type': 'video/mp2t',
            'Cache-Control': 'no-store, max-age=0',
            'Pragma': 'no-cache',
            'Expires': '0',
        },
    )


@bp.route('/api/video/<int:media_id>/playback', methods=['GET', 'POST'])
def playback_position_api(media_id: int):
    """Get or set the playback position for a media item, or one of its episodes.

    `?episode=` is the same relative-path selector direct playback already
    uses, so a show's episodes never share one position — see
    `medialibrary.identify._resolve_episode_file`.
    """
    episode_key = _request_episode()

    if request.method == 'GET':
        item = runtime.store().get_media_item(media_id)
        if not item:
            return jsonify({'ok': False, 'error': 'not_found'}), 404

        playback = runtime.store().get_playback_position(media_id, episode_key)
        playback = dict(playback) if playback else None
        if playback:
            return jsonify(
                {
                    'ok': True,
                    'position_seconds': playback.get('position_seconds', 0),
                    'duration_seconds': playback.get('duration_seconds'),
                    'watched': bool(playback.get('watched')),
                    'last_updated': playback.get('last_updated'),
                }
            )
        return jsonify(
            {'ok': True, 'position_seconds': 0, 'duration_seconds': None, 'watched': False}
        )

    elif request.method == 'POST':
        item = runtime.store().get_media_item(media_id)
        if not item:
            return jsonify({'ok': False, 'error': 'not_found'}), 404

        payload = request.get_json(silent=True) or {}
        position_seconds = payload.get('position_seconds', 0)
        duration_seconds = payload.get('duration_seconds')

        try:
            position_seconds = float(position_seconds) if position_seconds is not None else 0
            duration_seconds = float(duration_seconds) if duration_seconds is not None else None
        except Exception:
            return jsonify({'ok': False, 'error': 'invalid_format'}), 400

        runtime.store().set_playback_position(
            media_id, position_seconds, duration_seconds, episode_key
        )
        return jsonify({'ok': True, 'position_seconds': position_seconds})

    return jsonify({'ok': False, 'error': 'method_not_allowed'}), 405
