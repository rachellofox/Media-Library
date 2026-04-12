import json
import ipaddress
import os
import re
import subprocess
import unicodedata
import base64
import binascii
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import keyring
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, redirect, render_template, request, url_for

from tmdb_client import TmdbClient
from qb_search import DEFAULT_MIRROR_URLS, QBSearch
from quality import compare_quality, detect_quality, detect_quality_from_file
from storage import Storage
from trakt_client import TraktClient, TraktRequestError


__version__ = '0.1.0'


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'library.db')
POSTERS_DIR = os.path.join(BASE_DIR, 'static', 'posters')

QBT_NOVA_PATH = os.environ.get(
    'QBT_NOVA_PATH',
    os.path.expandvars(r'%LOCALAPPDATA%\\qBittorrent\\nova3'),
)
QBT_WEBUI_URL = os.environ.get('QBT_WEBUI_URL', '').strip().rstrip('/')
QBT_WEBUI_USERNAME = os.environ.get('QBT_WEBUI_USERNAME', '').strip()
QBT_WEBUI_PASSWORD = os.environ.get('QBT_WEBUI_PASSWORD', '').strip()
FFPROBE_EXE = os.environ.get('FFPROBE_EXE', 'ffprobe')
DISCOVER_COLLECTION_CACHE_HOURS = int(os.environ.get('DISCOVER_COLLECTION_CACHE_HOURS', '24'))
DISCOVER_WATCHLIST_CACHE_HOURS = int(os.environ.get('DISCOVER_WATCHLIST_CACHE_HOURS', '24'))
TRUSTED_RELEASE_GROUPS = (
    'qxr', 'tigole', 'ctrlhd', 'framestor', 'flux', 'ntb', 'rarbg', 'yts',
)
TRAKT_TOKEN_SETTING = 'trakt_oauth_token'
TRAKT_PROFILE_SETTING = 'trakt_oauth_profile'
TRAKT_DEVICE_SETTING = 'trakt_oauth_device'
TRAKT_SECRET_SERVICE = 'MediaLibrary'
TRAKT_SECRET_ACCOUNT = 'trakt_client_secret'
TMDB_SECRET_SERVICE = 'MediaLibrary'
TMDB_SECRET_ACCOUNT = 'tmdb_api_key'

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}
SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx'}


_ENGLISH_TAGS = {'en', 'eng', 'english'}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


