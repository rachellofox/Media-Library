"""Video playback: direct play, direct stream, and HLS transcoding.

Three strategies, chosen per request. A file the browser can already decode is
streamed as-is; one whose container is wrong but whose codecs are fine is
remuxed; anything else is transcoded to HLS. The transcode jobs outlive the
request that starts them, so their ffmpeg handles and kill timers are held in
module state here rather than on the request.

Nothing in here touches the database or TMDB, which is what makes it separable
from the rest of the app.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse
from datetime import datetime

from flask import request

from medialibrary.config import FFMPEG_EXE, FFPROBE_EXE, HLS_CACHE_DIR


def _cleanup_hls_cache(media_id: int | None = None) -> None:
    """Remove HLS cache directory for a media item, or full cache if media_id is None."""
    if media_id is not None:
        cache_path = os.path.join(HLS_CACHE_DIR, _playback_cache_key(media_id))
        try:
            if os.path.isdir(cache_path):
                import shutil

                shutil.rmtree(cache_path, ignore_errors=True)
        except Exception:
            pass
    else:
        # Clean full HLS cache on startup
        try:
            if os.path.isdir(HLS_CACHE_DIR):
                import shutil

                shutil.rmtree(HLS_CACHE_DIR, ignore_errors=True)
        except Exception:
            pass


def _request_episode() -> str:
    # Transcode helpers are also reached from background threads, where there is
    # no request to read; treating that as "no episode" keeps the cache key stable.
    try:
        return (request.args.get('episode') or '').strip()
    except RuntimeError:
        return ''


def _playback_cache_key(media_id: int) -> str:
    """Transcode cache folder name for this request, unique per episode.

    Keyed on media_id alone, two episodes of one show would share a single HLS
    cache and serve each other's segments.
    """
    episode = _request_episode()
    if not episode:
        return str(media_id)
    return f'{media_id}-{hashlib.sha1(episode.encode("utf-8")).hexdigest()[:12]}'


def _get_video_mime_type(filepath: str) -> str:
    """Return the MIME type for a video file based on extension."""
    _, ext = os.path.splitext(filepath)
    ext = ext.lower()
    mime_map = {
        '.mp4': 'video/mp4',
        '.mkv': 'video/x-matroska',
        '.avi': 'video/x-msvideo',
        '.m4v': 'video/x-m4v',
        '.mov': 'video/quicktime',
        '.wmv': 'video/x-ms-wmv',
    }
    return mime_map.get(ext, 'video/mp4')


# Track active HLS transcoding jobs: media_id -> {'process': Popen, 'started': timestamp}
_transcode_jobs: dict[int, dict] = {}

# Track active direct-stream jobs (video copy + audio transcode).
_direct_stream_jobs: dict[int, dict] = {}

# Jellyfin-style segment-on-demand HLS settings.
_HLS_SEGMENT_LENGTH = 6  # seconds per segment for the generated VOD playlist.


def _segment_lengths_for(duration: float, seg_len: int = _HLS_SEGMENT_LENGTH) -> list[float]:
    """Return per-segment durations covering the full source runtime.

    All segments are seg_len seconds except the final one, which holds the
    remainder when duration is not an exact multiple.
    """
    if duration <= 0:
        return []
    full = int(duration // seg_len)
    remainder = duration - (full * seg_len)
    out: list[float] = [float(seg_len)] * full
    if remainder > 0.05:
        out.append(round(float(remainder), 3))
    elif not out:
        out.append(round(float(duration), 3))
    return out


def _segment_query_suffix() -> str:
    """Query string to append to segment URLs inside a playlist.

    A player resolves the bare segment names in a playlist against the playlist's
    own URL, which drops its query string — so without this the episode selector
    would be lost and segments would be looked for in the wrong cache directory.
    """
    episode = _request_episode()
    return f'?episode={urllib.parse.quote(episode)}' if episode else ''


def _build_vod_playlist(
    duration: float, seg_len: int = _HLS_SEGMENT_LENGTH, segment_query: str = ''
) -> str:
    """Generate a complete HLS VOD playlist listing every segment up front."""
    seg_lens = _segment_lengths_for(duration, seg_len)
    target = max(1, int(seg_len))
    lines = [
        '#EXTM3U',
        '#EXT-X-VERSION:3',
        f'#EXT-X-TARGETDURATION:{target}',
        '#EXT-X-MEDIA-SEQUENCE:0',
        '#EXT-X-PLAYLIST-TYPE:VOD',
        '#EXT-X-INDEPENDENT-SEGMENTS',
    ]
    for i, sl in enumerate(seg_lens):
        lines.append(f'#EXTINF:{sl:.6f},')
        lines.append(f'segment_{i:05d}.ts{segment_query}')
    lines.append('#EXT-X-ENDLIST')
    return '\n'.join(lines) + '\n'


_SEGMENT_FILE_RE = re.compile(r'^segment_(\d+)\.ts$')


def _highest_completed_segment(cache_dir: str) -> int | None:
    """Scan cache_dir for the largest fully-written segment_NNNNN.ts index.

    A segment is considered complete only if a strictly larger-numbered
    segment also exists, since FFmpeg writes the current segment
    incrementally.
    """
    if not os.path.isdir(cache_dir):
        return None
    indices: list[int] = []
    try:
        for name in os.listdir(cache_dir):
            m = _SEGMENT_FILE_RE.match(name)
            if m:
                indices.append(int(m.group(1)))
    except Exception:
        return None
    if not indices:
        return None
    indices.sort()
    # All but the highest are guaranteed complete (FFmpeg moves on after closing them).
    return indices[-2] if len(indices) > 1 else None


def _is_hls_complete(manifest_path: str) -> bool:
    """Return True when an HLS VOD manifest has finished writing."""
    if not os.path.isfile(manifest_path):
        return False
    try:
        with open(manifest_path, encoding='utf-8', errors='replace') as f:
            content = f.read()
        return '#EXT-X-ENDLIST' in content
    except Exception:
        return False


def _cleanup_finished_transcode_job(media_id: int) -> None:
    """Remove completed/failed jobs and close any open log file handles."""
    job = _transcode_jobs.get(media_id)
    if not job:
        return
    process = job.get('process')
    if process and process.poll() is None:
        return

    log_handle = job.get('log_handle')
    if log_handle:
        try:
            log_handle.close()
        except Exception:
            pass

    _transcode_jobs.pop(media_id, None)


def _cleanup_finished_direct_stream_job(media_id: int) -> None:
    """Remove completed/failed direct-stream jobs and close open log handles."""
    job = _direct_stream_jobs.get(media_id)
    if not job:
        return
    process = job.get('process')
    if process and process.poll() is None:
        return

    log_handle = job.get('log_handle')
    if log_handle:
        try:
            log_handle.close()
        except Exception:
            pass

    _direct_stream_jobs.pop(media_id, None)


def _stop_hls_transcode_job(media_id: int) -> None:
    """Stop an active HLS transcode process and release resources."""
    job = _transcode_jobs.get(media_id)
    if not job:
        return

    process = job.get('process')
    if process and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    log_handle = job.get('log_handle')
    if log_handle:
        try:
            log_handle.close()
        except Exception:
            pass

    _transcode_jobs.pop(media_id, None)


# Jellyfin-style keep-alive: kill idle HLS jobs after this many seconds with no ping.
_HLS_JOB_PING_TIMEOUT = 60.0


def _start_job_kill_timer(media_id: int) -> None:
    """Start a background watchdog thread for an HLS transcode job.

    The job is terminated when no client ping has been received for
    _HLS_JOB_PING_TIMEOUT seconds. The reference time is the most recent
    ping, falling back to the job start time so newly created jobs are
    also reaped if the player never connects.
    """

    def _watch() -> None:
        import time as _time

        while True:
            _time.sleep(10)
            job = _transcode_jobs.get(media_id)
            if not job:
                return
            process = job.get('process')
            if process and process.poll() is not None:
                _cleanup_finished_transcode_job(media_id)
                return
            reference = job.get('last_ping') or job.get('started')
            if reference is None:
                return
            age = (datetime.now() - reference).total_seconds()
            if age > _HLS_JOB_PING_TIMEOUT:
                _stop_hls_transcode_job(media_id)
                return

    t = threading.Thread(target=_watch, daemon=True, name=f'hls-kill-timer-{media_id}')
    t.start()


def _get_video_info(video_file: str) -> dict | None:
    """Get video stream info using ffprobe.

    Returns width, height, duration, video_codec, audio_codec, and container.
    """
    try:
        result = subprocess.run(
            [
                FFPROBE_EXE,
                '-v',
                'quiet',
                '-print_format',
                'json',
                '-show_streams',
                '-show_format',
                video_file,
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        video_stream = None
        audio_stream = None

        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video' and video_stream is None:
                video_stream = stream
            elif stream.get('codec_type') == 'audio' and audio_stream is None:
                audio_stream = stream

        if not video_stream:
            return None

        fmt = data.get('format', {})
        # format_name may be 'matroska,webm' — take the first token.
        container = (fmt.get('format_name') or '').split(',')[0].strip()

        return {
            'width': video_stream.get('width'),
            'height': video_stream.get('height'),
            'duration': float(fmt.get('duration') or 0),
            'video_codec': (video_stream.get('codec_name') or '').lower(),
            'audio_codec': (audio_stream.get('codec_name') or '').lower() if audio_stream else '',
            'container': container,
        }
    except Exception:
        return None


# Video codecs browsers reliably decode natively (H.264 is universally supported).
_DIRECT_PLAY_VIDEO_CODECS = {'h264', 'avc', 'avc1'}

# Audio codecs browsers handle natively.
_DIRECT_PLAY_AUDIO_CODECS = {'aac', 'mp3', 'mpeg', 'opus', 'vorbis'}


def _direct_play_issues(info: dict) -> list[str]:
    """Return a list of compatibility issues for direct browser playback."""
    issues: list[str] = []
    video_codec = (info.get('video_codec') or '').lower()
    audio_codec = (info.get('audio_codec') or '').lower()

    if video_codec not in _DIRECT_PLAY_VIDEO_CODECS:
        issues.append('video_codec_unsupported')

    # Audio can be absent for silent content; only flag when a codec exists but is unsupported.
    if audio_codec and audio_codec not in _DIRECT_PLAY_AUDIO_CODECS:
        issues.append('audio_codec_unsupported')

    return issues


def _start_hls_transcode(media_id: int, video_file: str, start_segment: int = 0) -> str | None:
    """Spawn an FFmpeg job that produces HLS segments starting at start_segment.

    Mirrors Jellyfin's segment-on-demand approach: the segment endpoint
    decides which segment is needed and asks for a transcode job that
    begins exactly there. The output filenames use FFmpeg's start_number,
    so segment_NNNNN.ts written to disk has the index requested by the
    HLS playlist.
    """
    try:
        start_segment = max(0, int(start_segment))
    except Exception:
        start_segment = 0

    cache_dir = os.path.join(HLS_CACHE_DIR, _playback_cache_key(media_id))
    os.makedirs(cache_dir, exist_ok=True)

    # Always stop any existing job before launching a new one at a different position.
    _stop_hls_transcode_job(media_id)

    start_seconds = float(start_segment * _HLS_SEGMENT_LENGTH)

    log_path = os.path.join(cache_dir, 'ffmpeg.log')
    try:
        # Deliberately not a context manager: the handle is ffmpeg's stdout and
        # has to outlive this function, so it is closed when the job is stopped.
        log_handle = open(log_path, 'ab')  # noqa: SIM115
    except Exception:
        return None

    seg_len = _HLS_SEGMENT_LENGTH
    # GOP aligned to segment boundary so each segment starts on a keyframe.
    gop_size = seg_len * 30
    internal_playlist = os.path.join(cache_dir, 'internal.m3u8')
    cmd = [
        FFMPEG_EXE,
        '-hide_banner',
        '-loglevel',
        'warning',
        '-ss',
        f'{start_seconds:.3f}',
        '-i',
        video_file,
        '-map_metadata',
        '-1',
        '-map_chapters',
        '-1',
        '-map',
        '0:v:0',
        '-map',
        '0:a:0?',
        '-sn',
        '-dn',
        '-max_muxing_queue_size',
        '2048',
        '-c:v',
        'libx264',
        '-preset',
        'veryfast',
        '-crf',
        '23',
        '-pix_fmt',
        'yuv420p',
        '-force_key_frames:0',
        f'expr:gte(t,n_forced*{seg_len})',
        '-sc_threshold:v:0',
        '0',
        '-g',
        str(gop_size),
        '-keyint_min',
        str(seg_len),
        '-c:a',
        'aac',
        '-ac',
        '2',
        '-ar',
        '48000',
        '-b:a',
        '192k',
        # Keep input timestamps and align the output timeline from the seek point.
        '-copyts',
        '-avoid_negative_ts',
        'disabled',
        '-start_at_zero',
        '-f',
        'hls',
        '-hls_time',
        str(seg_len),
        '-hls_list_size',
        '0',
        '-hls_playlist_type',
        'vod',
        '-hls_segment_type',
        'mpegts',
        '-hls_flags',
        'independent_segments+temp_file',
        '-start_number',
        str(start_segment),
        '-hls_segment_filename',
        os.path.join(cache_dir, 'segment_%05d.ts'),
        '-y',
        internal_playlist,
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        try:
            log_handle.close()
        except Exception:
            pass
        return None

    _transcode_jobs[media_id] = {
        'process': process,
        'started': datetime.now(),
        'start_segment': start_segment,
        'start_seconds': start_seconds,
        'cache_dir': cache_dir,
        'log_path': log_path,
        'log_handle': log_handle,
        'last_ping': datetime.now(),
        'last_position': None,
    }
    _start_job_kill_timer(media_id)
    return cache_dir


def _start_direct_stream(media_id: int, video_file: str) -> str | None:
    """Start a direct-stream HLS pipeline (copy video, transcode audio to AAC)."""
    _cleanup_finished_direct_stream_job(media_id)
    if media_id in _direct_stream_jobs:
        return os.path.join(
            HLS_CACHE_DIR, _playback_cache_key(media_id), 'direct_stream', 'master.m3u8'
        )

    cache_dir = os.path.join(HLS_CACHE_DIR, _playback_cache_key(media_id), 'direct_stream')
    os.makedirs(cache_dir, exist_ok=True)

    master_m3u8 = os.path.join(cache_dir, 'master.m3u8')
    init_segment = os.path.join(cache_dir, 'init.mp4')
    if _is_hls_complete(master_m3u8) and os.path.isfile(init_segment):
        return master_m3u8
    if os.path.isfile(master_m3u8):
        shutil.rmtree(cache_dir, ignore_errors=True)
        os.makedirs(cache_dir, exist_ok=True)

    log_handle = None
    try:
        log_path = os.path.join(cache_dir, 'ffmpeg.log')
        # Deliberately not a context manager: the handle is ffmpeg's stdout and
        # has to outlive this function, so it is closed when the job is stopped.
        log_handle = open(log_path, 'ab')  # noqa: SIM115

        cmd = [
            FFMPEG_EXE,
            '-i',
            video_file,
            '-map',
            '0:v:0',
            '-map',
            '0:a:0?',
            '-map',
            '-0:s',
            '-sn',
            '-dn',
            '-c:v',
            'copy',
            '-c:a',
            'aac',
            '-ac',
            '2',
            '-ar',
            '48000',
            '-b:a',
            '192k',
            '-f',
            'hls',
            '-hls_time',
            '2',
            '-hls_list_size',
            '0',
            '-hls_playlist_type',
            'event',
            '-hls_flags',
            'independent_segments',
            '-hls_segment_type',
            'fmp4',
            '-hls_fmp4_init_filename',
            'init.mp4',
            '-hls_segment_filename',
            os.path.join(cache_dir, 'segment_%03d.m4s'),
            '-y',
            master_m3u8,
        ]

        process = subprocess.Popen(
            cmd,
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            cwd=cache_dir,
        )

        _direct_stream_jobs[media_id] = {
            'process': process,
            'started': datetime.now(),
            'cache_dir': cache_dir,
            'log_path': log_path,
            'log_handle': log_handle,
        }

        return master_m3u8
    except Exception:
        # The log handle must be closed before the directory can go: on Windows
        # an open file keeps ffmpeg.log locked, so rmtree would quietly leave the
        # cache behind — and every failed start would leak a file descriptor.
        if log_handle:
            try:
                log_handle.close()
            except Exception:
                pass
        shutil.rmtree(cache_dir, ignore_errors=True)
        return None


def _stream_file(filepath: str, chunk_size: int = 8192):
    """Generator to stream a file in chunks."""
    try:
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    except Exception:
        pass


def _stream_file_chunk(filepath: str, start: int, end: int, chunk_size: int = 8192):
    """Generator to stream a specific range of a file."""
    try:
        with open(filepath, 'rb') as f:
            f.seek(start)
            bytes_sent = 0
            while bytes_sent < (end - start + 1):
                to_read = min(chunk_size, (end - start + 1) - bytes_sent)
                chunk = f.read(to_read)
                if not chunk:
                    break
                bytes_sent += len(chunk)
                yield chunk
    except Exception:
        pass


def _rewrite_playlist_segments(content: str) -> str:
    """Append the episode selector to each segment line of an ffmpeg playlist.

    ffmpeg writes bare segment names, and a player resolves those against the
    playlist URL, discarding its query string. Without rewriting them the episode
    would be lost on every segment request.
    """
    suffix = _segment_query_suffix()
    if not suffix:
        return content
    out = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '?' not in stripped:
            out.append(stripped + suffix)
        else:
            out.append(line)
    return '\n'.join(out) + '\n'
