"""Finding subtitles, and getting them into a form the browser will accept.

Subtitles arrive three ways and all three have to be handled: sidecar files next
to the video, tracks embedded in the container, and whatever the fetcher
downloaded. Browsers only take WebVTT, so SubRip is converted on the way out and
embedded tracks are extracted to VTT on demand.

Deciding which track is English is the awkward part. The language tag is often
missing or wrong, so a sidecar with no usable tag is sampled and its text
inspected rather than trusted.

Pure functions over files: nothing here reads the database or calls TMDB.
"""

import json
import os
import subprocess

from medialibrary.config import FFMPEG_EXE, FFPROBE_EXE, HLS_CACHE_DIR
from medialibrary.identify import VIDEO_EXTENSIONS
from medialibrary.playback import _playback_cache_key

SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx'}

_ENGLISH_TAGS = {'en', 'eng', 'english'}


def _has_english_tag(tag: str) -> bool:
    return tag.strip().lower() in _ENGLISH_TAGS


def _ffprobe_embedded_english(video_path: str) -> bool:
    """Return True if the video file has an embedded English subtitle stream."""
    try:
        result = subprocess.run(
            [
                FFPROBE_EXE,
                '-v',
                'quiet',
                '-print_format',
                'json',
                '-show_streams',
                '-select_streams',
                's',
                video_path,
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        for stream in data.get('streams', []):
            lang = (stream.get('tags') or {}).get('language', '')
            if _has_english_tag(lang):
                return True
    except Exception:
        pass
    return False


def _ffprobe_has_embedded_subtitles(video_path: str) -> bool:
    """Return True if the video file has any embedded subtitle stream."""
    try:
        result = subprocess.run(
            [
                FFPROBE_EXE,
                '-v',
                'quiet',
                '-print_format',
                'json',
                '-show_streams',
                '-select_streams',
                's',
                video_path,
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        return bool(data.get('streams'))
    except Exception:
        return False


def _srt_is_english(path: str) -> bool:
    """Read the first ~2KB of text from an SRT file and return True if it appears to be English.

    Heuristic: extract only dialogue lines (skip index numbers and timestamp lines),
    then check whether the ratio of non-Latin characters is below 15%. Languages
    like Cyrillic, CJK, Arabic, Hebrew, Greek etc. will exceed this threshold.
    """
    _NON_LATIN_RANGES = [
        (0x0370, 0x03FF),  # Greek
        (0x0400, 0x04FF),  # Cyrillic
        (0x0500, 0x052F),  # Cyrillic Supplement
        (0x0590, 0x05FF),  # Hebrew
        (0x0600, 0x06FF),  # Arabic
        (0x0900, 0x097F),  # Devanagari (Hindi)
        (0x0E00, 0x0E7F),  # Thai
        (0x1100, 0x11FF),  # Hangul Jamo (Korean)
        (0x3000, 0x9FFF),  # CJK, Hiragana, Katakana, etc.
        (0xAC00, 0xD7AF),  # Hangul Syllables (Korean)
    ]
    try:
        import re as _re

        _ts = _re.compile(r'^\d+$|^\d{2}:\d{2}')
        text = []
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line and not _ts.match(line):
                    text.append(line)
                if sum(len(t) for t in text) >= 2000:
                    break
        sample = ' '.join(text)
        if not sample:
            return True  # Empty file — assume English
        non_latin = sum(
            1 for ch in sample if any(lo <= ord(ch) <= hi for lo, hi in _NON_LATIN_RANGES)
        )
        return (non_latin / len(sample)) < 0.15
    except Exception:
        return True  # On read error, don't discard the file


def scan_subtitles(media_path: str | None) -> str | None:
    """Return subtitle status string when subtitles are found, else None."""
    if not media_path:
        return None

    # Collect all video files under the path
    video_files = []
    if os.path.isfile(media_path):
        video_files = [media_path]
    elif os.path.isdir(media_path):
        for root, _dirs, files in os.walk(media_path):
            for fname in files:
                if os.path.splitext(fname)[1].lower() in VIDEO_EXTENSIONS:
                    video_files.append(os.path.join(root, fname))

    # Check embedded subtitles via ffprobe
    for vf in video_files:
        if _ffprobe_embedded_english(vf):
            return 'en'

    # Some files have valid embedded subtitle tracks but missing language tags.
    # Treat these as available subtitles to avoid false "missing subtitles" states.
    for vf in video_files:
        if _ffprobe_has_embedded_subtitles(vf):
            return 'embedded'

    # Check external subtitle files alongside any file in the folder tree.
    # Read the file content to confirm it's English rather than trusting the filename.
    base_dir = media_path if os.path.isdir(media_path) else os.path.dirname(media_path)
    try:
        for root, _dirs, files in os.walk(base_dir):
            for fname in files:
                _, ext = os.path.splitext(fname)
                if ext.lower() in SUBTITLE_EXTENSIONS and _srt_is_english(
                    os.path.join(root, fname)
                ):
                    return 'en'
    except PermissionError:
        pass

    return None


def _find_video_file(media_path: str | None) -> str | None:
    """Locate the actual video file from a media_path (file or folder).

    Returns the best (largest) video file, or None if not found.
    """
    if not media_path or not os.path.exists(media_path):
        return None

    if os.path.isfile(media_path):
        _, ext = os.path.splitext(media_path)
        return media_path if ext.lower() in VIDEO_EXTENSIONS else None

    if os.path.isdir(media_path):
        candidates = []
        try:
            for root, _dirs, files in os.walk(media_path):
                for fname in files:
                    _, ext = os.path.splitext(fname)
                    if ext.lower() in VIDEO_EXTENSIONS:
                        full = os.path.join(root, fname)
                        try:
                            size = os.path.getsize(full)
                            candidates.append((size, full))
                        except Exception:
                            pass
        except Exception:
            return None

        if not candidates:
            return None
        return max(candidates, key=lambda t: t[0])[1]

    return None


# Cache to store subtitle metadata during session
# Keys: (media_id, episode_key) — episode_key is '' for a movie. Two episodes
# of one show share a media_id, and each request to /video/<id> repopulates
# whichever key its own ?episode= names; keying on media_id alone let a
# request for episode 2's subtitle list overwrite what episode 1 had just
# populated, so a track fetch that arrived after could be served the wrong
# episode's mapping — same class of bug as playback_positions before it
# gained an episode key.
# Values: list of subtitle dicts
_subtitle_cache: dict[tuple[int, str], list[dict]] = {}

# Subtitle codecs that are text and can be converted to WebVTT. dvd_subtitle,
# hdmv_pgs_subtitle and dvb_subtitle are bitmaps — a rendered image per line,
# not text — and ffmpeg's `-c:s webvtt` has nothing to convert; it fails every
# time. Offering them anyway is how a real X-Men rip's CC menu listed six
# tracks, three of them (the DVD-sourced French, Spanish and English) always
# failing extraction with no indication why: the browser just never receives
# the track, which looks identical to "subtitles do not work".
_TEXT_SUBTITLE_CODECS = {'subrip', 'srt', 'ass', 'ssa', 'mov_text', 'webvtt', 'text'}


def _probe_embedded_subtitles(video_file: str) -> list[dict]:
    """Return embedded subtitle stream metadata from a video file.

    Bitmap subtitle streams are left out entirely — see _TEXT_SUBTITLE_CODECS —
    rather than offered and left to fail when picked.
    """
    results: list[dict] = []
    try:
        probe = subprocess.run(
            [
                FFPROBE_EXE,
                '-v',
                'quiet',
                '-print_format',
                'json',
                '-show_streams',
                video_file,
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20,
        )
        if probe.returncode != 0:
            return results
        data = json.loads(probe.stdout or '{}')
        for stream in data.get('streams', []):
            if stream.get('codec_type') != 'subtitle':
                continue
            if (stream.get('codec_name') or '').lower() not in _TEXT_SUBTITLE_CODECS:
                continue
            tags = stream.get('tags') or {}
            lang = (tags.get('language') or 'und').lower()
            title = tags.get('title') or f'Embedded {lang.upper()}'
            stream_index = stream.get('index')
            if stream_index is None:
                continue
            results.append(
                {
                    'stream_index': int(stream_index),
                    'lang': 'en' if lang in {'en', 'eng', 'english'} else lang,
                    'name': title,
                }
            )
    except Exception:
        return []
    return results


def _srt_to_vtt(content: str) -> str:
    """Convert SRT content to WebVTT for browser subtitle tracks."""
    lines = content.splitlines()
    out = ['WEBVTT', '']
    for line in lines:
        out.append(line.replace(',', '.') if '-->' in line else line)
    out.append('')
    return '\n'.join(out)


def _extract_embedded_subtitle_to_vtt(video_file: str, stream_index: int, out_path: str) -> bool:
    """Extract an embedded subtitle stream to a VTT file using FFmpeg."""
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        cmd = [
            FFMPEG_EXE,
            '-i',
            video_file,
            '-map',
            f'0:{stream_index}',
            '-c:s',
            'webvtt',
            '-y',
            out_path,
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        return proc.returncode == 0 and os.path.isfile(out_path)
    except Exception:
        return False


def _find_subtitle_files(
    media_path: str | None, media_id: int | None = None, episode_key: str = ''
) -> list[dict]:
    """Find all subtitle files (.srt, .vtt, etc.) near a video file.

    Returns a list of dicts: [{'name': 'English', 'lang': 'en', 'index': 0}]
    Caches file paths keyed by (media_id, episode_key) for later serving via
    API — `episode_key` is the same `?episode=` selector the player already
    sends, so two episodes of one show never share a cache slot.
    """
    video_file = _find_video_file(media_path)
    if not video_file:
        return []

    video_dir = os.path.dirname(video_file)
    video_stem = os.path.splitext(os.path.basename(video_file))[0]

    subtitles = []
    entries = []
    lang_map = {
        'srt': 'en',
        'vtt': 'en',
        'ass': 'en',
        'ssa': 'en',
        'sub': 'en',
        'idx': 'en',
        'sup': 'en',
    }

    try:
        for fname in os.listdir(video_dir):
            _, ext = os.path.splitext(fname)
            if ext.lower().lstrip('.') not in lang_map:
                continue
            # Match subtitles with similar stem (e.g., "Movie.srt", "Movie.en.srt")
            if fname.lower().startswith(video_stem.lower()):
                full_path = os.path.join(video_dir, fname)
                index = len(subtitles)
                _, ext_lower = os.path.splitext(full_path)
                ext_lower = ext_lower.lower()
                subtitles.append(
                    {
                        'name': 'English',
                        'lang': 'en',
                        'index': index,
                    }
                )
                entries.append(
                    {
                        'type': 'external',
                        'path': full_path,
                        'ext': ext_lower,
                    }
                )
    except Exception:
        pass

    # Include embedded subtitle streams as additional CC tracks.
    try:
        embedded = _probe_embedded_subtitles(video_file)
        for sub in embedded:
            index = len(subtitles)
            subtitles.append(
                {
                    'name': sub.get('name') or 'Embedded Subtitle',
                    'lang': sub.get('lang') or 'und',
                    'index': index,
                }
            )
            entries.append(
                {
                    'type': 'embedded',
                    'video_file': video_file,
                    'stream_index': sub.get('stream_index'),
                    'path': os.path.join(
                        HLS_CACHE_DIR,
                        _playback_cache_key(media_id) if media_id else 'tmp',
                        f'subtitle_{sub.get("stream_index")}.vtt',
                    ),
                    'ext': '.vtt',
                }
            )
    except Exception:
        pass

    # Cache the file paths if media_id provided
    if media_id and entries:
        _subtitle_cache[(media_id, episode_key or '')] = entries

    return subtitles