def _load_json_setting(key: str) -> dict | None:
    raw = store.get_setting(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _save_json_setting(key: str, value: dict | None) -> None:
    if not value:
        store.delete_setting(key)
        return
    store.set_setting(key, json.dumps(value))


def _tmdb_api_key() -> str:
    try:
        stored_key = keyring.get_password(TMDB_SECRET_SERVICE, TMDB_SECRET_ACCOUNT)
    except Exception:
        stored_key = ''
    return (stored_key or '').strip()


def _set_tmdb_api_key(api_key: str) -> None:
    api_key = (api_key or '').strip()
    if not api_key:
        return
    keyring.set_password(TMDB_SECRET_SERVICE, TMDB_SECRET_ACCOUNT, api_key)


def _refresh_tmdb_client() -> None:
    global tmdb
    api_key = _tmdb_api_key()
    tmdb = TmdbClient(api_key=api_key) if api_key else None


def _trakt_client_id() -> str:
    return (store.get_setting('trakt_client_id') or '').strip()


def _trakt_client_secret() -> str:
    try:
        stored_secret = keyring.get_password(TRAKT_SECRET_SERVICE, TRAKT_SECRET_ACCOUNT)
    except Exception:
        stored_secret = ''
    return (stored_secret or '').strip()


def _set_trakt_client_secret(secret: str) -> None:
    secret = (secret or '').strip()
    if not secret:
        return
    keyring.set_password(TRAKT_SECRET_SERVICE, TRAKT_SECRET_ACCOUNT, secret)


def _delete_trakt_client_secret() -> None:
    try:
        keyring.delete_password(TRAKT_SECRET_SERVICE, TRAKT_SECRET_ACCOUNT)
    except Exception:
        pass


def _trakt_username() -> str:
    return (store.get_setting('trakt_username') or '').strip()


def _serialize_trakt_token(token: dict) -> dict:
    created_at = token.get('created_at')
    expires_in = int(token.get('expires_in') or 0)
    if created_at is not None:
        issued_at = datetime.fromtimestamp(int(created_at), tz=timezone.utc)
    else:
        issued_at = _utc_now()
    expires_at = issued_at + timedelta(seconds=expires_in)
    payload = dict(token)
    payload['expires_at'] = expires_at.isoformat()
    return payload


def _trakt_device_flow() -> dict | None:
    flow = _load_json_setting(TRAKT_DEVICE_SETTING)
    if not flow:
        return None
    expires_at = _parse_iso_datetime(flow.get('expires_at'))
    if expires_at and expires_at <= _utc_now():
        _save_json_setting(TRAKT_DEVICE_SETTING, None)
        return None
    return flow


def _clear_trakt_auth() -> None:
    _save_json_setting(TRAKT_TOKEN_SETTING, None)
    _save_json_setting(TRAKT_PROFILE_SETTING, None)
    _save_json_setting(TRAKT_DEVICE_SETTING, None)


def _refresh_trakt_token_if_needed() -> dict | None:
    token = _load_json_setting(TRAKT_TOKEN_SETTING)
    if not token:
        return None
    expires_at = _parse_iso_datetime(token.get('expires_at'))
    if expires_at and expires_at > (_utc_now() + timedelta(seconds=60)):
        return token
    client_id = _trakt_client_id()
    client_secret = _trakt_client_secret()
    if not client_id or not client_secret or not token.get('refresh_token'):
        _clear_trakt_auth()
        return None
    try:
        trakt = TraktClient(client_id=client_id, client_secret=client_secret)
        refreshed = _serialize_trakt_token(trakt.exchange_refresh_token(token['refresh_token']))
        _save_json_setting(TRAKT_TOKEN_SETTING, refreshed)
        return refreshed
    except Exception:
        _clear_trakt_auth()
        return None


def _connected_trakt_profile() -> dict | None:
    profile = _load_json_setting(TRAKT_PROFILE_SETTING)
    return profile or None


def _trakt_client() -> TraktClient | None:
    client_id = _trakt_client_id()
    token = _refresh_trakt_token_if_needed()
    if not client_id or not token or not token.get('access_token'):
        return None
    return TraktClient(
        client_id=client_id,
        client_secret=_trakt_client_secret(),
        access_token=token['access_token'],
    )


def _trakt_display_username() -> str:
    profile = _connected_trakt_profile() or {}
    return profile.get('slug') or profile.get('username') or _trakt_username()


def _trakt_context() -> dict:
    client_id = _trakt_client_id()
    client_secret = _trakt_client_secret()
    username = _trakt_username()
    profile = _connected_trakt_profile() or {}
    return {
        'oauth_available': bool(client_id and client_secret),
        'connected': bool(_refresh_trakt_token_if_needed()),
        'configured': bool(client_id and client_secret),
        'username': _trakt_display_username(),
        'profile': profile,
        'device_flow': _trakt_device_flow(),
        'client_id': client_id,
        'saved_username': username,
        'secret_configured': bool(client_secret),
    }


def _has_english_tag(tag: str) -> bool:
    return tag.strip().lower() in _ENGLISH_TAGS


def _ffprobe_embedded_english(video_path: str) -> bool:
    """Return True if the video file has an embedded English subtitle stream."""
    try:
        result = subprocess.run(
            [FFPROBE_EXE, '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-select_streams', 's', video_path],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
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


def _srt_is_english(path: str) -> bool:
    """Read the first ~2KB of text from an SRT file and return True if it appears to be English.

    Heuristic: extract only dialogue lines (skip index numbers and timestamp lines),
    then check whether the ratio of non-Latin characters is below 15%. Languages
    like Cyrillic, CJK, Arabic, Hebrew, Greek etc. will exceed this threshold.
    """
    _NON_LATIN_RANGES = [
        (0x0370, 0x03FF),   # Greek
        (0x0400, 0x04FF),   # Cyrillic
        (0x0500, 0x052F),   # Cyrillic Supplement
        (0x0590, 0x05FF),   # Hebrew
        (0x0600, 0x06FF),   # Arabic
        (0x0900, 0x097F),   # Devanagari (Hindi)
        (0x0E00, 0x0E7F),   # Thai
        (0x1100, 0x11FF),   # Hangul Jamo (Korean)
        (0x3000, 0x9FFF),   # CJK, Hiragana, Katakana, etc.
        (0xAC00, 0xD7AF),   # Hangul Syllables (Korean)
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
            1 for ch in sample
            if any(lo <= ord(ch) <= hi for lo, hi in _NON_LATIN_RANGES)
        )
        return (non_latin / len(sample)) < 0.15
    except Exception:
        return True  # On read error, don't discard the file


def scan_subtitles(media_path: str | None) -> str | None:
    """Return 'en' if English subtitles are found (embedded or external), else None."""
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

    # Check external subtitle files alongside any file in the folder tree.
    # Read the file content to confirm it's English rather than trusting the filename.
    base_dir = media_path if os.path.isdir(media_path) else os.path.dirname(media_path)
    try:
        for root, _dirs, files in os.walk(base_dir):
            for fname in files:
                _, ext = os.path.splitext(fname)
                if ext.lower() in SUBTITLE_EXTENSIONS:
                    if _srt_is_english(os.path.join(root, fname)):
                        return 'en'
    except PermissionError:
        pass

    return None


def _best_local_video_path(media_path: str | None) -> str | None:
    path = (media_path or '').strip()
    if not path:
        return None

    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
        return path if ext in VIDEO_EXTENSIONS else None

    if not os.path.isdir(path):
        return None

    candidates: list[tuple[int, str]] = []
    try:
        for root, _dirs, files in os.walk(path):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in VIDEO_EXTENSIONS:
                    continue
                full = os.path.join(root, fname)
                try:
                    size = os.path.getsize(full)
                except Exception:
                    size = 0
                candidates.append((size, full))
    except Exception:
        return None

    if not candidates:
        return None
    return max(candidates, key=lambda t: t[0])[1]


def _refresh_local_media_signals(media_id: int, media_path: str | None) -> None:
    best_video = _best_local_video_path(media_path)
    if best_video:
        quality = (
            detect_quality_from_file(best_video, ffprobe_exe=FFPROBE_EXE)
            or detect_quality(os.path.basename(best_video))
        )
        if quality:
            store.update_quality(media_id, quality)
    subs = scan_subtitles(media_path)
    if subs:
        store.update_subtitles(media_id, subs)


def _auto_finalize_qb_completed_downloads() -> None:
    if not _qbt_webui_enabled():
        return

    active_states = {'starting', 'handed_off', 'downloading'}
    done_states = {'uploading', 'stalledup', 'seeding', 'pausedup', 'forcedup', 'checkingup'}
    torrents = _qbt_webui_torrents_info()
    if not torrents:
        return

    for row in store.list_media_items():
        status = (row['download_status'] or '').strip().lower()
        if status not in active_states:
            continue
        source = (row['download_source'] or '').strip()
        torrent_hash = (row['download_torrent_hash'] or '').strip().upper()
        try:
            if source == 'qb_webui' and re.fullmatch(r'[0-9A-F]{40}', torrent_hash):
                torrent = _qbt_webui_torrent_info(torrent_hash)
            else:
                torrent = _qbt_match_torrent_for_item(dict(row), torrents=torrents)
        except Exception:
            continue
        if not torrent:
            continue

        qbt_state = (torrent.get('state') or '').lower()
        if qbt_state not in done_states:
            continue

        media_id = int(row['id'])
        effective_path = (row['path'] or '').strip()
        if not effective_path:
            torrent_content_path = (torrent.get('content_path') or '').strip()
            if torrent_content_path and os.path.exists(torrent_content_path):
                store.update_path(media_id, torrent_content_path)
                effective_path = torrent_content_path
        try:
            _refresh_local_media_signals(media_id, effective_path or None)
        except Exception:
            pass
        store.clear_download_state(media_id)


def cache_poster(key: str, remote_url: str, force_replace: bool = False) -> str:
    """Download a poster image and save it under static/posters/.

    Returns the local Flask static URL on success, or the original remote URL on failure.
    Skips download if a local copy already exists.
    """
    if not remote_url or not key:
        return remote_url or ''
    os.makedirs(POSTERS_DIR, exist_ok=True)
    ext = os.path.splitext(remote_url.split('?')[0])[-1] or '.jpg'
    filename = f'{key}{ext}'
    local_path = os.path.join(POSTERS_DIR, filename)
    if os.path.exists(local_path) and not force_replace:
        return f'/static/posters/{filename}'

    if force_replace:
        key_prefix = f'{key}.'
        try:
            for existing in os.listdir(POSTERS_DIR):
                if existing.startswith(key_prefix):
                    try:
                        os.remove(os.path.join(POSTERS_DIR, existing))
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        req = urllib.request.Request(remote_url, headers={'User-Agent': 'MediaLibrary/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        with open(local_path, 'wb') as fh:
            fh.write(data)
        return f'/static/posters/{filename}'
    except Exception:
        return remote_url


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


def scan_media_entries(folder_path: str, media_type: str) -> list[dict[str, str]]:
    """Return importable media entries from a configured library folder."""
    if not folder_path or not os.path.isdir(folder_path):
        return []

    entries: list[dict[str, str]] = []
    try:
        for entry in os.scandir(folder_path):
            if entry.name.startswith('.'):
                continue
            if entry.is_dir():
                entries.append({'name': entry.name, 'path': entry.path})
                continue
            if media_type == 'movie' and entry.is_file():
                _, ext = os.path.splitext(entry.name)
                if ext.lower() in VIDEO_EXTENSIONS:
                    entries.append({'name': entry.name, 'path': entry.path})
            elif media_type == 'tv' and entry.is_file():
                _, ext = os.path.splitext(entry.name)
                if ext.lower() in VIDEO_EXTENSIONS:
                    entries.append({'name': entry.name, 'path': entry.path})
    except PermissionError:
        return []

    return sorted(entries, key=lambda item: item['name'].lower())


def normalize_media_name(raw_name: str, media_type: str) -> tuple[str, int | None]:
    """Convert a filename or folder name into a cleaner IMDb search query."""
    base_name = raw_name.rstrip('/\\')
    stem, ext = os.path.splitext(base_name)
    if ext.lower() in VIDEO_EXTENSIONS:
        base_name = stem

    text = re.sub(r'[._]+', ' ', base_name)
    text = re.sub(r'\[[^\]]*\]', ' ', text)

    year = None
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if year_match:
        year = int(year_match.group(1))

    cleanup_patterns = [
        r'\bS\d{1,2}E\d{1,2}\b',
        r'\bSeason\s+\d+\b',
        r'\bComplete\b',
        r'\b(2160p|1440p|1080p|720p|480p|4k)\b',
        r'\b(BluRay|BRRip|BDRip|WEBRip|WEB-DL|HDRip|DVDRip|HDTV|REMUX)\b',
        r'\b(x264|x265|h264|h265|HEVC|AAC|DDP?\d?(?:\.\d)?|Atmos)\b',
        r'\b(YIFY|RARBG|ETHEL|PSA|Vyndros|CtrlHD)\b',
        r'\b(Proper|Repack|Extended|Unrated|Criterion|Multi(?:sub)?|Dual Audio)\b',
    ]
    for pattern in cleanup_patterns:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)

    if media_type == 'tv':
        text = re.sub(r'\bEpisode\s+\d+\b', ' ', text, flags=re.IGNORECASE)

    text = re.sub(r'\([^)]*\)', ' ', text)
    text = re.sub(r'[^A-Za-z0-9]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text, year


def _normalized_title_tokens(value: str | None) -> list[str]:
    s = unicodedata.normalize('NFKD', value or '')
    s = ''.join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    roman = {
        'viii': '8', 'vii': '7', 'vi': '6', 'iv': '4',
        'iii': '3', 'ii': '2', 'ix': '9', 'xi': '11', 'xii': '12',
    }
    parts = [roman.get(part, part) for part in s.split()]
    return [part for part in parts if part]


def _titles_likely_match(expected: str | None, candidate: str | None) -> bool:
    expected_tokens = _normalized_title_tokens(expected)
    candidate_tokens = _normalized_title_tokens(candidate)
    if not expected_tokens or not candidate_tokens:
        return False
    if expected_tokens == candidate_tokens:
        return True

    expected_set = set(expected_tokens)
    candidate_set = set(candidate_tokens)
    overlap = len(expected_set & candidate_set)
    expected_ratio = overlap / max(1, len(expected_set))
    candidate_ratio = overlap / max(1, len(candidate_set))

    # Require substantial overlap so franchise roots do not match specific sequels.
    if expected_ratio >= 0.75 and candidate_ratio >= 0.75:
        return True

    expected_joined = ' '.join(expected_tokens)
    candidate_joined = ' '.join(candidate_tokens)
    if expected_joined in candidate_joined or candidate_joined in expected_joined:
        return len(expected_tokens) <= 2 and len(candidate_tokens) <= 2

    return False


def choose_search_result(results, title: str, media_type: str, year: int | None):
    """Pick the most likely IMDb result for a local library entry."""

    def normalized(value: str | None) -> str:
        """Lowercase, strip punctuation, normalise roman numerals and collapse whitespace."""
        s = unicodedata.normalize('NFKD', value or '')
        s = ''.join(ch for ch in s if not unicodedata.combining(ch)).lower()
        s = re.sub(r'[^a-z0-9\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        # Convert common roman numerals to arabic so '2' matches 'ii', etc.
        _roman = {'viii': '8', 'vii': '7', 'vi': '6', 'iv': '4',
                  'iii': '3', 'ii': '2', 'ix': '9', 'xi': '11', 'xii': '12'}
        parts = s.split()
        parts = [_roman.get(p, p) for p in parts]
        return ' '.join(parts)

    normalized_title = normalized(title)
    query_tokens = set(normalized_title.split())

    filtered = [item for item in results if item.get('media_type') == media_type] or list(results)

    # Exact title + exact year
    if year is not None:
        for item in filtered:
            if abs((item.get('year') or 0) - year) <= 1 and normalized(item.get('title')) == normalized_title:
                return item

    # Exact title, any year
    for item in filtered:
        candidate = normalized(item.get('title'))
        if candidate == normalized_title:
            return item

    # Score-based fallback to avoid broad partial matches.
    def score(item: dict) -> float:
        candidate = normalized(item.get('title'))
        if not candidate:
            return -1.0

        candidate_tokens = set(candidate.split())
        if not candidate_tokens or not query_tokens:
            return -1.0

        overlap = len(query_tokens & candidate_tokens)
        recall = overlap / len(query_tokens)
        precision = overlap / len(candidate_tokens)
        phrase_bonus = 0.2 if (candidate in normalized_title or normalized_title in candidate) else 0.0

        score_val = (recall * 3.0) + (precision * 2.0) + phrase_bonus

        item_year = item.get('year')
        if year is not None and item_year:
            delta = abs(item_year - year)
            if delta <= 1:
                score_val += 1.2
            elif delta <= 2:
                score_val += 0.4
            else:
                score_val -= 0.6

        # Reject broad subset matches when query is clearly more specific.
        if (
            len(query_tokens) >= 4
            and len(candidate_tokens) <= 2
            and candidate_tokens.issubset(query_tokens)
            and not (year is not None and item_year and abs(item_year - year) <= 1)
        ):
            score_val -= 2.0

        return score_val

    ranked = sorted(filtered, key=score, reverse=True)
    if not ranked:
        return None

    best = ranked[0]
    return best if score(best) >= 1.5 else None


def import_media_from_paths(folder_path: str, media_type: str) -> int:
    """Import missing items from a configured media folder into the local library."""
    existing_items = store.list_media_items()
    existing_paths = {
        os.path.normcase(os.path.normpath(item['path']))
        for item in existing_items
        if item['path']
    }
    imported = 0

    for entry in scan_media_entries(folder_path, media_type):
        normalized_path = os.path.normcase(os.path.normpath(entry['path']))
        if normalized_path in existing_paths:
            continue

        title, year = normalize_media_name(entry['name'], media_type)
        if not title:
            continue

        query = title  # year in folder name can confuse TMDB ranking; search by title alone
        match = choose_search_result(tmdb.search(query, max_results=10) if tmdb else [], title, media_type, year)
        if not match:
            continue

        meta = tmdb.metadata_by_tmdb_id(match['tmdb_id'], match['media_type']) if tmdb else {}
        if not meta.get('imdb_id'):
            continue
        poster_url = cache_poster(meta.get('imdb_id') or entry['name'], meta.get('poster_url') or '') or meta.get('poster_url')
        store.add_media_item(
            imdb_id=meta['imdb_id'],
            tmdb_id=meta.get('tmdb_id'),
            title=meta['title'],
            year=meta['year'],
            media_type=meta['media_type'],
            collection_id=meta.get('collection_id'),
            collection_name=meta.get('collection_name'),
            current_quality=detect_quality_from_file(entry['path'], ffprobe_exe=FFPROBE_EXE) or detect_quality(entry['name']),
            path=entry['path'],
            poster_url=poster_url,
            synopsis=meta.get('synopsis'),
            actors=meta.get('actors'),
            rating=meta.get('rating'),
            subtitles=scan_subtitles(entry['path']),
        )
        existing_paths.add(normalized_path)
        imported += 1

    return imported


def import_from_configured_folders() -> int:
    movies_path = store.get_setting('movies_path') or ''
    tv_path = store.get_setting('tv_path') or ''
    imported = 0
    if movies_path:
        imported += import_media_from_paths(movies_path, 'movie')
    if tv_path:
        imported += import_media_from_paths(tv_path, 'tv')
    return imported


app = Flask(__name__)
store = Storage(DB_PATH)
tmdb = None
_refresh_tmdb_client()
qb = QBSearch(nova_path=QBT_NOVA_PATH)


def configured_mirror_urls() -> list[str]:
    raw = store.get_setting('mirror_urls') or ''
    urls = [line.strip() for line in raw.splitlines() if line.strip()]
    return urls or list(DEFAULT_MIRROR_URLS)


def _cache_meta_poster(cache_key: str, meta: dict, force_replace: bool = False) -> str | None:
    remote_url = meta.get('poster_url') or ''
    return cache_poster(cache_key, remote_url, force_replace=force_replace) or meta.get('poster_url')


def _torrent_candidates_for(meta: dict, limit: int = 25) -> list[dict]:
    title = (meta.get('title') or '').strip()
    year = meta.get('year')
    if not title:
        return []
    query = f'{title} {year}' if year else title
    try:
        qb.set_mirror_urls(configured_mirror_urls())
        rows = qb._run_search(query)
    except Exception:
        return []

    candidates = []
    for row in rows:
        quality = detect_quality(row.get('name') or '')
        name = row.get('name') or ''
        lowered_name = name.lower()
        trusted = any(group in lowered_name for group in TRUSTED_RELEASE_GROUPS)
        candidates.append({
            'name': name,
            'size': row.get('size') or '',
            'seeds': row.get('seeds') or '0',
            'leech': row.get('leech') or '0',
            'desc_link': row.get('desc_link') or '',
            'link': row.get('link') or '',
            'pub_date': row.get('pub_date') or '',
            'quality': quality,
            'trusted': trusted,
        })

    def _seed_count(item: dict) -> int:
        try:
            return int(item.get('seeds') or 0)
        except Exception:
            return 0

    preferred_quality = (store.get_setting('preferred_quality') or '2160p').lower()
    rank_map = {'480p': 1, '720p': 2, '1080p': 3, '1440p': 4, '2160p': 5, '4k': 5}

    def _quality_rank(item: dict) -> int:
        return rank_map.get((item.get('quality') or '').lower(), 0)

    def _meets_preferred(item: dict) -> int:
        return 1 if compare_quality(preferred_quality, item.get('quality')) >= 0 else 0

    def _trusted_rank(item: dict) -> int:
        return 1 if item.get('trusted') else 0

    candidates.sort(
        key=lambda i: (
            _meets_preferred(i),
            _quality_rank(i),
            _trusted_rank(i),
            _seed_count(i),
        ),
        reverse=True,
    )
    return candidates[:limit]


def _qbt_webui_enabled() -> bool:
    return bool(_qbt_webui_url())


def _qbt_webui_url() -> str:
    return (store.get_setting('qbt_webui_url') or QBT_WEBUI_URL or '').strip().rstrip('/')


def _qbt_webui_username() -> str:
    return (store.get_setting('qbt_webui_username') or QBT_WEBUI_USERNAME or '').strip()


def _qbt_webui_build_opener() -> urllib.request.OpenerDirector:
    cookie_jar = urllib.request.HTTPCookieProcessor()
    return urllib.request.build_opener(cookie_jar)


def _qbt_webui_try_login(opener: urllib.request.OpenerDirector) -> bool:
    qbt_url = _qbt_webui_url()
    qbt_username = _qbt_webui_username()
    if not qbt_url or not qbt_username or not QBT_WEBUI_PASSWORD:
        return False
    login_payload = urllib.parse.urlencode({
        'username': qbt_username,
        'password': QBT_WEBUI_PASSWORD,
    }).encode('utf-8')
    login_req = urllib.request.Request(
        f'{qbt_url}/api/v2/auth/login',
        data=login_payload,
        method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    try:
        with opener.open(login_req, timeout=15) as resp:
            login_body = resp.read().decode('utf-8', errors='replace').strip()
        return login_body == 'Ok.'
    except Exception:
        return False


def _qbt_webui_open(path: str, method: str = 'GET', data: bytes | None = None) -> bytes:
    qbt_url = _qbt_webui_url()
    if not qbt_url:
        raise RuntimeError('qbt_webui_not_configured')

    opener = _qbt_webui_build_opener()
    req = urllib.request.Request(
        f'{qbt_url}{path}',
        data=data,
        method=method,
        headers={'Content-Type': 'application/x-www-form-urlencoded'} if data is not None else {},
    )

    # First try direct call.
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.read()
    except Exception:
        pass

    # Fallback to explicit login only when creds are configured.
    if not _qbt_webui_try_login(opener):
        raise RuntimeError('qbt_auth_required_or_failed')
    with opener.open(req, timeout=20) as resp:
        return resp.read()


def _is_local_or_private_host(hostname: str | None) -> bool:
    host = (hostname or '').strip().lower()
    if not host:
        return False
    if host in {'localhost'}:
        return True
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_loopback or addr.is_private
    except ValueError:
        return host.endswith('.local')


def _sanitize_qbt_webui_url(raw: str) -> str | None:
    value = (raw or '').strip()
    if not value:
        return ''
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {'http', 'https'}:
        return None
    if not parsed.hostname or not _is_local_or_private_host(parsed.hostname):
        return None
    port = f':{parsed.port}' if parsed.port else ''
    return f'{parsed.scheme}://{parsed.hostname}{port}'.rstrip('/')


def _extract_btih_hash(download_url: str) -> str | None:
    if not download_url:
        return None

    parsed = urllib.parse.urlparse(download_url)
    if parsed.scheme.lower() != 'magnet':
        return None

    xt_values = urllib.parse.parse_qs(parsed.query).get('xt') or []
    for xt in xt_values:
        if not xt:
            continue
        lower_xt = xt.lower()
        marker = 'urn:btih:'
        pos = lower_xt.find(marker)
        if pos == -1:
            continue

        raw_hash = xt[pos + len(marker):].strip()
        if re.fullmatch(r'[0-9a-fA-F]{40}', raw_hash):
            return raw_hash.upper()

        # Some magnets use base32 info-hash (32 chars); convert to hex.
        if re.fullmatch(r'[A-Za-z2-7]{32}', raw_hash):
            try:
                return base64.b32decode(raw_hash.upper()).hex().upper()
            except (binascii.Error, ValueError):
                continue
    return None


def _qbt_webui_torrent_info(info_hash: str) -> dict | None:
    if not _qbt_webui_enabled():
        return None
    if not re.fullmatch(r'[0-9A-F]{40}', (info_hash or '').upper()):
        return None

    try:
        body = _qbt_webui_open(
            f'/api/v2/torrents/info?hashes={urllib.parse.quote(info_hash.upper())}',
            method='GET',
        ).decode('utf-8', errors='replace')
    except Exception:
        return None
    try:
        items = json.loads(body)
    except Exception:
        return None
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    return first if isinstance(first, dict) else None

def _qbt_webui_torrents_info() -> list[dict]:
    if not _qbt_webui_enabled():
        return []

    try:
        body = _qbt_webui_open('/api/v2/torrents/info?filter=all', method='GET').decode('utf-8', errors='replace')
    except Exception:
        return []
    try:
        items = json.loads(body)
    except Exception:
        return []
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _norm_match_text(value: str | None) -> str:
    text = unicodedata.normalize('NFKD', value or '').lower()
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _qbt_match_torrent_for_item(item: dict, torrents: list[dict] | None = None) -> dict | None:
    if torrents is None:
        torrents = _qbt_webui_torrents_info()
    if not torrents:
        return None

    title_norm = _norm_match_text(item.get('title') or '')
    year_str = str(item.get('year') or '').strip()
    path = (item.get('path') or '').strip()
    folder_norm = _norm_match_text(os.path.basename(path)) if path else ''

    best: tuple[int, dict] | None = None
    for t in torrents:
        t_name_norm = _norm_match_text(t.get('name') or '')
        t_save_norm = _norm_match_text(t.get('save_path') or '')
        t_content_norm = _norm_match_text(t.get('content_path') or '')

        score = 0
        if title_norm and title_norm in t_name_norm:
            score += 3
        if year_str and year_str in t_name_norm:
            score += 2
        if folder_norm and (folder_norm in t_content_norm or folder_norm in t_save_norm or folder_norm in t_name_norm):
            score += 4
        if score == 0:
            continue

        # Prefer active/most recently added torrents when scores tie.
        state = (t.get('state') or '').lower()
        if state in {'downloading', 'stalleddl', 'metadl', 'forceddl'}:
            score += 1
        if best is None or score > best[0]:
            best = (score, t)

    return best[1] if best else None


def _qbt_webui_add_download(download_url: str, save_path: str) -> bool:
    if not _qbt_webui_enabled():
        return False

    add_payload = urllib.parse.urlencode({
        'urls': download_url,
        'savepath': save_path,
        'autoTMM': 'false',
    }).encode('utf-8')
    try:
        add_body = _qbt_webui_open('/api/v2/torrents/add', method='POST', data=add_payload).decode('utf-8', errors='replace').strip()
    except Exception:
        return False

    # qBittorrent may return an empty body for successful submissions.
    return not add_body.lower().startswith('fails')


@app.route('/api/debug/qbt-status')
def debug_qbt_status():
    if not _qbt_webui_enabled():
        return jsonify({'ok': False, 'error': 'qbt_webui_not_configured'}), 409

    info_hash = (request.args.get('hash') or '').strip().upper()
    magnet = (request.args.get('magnet') or '').strip()

    if not info_hash and magnet:
        info_hash = _extract_btih_hash(magnet) or ''
    if not re.fullmatch(r'[0-9A-F]{40}', info_hash):
        return jsonify({'ok': False, 'error': 'missing_or_invalid_hash'}), 400

    try:
        torrent = _qbt_webui_torrent_info(info_hash)
    except Exception:
        return jsonify({'ok': False, 'error': 'qbt_query_failed'}), 502

    if not torrent:
        return jsonify({'ok': True, 'found': False, 'hash': info_hash})

    progress_raw = torrent.get('progress')
    try:
        progress = float(progress_raw)
    except Exception:
        progress = 0.0

    return jsonify({
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
    })


@app.route('/api/settings/qbt-test')
def settings_qbt_test():
    if not _qbt_webui_url():
        return jsonify({'ok': False, 'error': 'qbt_webui_not_configured'}), 409
    try:
        torrents = _qbt_webui_torrents_info()
    except Exception:
        return jsonify({'ok': False, 'error': 'qbt_connection_failed'}), 502
    return jsonify({'ok': True, 'reachable': True, 'torrents_seen': len(torrents)})


@app.route('/api/settings/tmdb-test', methods=['POST'])
def settings_tmdb_test():
    api_key = (request.form.get('tmdb_api_key') or '').strip() or _tmdb_api_key()
    if not api_key:
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 409
    try:
        client = TmdbClient(api_key=api_key)
        payload = client.ping() or {}
    except Exception:
        return jsonify({'ok': False, 'error': 'tmdb_connection_failed'}), 502
    return jsonify({'ok': True, 'reachable': True, 'has_images_config': bool((payload.get('images') or {}).get('base_url'))})


@app.route('/api/settings/trakt-test', methods=['POST'])
def settings_trakt_test():
    client_id = (request.form.get('trakt_client_id') or '').strip() or _trakt_client_id()
    client_secret = (request.form.get('trakt_client_secret') or '').strip() or _trakt_client_secret()
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
    return jsonify({
        'ok': True,
        'reachable': True,
        'verification_url': flow.get('verification_url', 'https://trakt.tv/activate'),
    })


def _discover_watchlist(limit: int = 16) -> list[dict]:
    trakt = _trakt_client()
    if not trakt:
        return []
    try:
        raw_items = trakt.watchlist_movies() + trakt.watchlist_shows()
    except Exception:
        return []

    # Keep cache aligned with the current Trakt watchlist set.
    watchlist_ids = {
        str(item.get('imdb_id'))
        for item in raw_items
        if item.get('imdb_id')
    }
    store.prune_watchlist_cache(watchlist_ids)

    cached = store.get_cached_watchlist_entries(DISCOVER_WATCHLIST_CACHE_HOURS)
    out = []
    seen = set()
    to_cache: list[dict] = []

    for item in raw_items:
        imdb_id = item.get('imdb_id')
        if not imdb_id or imdb_id in seen:
            continue
        seen.add(imdb_id)
        row = cached.get(str(imdb_id))
        if row:
            out.append({
                'imdb_id': imdb_id,
                'tmdb_id': row.get('tmdb_id'),
                'title': row.get('title') or item.get('title') or imdb_id,
                'year': row.get('year') or item.get('year'),
                'media_type': row.get('media_type') or item.get('media_type') or 'movie',
                'poster_url': row.get('poster_url'),
            })
        else:
            meta = tmdb.metadata_by_imdb_id(imdb_id) if tmdb else {}
            built = {
                'imdb_id': imdb_id,
                'tmdb_id': meta.get('tmdb_id'),
                'title': meta.get('title') or item.get('title') or imdb_id,
                'year': meta.get('year') or item.get('year'),
                'media_type': meta.get('media_type') or item.get('media_type') or 'movie',
                'poster_url': meta.get('poster_url'),
            }
            out.append(built)
            to_cache.append(built)
        if len(out) >= limit:
            break

    if to_cache:
        store.upsert_watchlist_cache_entries(to_cache)
    return out


def _discover_incomplete_collections() -> list[dict]:
    if not tmdb:
        return []

    ignored_title_ids = store.list_discover_ignored_title_ids()
    ignored_collection_ids = store.list_discover_ignored_collection_ids()

    def is_released(part: dict) -> bool:
        raw = (part.get('release_date') or '').strip()
        if not raw:
            return False
        try:
            released_on = date.fromisoformat(raw)
        except ValueError:
            return False
        return released_on <= date.today()

    grouped: dict[int, dict] = {}

    def normalized_title(value: str | None) -> str:
        s = (value or '').lower()
        s = re.sub(r'[^a-z0-9\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    for row in store.list_collection_items():
        item = dict(row)
        collection_id = item.get('collection_id')
        if not collection_id or collection_id in ignored_collection_ids:
            continue
        group = grouped.setdefault(collection_id, {
            'collection_id': collection_id,
            'collection_name': item.get('collection_name') or 'Collection',
            'owned_tmdb_ids': set(),
            'owned_title_years': set(),
            'owned_count': 0,
        })
        if item.get('tmdb_id'):
            group['owned_tmdb_ids'].add(item['tmdb_id'])
        group['owned_title_years'].add((normalized_title(item.get('title')), item.get('year')))
        group['owned_count'] += 1

    out = []
    for collection_id, group in grouped.items():
        parts = store.get_cached_collection_parts(collection_id, DISCOVER_COLLECTION_CACHE_HOURS)
        if parts is None:
            parts = tmdb.collection_parts(collection_id)
            if parts:
                store.set_cached_collection_parts(collection_id, parts)
        if not parts:
            continue
        missing = [
            part
            for part in parts
            if (
                part['tmdb_id'] not in group['owned_tmdb_ids']
                and (normalized_title(part.get('title')), part.get('year')) not in group['owned_title_years']
                and part['tmdb_id'] not in ignored_title_ids
                and is_released(part)
            )
        ]
        if not missing:
            continue
        out.append({
            'collection_id': collection_id,
            'collection_name': group['collection_name'],
            'owned_count': group['owned_count'],
            'total_count': len(parts),
            'missing': missing,
        })

    out.sort(key=lambda item: (item['total_count'] - item['owned_count'], item['collection_name'].lower()))
    return out


def backfill_discover_metadata() -> dict:
    if not tmdb:
        return {'updated': 0, 'skipped': 0, 'error': 'tmdb_not_configured'}

    updated = 0
    skipped = 0
    for item in store.list_media_items():
        try:
            meta = _fetch_best_metadata(item)
            if not meta.get('tmdb_id'):
                skipped += 1
                continue
            poster_url = _cache_meta_poster(item['imdb_id'] or str(item['id']), meta)
            store.update_metadata(
                media_id=item['id'],
                imdb_id=meta.get('imdb_id'),
                tmdb_id=meta.get('tmdb_id'),
                poster_url=poster_url,
                synopsis=meta.get('synopsis'),
                actors=meta.get('actors'),
                rating=meta.get('rating'),
                title=meta.get('title') or None,
                media_type=meta.get('media_type') or None,
                year=meta.get('year') or None,
                collection_id=meta.get('collection_id'),
                collection_name=meta.get('collection_name'),
            )
            updated += 1
        except Exception:
            skipped += 1
    return {'updated': updated, 'skipped': skipped}


@app.route('/')
def index():
    if not store.list_media_items():
        import_from_configured_folders()

    # Auto-clear only when qBittorrent itself reports completion.
    _auto_finalize_qb_completed_downloads()

    all_items = store.list_media_items()

    def with_flags(items):
        out = []
        for i in all_items if items is all_items else items:
            d = dict(i)
            p = d.get('path') or ''
            d['file_missing'] = bool(p) and not os.path.exists(p)
            out.append(d)
        return out

    all_flagged = with_flags(all_items)
    _active_dl = {'starting', 'handed_off', 'downloading'}
    local_flagged = [
        i for i in all_flagged
        if (i.get('path') or '').strip() or i.get('download_status') in _active_dl
    ]
    movies = [i for i in local_flagged if i['media_type'] == 'movie']
    tv_shows = [i for i in local_flagged if i['media_type'] == 'tv']
    favourites   = [i for i in all_flagged if i['favourite']]

    movies_path = store.get_setting('movies_path') or ''
    tv_path = store.get_setting('tv_path') or ''
    preferred_quality = store.get_setting('preferred_quality') or '2160p'
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
        qbt_webui_url=_qbt_webui_url(),
        status=request.args.get('status', ''),
        preferred_quality=preferred_quality,
        mirror_urls_text=mirror_urls_text,
        initial_section=initial_section,
    )


@app.route('/api/search-imdb')
def search_imdb():
    query = request.args.get('q', '').strip()
    results = tmdb.search(query) if (query and tmdb) else []
    library_tmdb_ids = {
        item['tmdb_id']
        for item in store.list_media_items()
        if item['tmdb_id'] is not None
    }
    payload = []
    for result in results:
        item = dict(result)
        item['in_library'] = item.get('tmdb_id') in library_tmdb_ids
        payload.append(item)
    return jsonify({'query': query, 'results': payload})


@app.route('/api/discover')
def discover_data():
    trakt = _trakt_context()
    return jsonify({
        'watchlist': _discover_watchlist(),
        'collections': _discover_incomplete_collections(),
        'trending': tmdb.trending() if tmdb else [],
        'trakt_configured': trakt['configured'],
        'trakt_connected': trakt['connected'],
        'tmdb_configured': bool(tmdb),
    })


@app.route('/api/discover/hero')
def discover_hero_data():
    if not tmdb:
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503

    tmdb_id_raw = request.args.get('tmdb_id', '').strip()
    media_type = request.args.get('media_type', '').strip().lower()
    if media_type not in {'movie', 'tv'}:
        return jsonify({'ok': False, 'error': 'invalid_media_type'}), 400
    try:
        tmdb_id = int(tmdb_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_tmdb_id'}), 400

    meta = tmdb.metadata_by_tmdb_id(tmdb_id, media_type)
    return jsonify({
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
            'rating': meta.get('rating'),
            'current_quality': None,
            'subtitles': None,
            'found': 0,
            'favourite': 0,
        },
    })


@app.route('/api/discover/add-and-search', methods=['POST'])
def discover_add_and_search():
    if not tmdb:
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503

    payload = request.get_json(silent=True) or {}
    tmdb_id_raw = str(request.form.get('tmdb_id') or payload.get('tmdb_id') or '').strip()
    media_type = str(request.form.get('media_type') or payload.get('media_type') or 'movie').strip().lower()
    if media_type not in {'movie', 'tv'}:
        return jsonify({'ok': False, 'error': 'invalid_media_type'}), 400

    try:
        tmdb_id = int(tmdb_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_tmdb_id'}), 400

    meta = tmdb.metadata_by_tmdb_id(tmdb_id, media_type)
    if not meta.get('imdb_id'):
        return jsonify({'ok': False, 'error': 'missing_imdb_id'}), 400

    store.add_media_item(
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
        rating=meta.get('rating'),
        subtitles=None,
    )
    row = store.get_media_item_by_imdb_id(meta['imdb_id'])
    media_id = row['id'] if row else None
    candidates = _torrent_candidates_for(meta)

    return jsonify({
        'ok': True,
        'media_id': media_id,
        'title': meta.get('title') or '',
        'year': meta.get('year'),
        'candidates': candidates,
    })


@app.route('/api/library/retry-download/<int:media_id>')
def library_retry_download(media_id: int):
    item = _ui_item_payload(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    status = (item['download_status'] or '').strip().lower()
    if status not in {'starting', 'handed_off', 'downloading'}:
        return jsonify({'ok': False, 'error': 'download_not_active'}), 409

    meta = {
        'title': item['title'] or item['imdb_id'] or '',
        'year': item['year'],
    }
    candidates = _torrent_candidates_for(meta)
    return jsonify({
        'ok': True,
        'media_id': int(item['id']),
        'title': item['title'] or item['imdb_id'] or 'Title',
        'candidates': candidates,
    })


@app.route('/api/discover/start-download', methods=['POST'])
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
            media_item = store.get_media_item(int(media_id_raw))
    except Exception:
        media_item = None

    if _qbt_webui_enabled() and media_item:
        media_type = media_item['media_type'] or 'movie'
        target_setting = 'tv_path' if media_type == 'tv' else 'movies_path'
        target_path = (store.get_setting(target_setting) or '').strip()
        if target_path:
            try:
                store.set_download_state(
                    media_item_id=int(media_item['id']),
                    status='starting',
                    source='qb_webui',
                    message='Submitting to qBittorrent',
                )
                submitted = _qbt_webui_add_download(launch, target_path)
                if submitted:
                    torrent_hash = _extract_btih_hash(launch)
                    store.set_download_state(
                        media_item_id=int(media_item['id']),
                        status='downloading',
                        source='qb_webui',
                        message=f'Destination: {target_path}',
                        torrent_hash=torrent_hash,
                    )
                    return jsonify({
                        'ok': True,
                        'mode': 'qbittorrent',
                        'save_path': target_path,
                        'section': 'tv' if media_type == 'tv' else 'movies',
                        'torrent_hash': torrent_hash,
                    })
            except Exception:
                pass

    # Fallback: frontend opens the URL and lets OS/client handle destination.
    if media_item:
        store.set_download_state(
            media_item_id=int(media_item['id']),
            status='handed_off',
            source='external_client',
            message='Sent to torrent client',
        )
    return jsonify({'ok': True, 'mode': 'fallback', 'launch_url': launch, 'section': 'movies'})


@app.route('/api/library/download-progress/<int:media_id>')
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
    if _qbt_webui_enabled():
        try:
            if source == 'qb_webui' and re.fullmatch(r'[0-9A-F]{40}', torrent_hash):
                torrent = _qbt_webui_torrent_info(torrent_hash)
            else:
                torrent = _qbt_match_torrent_for_item(item)
        except Exception:
            torrent = None
        if torrent:
            progress_raw = torrent.get('progress')
            try:
                progress = float(progress_raw)
            except Exception:
                progress = 0.0
            qbt_state = (torrent.get('state') or '').lower()
            done_states = {'uploading', 'stalledup', 'seeding', 'pausedup', 'forcedup', 'checkingup'}
            return jsonify({
                'ok': True,
                'source': 'qbittorrent',
                'state': qbt_state,
                'progress': progress,
                'progress_percent': round(progress * 100, 2),
                'eta': torrent.get('eta'),
                'name': torrent.get('name') or '',
                'dlspeed': torrent.get('dlspeed'),
                'is_complete': qbt_state in done_states,
            })
        if source == 'qb_webui' and torrent_hash:
            return jsonify({'ok': True, 'source': 'qbittorrent', 'state': 'queued',
                            'progress': 0.0, 'progress_percent': 0.0, 'is_complete': False})

    return jsonify({'ok': True, 'source': source or 'external', 'state': status,
                    'progress': None, 'progress_percent': None, 'is_complete': False})


@app.route('/api/library/mark-downloaded/<int:media_id>', methods=['POST'])
def library_mark_downloaded(media_id: int):
    item = store.get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    store.clear_download_state(media_id)

    # Run a targeted folder scan so quality/subtitles are picked up immediately.
    _refresh_local_media_signals(media_id, item['path'])

    payload = _ui_item_payload(media_id) or {'id': media_id}
    return jsonify({'ok': True, 'item': payload})


@app.route('/api/discover/ignore-title', methods=['POST'])
def discover_ignore_title():
    payload = request.get_json(silent=True) or {}
    tmdb_id_raw = request.form.get('tmdb_id') or payload.get('tmdb_id')
    collection_id_raw = request.form.get('collection_id') or payload.get('collection_id')
    title = (request.form.get('title') or payload.get('title') or '').strip() or None
    collection_name = (request.form.get('collection_name') or payload.get('collection_name') or '').strip() or None
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

    store.ignore_discover_title(
        tmdb_id=tmdb_id,
        collection_id=collection_id,
        title=title,
        collection_name=collection_name,
    )
    return jsonify({'ok': True, 'tmdb_id': tmdb_id, 'collection_id': collection_id})


@app.route('/api/discover/ignore-collection', methods=['POST'])
def discover_ignore_collection():
    payload = request.get_json(silent=True) or {}
    collection_id_raw = request.form.get('collection_id') or payload.get('collection_id')
    collection_name = (request.form.get('collection_name') or payload.get('collection_name') or '').strip() or None
    try:
        collection_id = int(collection_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_collection_id'}), 400

    store.ignore_discover_collection(collection_id=collection_id, collection_name=collection_name)
    return jsonify({'ok': True, 'collection_id': collection_id})


@app.route('/api/discover/ignored')
def discover_ignored():
    return jsonify({
        'titles': store.list_discover_ignored_titles(),
        'collections': store.list_discover_ignored_collections(),
    })


@app.route('/api/discover/unignore-title', methods=['POST'])
def discover_unignore_title():
    payload = request.get_json(silent=True) or {}
    tmdb_id_raw = request.form.get('tmdb_id') or payload.get('tmdb_id')
    try:
        tmdb_id = int(tmdb_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_tmdb_id'}), 400

    store.unignore_discover_title(tmdb_id)
    return jsonify({'ok': True, 'tmdb_id': tmdb_id})


@app.route('/api/discover/unignore-collection', methods=['POST'])
def discover_unignore_collection():
    payload = request.get_json(silent=True) or {}
    collection_id_raw = request.form.get('collection_id') or payload.get('collection_id')
    try:
        collection_id = int(collection_id_raw)
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid_collection_id'}), 400

    store.unignore_discover_collection(collection_id)
    return jsonify({'ok': True, 'collection_id': collection_id})


@app.route('/api/discover/unignore-all-titles', methods=['POST'])
def discover_unignore_all_titles():
    store.unignore_all_discover_titles()
    return jsonify({'ok': True})


@app.route('/api/discover/unignore-all-collections', methods=['POST'])
def discover_unignore_all_collections():
    store.unignore_all_discover_collections()
    return jsonify({'ok': True})


@app.route('/add', methods=['POST'])
def add():
    tmdb_id = request.form['tmdb_id']
    media_type = request.form.get('media_type') or 'movie'
    current_quality = request.form.get('current_quality') or None
    path = request.form.get('path') or None

    meta = tmdb.metadata_by_tmdb_id(tmdb_id, media_type)
    if not meta.get('imdb_id'):
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'missing_imdb_id'}), 400
        return redirect(url_for('index', section=request.form.get('return_section', 'discover'), status='add_failed'))

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
    outcome = qb.check_for_higher_quality(
        title=item['title'],
        year=item['year'],
        current_quality=item['current_quality'],
        preferred_quality=store.get_setting('preferred_quality') or '2160p',
    )

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


def _fetch_best_metadata(item) -> dict:
    """Return TMDB metadata for a library item.

    Strategy:
    1. If the stored imdb_id is a valid title ID (starts with 'tt'), try /find.
    2. If that yields no poster (bad/missing ID), fall back to a text search
       using the folder/file name from the stored path.
    """
    imdb_id = (item['imdb_id'] or '').strip()
    media_type = item['media_type'] or 'movie'

    # Derive a preferred local title/year anchor first.
    fallback_title: str | None = None
    year: int | None = item['year'] if isinstance(item['year'], int) else None
    if item['path']:
        basename = os.path.basename(item['path'].rstrip('/\\'))
        fallback_title, parsed_year = normalize_media_name(basename, media_type)
        # Local folder/file naming should win over stale DB year when re-resolving metadata.
        if parsed_year:
            year = parsed_year
    if not fallback_title and item['title'] and not item['title'].startswith('tt'):
        fallback_title = item['title']

    meta: dict = {}
    if imdb_id.startswith('tt'):
        meta = tmdb.metadata_by_imdb_id(imdb_id)

    if meta.get('poster_url'):
        same_type = (meta.get('media_type') or media_type) == media_type
        expected_title = fallback_title or item.get('title')
        title_matches = _titles_likely_match(expected_title, meta.get('title')) if expected_title else True
        year_matches = (
            year is None
            or meta.get('year') is None
            or abs(int(meta.get('year')) - int(year)) <= 1
        )
        if same_type and title_matches and year_matches:
            return meta

    if not fallback_title:
        return meta

    def _pick_match(query: str):
        results = tmdb.search(query, max_results=15)
        return choose_search_result(results, fallback_title, media_type, year)

    def _year_distance(candidate: dict | None) -> int:
        if year is None or not candidate or not candidate.get('year'):
            return 999
        return abs(int(candidate.get('year')) - int(year))

    match = _pick_match(fallback_title)

    # Short titles are often ambiguous on TMDB (e.g. TAR); retry with year.
    token_count = len(_normalized_title_tokens(fallback_title))
    if year is not None:
        precise_results = tmdb.search_precise(fallback_title, media_type, year=year, max_results=15)
        precise_match = choose_search_result(precise_results, fallback_title, media_type, year)
        by_year = _pick_match(f'{fallback_title} {year}')
        if precise_match and (
            match is None
            or _year_distance(precise_match) < _year_distance(match)
            or (token_count <= 2 and _year_distance(match) > 1)
        ):
            match = precise_match
        elif by_year and (
            match is None
            or _year_distance(by_year) < _year_distance(match)
            or (token_count <= 2 and _year_distance(match) > 1)
        ):
            match = by_year

    if not match:
        return meta

    return tmdb.metadata_by_tmdb_id(match['tmdb_id'], match['media_type'])


def _ui_item_payload(media_id: int) -> dict | None:
    for row in store.list_media_items():
        if int(row['id']) != int(media_id):
            continue
        payload = dict(row)
        path = payload.get('path') or ''
        payload['file_missing'] = bool(path) and not os.path.exists(path)
        return payload
    return None


@app.route('/refresh/<int:media_id>', methods=['POST'])
def refresh_metadata(media_id: int):
    item = store.get_media_item(media_id)
    if not item:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        return redirect(url_for('index'))
    meta = _fetch_best_metadata(item)
    poster_key = meta.get('imdb_id') or item['imdb_id'] or str(media_id)
    poster_url = cache_poster(poster_key, meta.get('poster_url') or '', force_replace=True) or meta.get('poster_url')
    store.update_metadata(
        media_id=media_id,
        imdb_id=meta.get('imdb_id'),
        tmdb_id=meta.get('tmdb_id'),
        poster_url=poster_url,
        synopsis=meta.get('synopsis'),
        actors=meta.get('actors'),
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
    if not tmdb:
        return redirect(url_for('index', status='tmdb_not_configured'))
    force_refresh = request.form.get('force') == '1'
    all_items = store.list_media_items()
    refreshed = 0
    for item in all_items:
        try:
            # Skip items that already have complete metadata
            title_ok = item['title'] and not item['title'].startswith('tt') and not item['title'].startswith('nm') and item['title'] != '/spotlight/'
            if not force_refresh and title_ok and item['poster_url'] and item['synopsis'] and item['year']:
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
        # Find the video file: path may be a direct file or a folder (possibly with subfolders for TV)
        target = None
        if os.path.isfile(path):
            target = path
        elif os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                for fname in files:
                    _, ext = os.path.splitext(fname)
                    if ext.lower() in VIDEO_EXTENSIONS:
                        target = os.path.join(root, fname)
                        break
                if target:
                    break
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
    items = [i for i in all_items if i['media_type'] == media_type] if media_type else list(all_items)

    qb.set_mirror_urls(configured_mirror_urls())

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
        except Exception:
            continue

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
                meta = tmdb.metadata_by_imdb_id(item['imdb_id']) if tmdb else {}
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


@app.route('/api/trakt/connect/start', methods=['POST'])
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


@app.route('/api/trakt/connect/poll', methods=['POST'])
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
            return jsonify({'ok': True, 'status': 'pending', 'interval': int(flow.get('interval') or 5) + (5 if exc.code == 'slow_down' else 0)})
        if exc.code in {'expired', 'denied', 'already_used', 'not_found'}:
            _save_json_setting(TRAKT_DEVICE_SETTING, None)
            return jsonify({'ok': False, 'error': f'trakt_oauth_{exc.code}'}), 400
        return jsonify({'ok': False, 'error': f'trakt_{exc.code}'}), 502


@app.route('/api/trakt/disconnect', methods=['POST'])
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


@app.route('/settings', methods=['POST'])
def save_settings():
    prev_movies = (store.get_setting('movies_path') or '').strip()
    prev_tv = (store.get_setting('tv_path') or '').strip()
    prev_trakt_client_id = _trakt_client_id()
    prev_trakt_secret = _trakt_client_secret()

    movies_path = request.form.get('movies_path')
    tv_path = request.form.get('tv_path')
    preferred_quality = request.form.get('preferred_quality')
    mirror_urls_raw = request.form.get('mirror_urls')
    qbt_webui_url_raw = request.form.get('qbt_webui_url')
    tmdb_api_key_raw = request.form.get('tmdb_api_key')
    trakt_client_id_raw = request.form.get('trakt_client_id')
    trakt_username_raw = request.form.get('trakt_username')
    trakt_client_secret_raw = request.form.get('trakt_client_secret')

    if movies_path is not None:
        store.set_setting('movies_path', movies_path.strip())
    if tv_path is not None:
        store.set_setting('tv_path', tv_path.strip())
    if preferred_quality is not None:
        store.set_setting('preferred_quality', preferred_quality.strip() or '2160p')
    if mirror_urls_raw is not None:
        mirror_urls = [line.strip().rstrip('/') for line in mirror_urls_raw.splitlines() if line.strip()]
        store.set_setting('mirror_urls', '\n'.join(mirror_urls))

    if qbt_webui_url_raw is not None:
        sanitized_url = _sanitize_qbt_webui_url(qbt_webui_url_raw)
        if sanitized_url is None:
            return redirect(url_for('index', section='settings', status='qbt_url_invalid'))
        store.set_setting('qbt_webui_url', sanitized_url)
    if tmdb_api_key_raw is not None and tmdb_api_key_raw.strip():
        _set_tmdb_api_key(tmdb_api_key_raw)
        _refresh_tmdb_client()
    if trakt_client_id_raw is not None:
        store.set_setting('trakt_client_id', trakt_client_id_raw.strip())
    if trakt_username_raw is not None:
        store.set_setting('trakt_username', trakt_username_raw.strip())
    if trakt_client_secret_raw is not None and trakt_client_secret_raw.strip():
        _set_trakt_client_secret(trakt_client_secret_raw)

    changed_client_id = trakt_client_id_raw is not None and trakt_client_id_raw.strip() != prev_trakt_client_id
    changed_client_secret = trakt_client_secret_raw is not None and trakt_client_secret_raw.strip() and trakt_client_secret_raw.strip() != prev_trakt_secret
    if changed_client_id or changed_client_secret:
        _clear_trakt_auth()

    qb.set_mirror_urls(configured_mirror_urls())
    new_movies = (store.get_setting('movies_path') or '').strip()
    new_tv = (store.get_setting('tv_path') or '').strip()
    if new_movies != prev_movies or new_tv != prev_tv:
        imported = import_from_configured_folders()
        return redirect(url_for('index', section='settings', status=f'settings_saved_{imported}'))
    return redirect(url_for('index', section='settings', status='settings_saved'))


if __name__ == '__main__':
    store.initialize()
    app.run(debug=True, port=5100)
