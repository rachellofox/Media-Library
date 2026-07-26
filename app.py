import hashlib
import json
import ipaddress
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import unicodedata
import base64
import binascii
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import keyring
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, redirect, render_template, request, session, url_for, Response
from werkzeug.security import check_password_hash, generate_password_hash

from tmdb_client import TmdbClient
from episode_match import names_other_show
from naming import canonical_paths, canonical_stem
from qb_search import DEFAULT_MIRROR_URLS, QBSearch, SearchEngineError
from quality import compare_quality, detect_quality, detect_quality_from_file
from storage import Storage
from trakt_client import TraktClient, TraktRequestError

try:
    from send2trash import send2trash
except ImportError:  # pragma: no cover - optional until requirements installed
    send2trash = None


__version__ = '0.1.0'


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'library.db')
POSTERS_DIR = os.path.join(BASE_DIR, 'static', 'posters')
HLS_CACHE_DIR = os.path.join(BASE_DIR, 'tmp', 'hls')

# Poster choices are only accepted from TMDB's own image host, so a crafted
# request cannot make the server fetch an arbitrary URL.
TMDB_IMAGE_PREFIX = 'https://image.tmdb.org/t/p/'

DEFAULT_SERVER_PORT = 5100
LOOPBACK_HOST = '127.0.0.1'
ALL_INTERFACES_HOST = '0.0.0.0'

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
FFPROBE_EXE = os.environ.get('FFPROBE_EXE', 'ffprobe')
FFMPEG_EXE = os.environ.get('FFMPEG_EXE', 'ffmpeg')
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
AUTH_SECRET_SERVICE = 'MediaLibrary'
AUTH_SECRET_ACCOUNT = 'session_secret_key'

SESSION_LIFETIME_DAYS = 30
AUTH_MAX_ATTEMPTS = 5
AUTH_LOCKOUT = timedelta(minutes=5)

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}
# Size floor for treating a video as a feature film rather than a sample or
# extra, used when deciding whether a folder holds several distinct films.
PACK_MIN_FEATURE_BYTES = 300 * 1024 * 1024
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


def _session_secret_key() -> str:
    """Signing key for session cookies, stable across restarts.

    Kept in the OS keyring like the other secrets. A regenerated key would
    silently sign everyone out, so it is created once and reused.
    """
    try:
        stored = keyring.get_password(AUTH_SECRET_SERVICE, AUTH_SECRET_ACCOUNT)
    except Exception:
        stored = None
    if stored:
        return stored

    generated = secrets.token_hex(32)
    try:
        keyring.set_password(AUTH_SECRET_SERVICE, AUTH_SECRET_ACCOUNT, generated)
    except Exception:
        # Without a keyring the key lives only for this process, so sessions
        # end on restart rather than failing outright.
        pass
    return generated


def _auth_username() -> str:
    return (store.get_setting('auth_username') or '').strip()


def _auth_password_hash() -> str:
    return (store.get_setting('auth_password_hash') or '').strip()


def _auth_configured() -> bool:
    return bool(_auth_username() and _auth_password_hash())


def _auth_required() -> bool:
    """Whether the current request must carry a signed-in session.

    Only enforced once the server is exposed to the network; a loopback-only
    server is already limited to whoever is sitting at this machine.
    """
    return _public_access_enabled() and _auth_configured()


def _is_signed_in() -> bool:
    return session.get('auth_user') == _auth_username() and _auth_configured()


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


def _ffprobe_has_embedded_subtitles(video_path: str) -> bool:
    """Return True if the video file has any embedded subtitle stream."""
    try:
        result = subprocess.run(
            [FFPROBE_EXE, '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-select_streams', 's', video_path],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
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


def _is_local_media_missing(media_path: str | None) -> bool:
    path = (media_path or '').strip()
    if not path:
        return True
    return _best_local_video_path(path) is None


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


def _staging_path_for(media_type: str) -> str:
    """Where in-progress downloads land before being finalised into the library.

    Falls back to '' when unset so callers keep the previous behaviour of
    downloading straight into the library root.
    """
    configured = (store.get_setting('downloads_path') or '').strip()
    if not configured:
        return ''
    subfolder = 'tv' if media_type == 'tv' else 'movies'
    return os.path.join(configured, subfolder)


def _retire_path(path: str) -> bool:
    """Send a superseded file/folder to the Recycle Bin so it stays recoverable.

    Refuses to act unless send2trash is available — we would rather leave a
    stale file on disk than permanently delete media.
    """
    target = (path or '').strip()
    if not target or not os.path.exists(target):
        return False
    if send2trash is None:
        app.logger.warning('send2trash unavailable; leaving superseded file in place: %s', target)
        return False
    try:
        send2trash(os.path.abspath(target))
        return True
    except Exception as exc:  # noqa: BLE001
        app.logger.warning('Could not recycle %s: %s', target, exc)
        return False


def _public_access_enabled() -> bool:
    return (store.get_setting('public_access') or '0').strip() == '1'


def _server_port() -> int:
    raw = (store.get_setting('server_port') or '').strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_SERVER_PORT
    return port if 1024 <= port <= 65535 else DEFAULT_SERVER_PORT


def _lan_ip_addresses() -> list[str]:
    """Private IPv4 addresses this machine holds, most likely LAN first.

    Every address is offered rather than one "best" guess: probing the outbound
    route returns the VPN tunnel address while a VPN is connected, and no device
    on the local network can reach that. Ordering is a heuristic only — home
    routers overwhelmingly hand out 192.168.x.x, while VPN clients tend to sit
    in 10.x.x.x — so the list is shown in full and the user picks.
    """
    try:
        candidates = {
            info[4][0]
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        }
    except OSError:
        return []

    def rank(address: str) -> tuple[int, str]:
        if address.startswith('192.168.'):
            return 0, address
        if address.startswith('172.'):
            return 1, address
        return 2, address

    usable = []
    for raw in candidates:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if address.is_private and not address.is_loopback and not address.is_link_local:
            usable.append(str(address))
    return sorted(usable, key=rank)


def _upgrade_available(item) -> bool:
    """Whether a genuinely better release is on offer for this item.

    `quality_checks.found` is a boolean frozen at the moment the search ran, so
    on its own it keeps advertising an upgrade long after the file has been
    upgraded — it will even offer a 1080p release for a file that is now 2160p.
    Re-comparing the recorded result against the current quality makes the
    badge self-correcting no matter how stale the stored check is.
    """
    try:
        if int(item['found'] or 0) != 1:
            return False
    except (KeyError, IndexError, TypeError, ValueError):
        return False

    try:
        best_found = item['best_found_quality']
    except (KeyError, IndexError):
        return False
    if not best_found:
        return False

    try:
        current = item['current_quality']
    except (KeyError, IndexError):
        current = None
    return compare_quality(current, best_found) > 0


def _is_download_state_stale(updated_at_text: str | None, stale_after: timedelta) -> bool:
    if not updated_at_text:
        return False
    try:
        updated_at = datetime.strptime(updated_at_text.strip(), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return (datetime.now(timezone.utc) - updated_at) >= stale_after


class QbtUnavailableError(RuntimeError):
    """qBittorrent could not be reached, so its torrent state is unknown."""


QBT_DONE_STATES = {'uploading', 'stalledup', 'seeding', 'pausedup', 'forcedup', 'checkingup'}

# Finalisation moves files, and _auto_finalize runs on every page load, so
# concurrent requests must not race each other into the same media item.
_FINALIZE_LOCK = threading.Lock()


def _torrent_is_complete(torrent: dict) -> bool:
    """True only when qB reports the payload fully written to disk.

    A 'done' state alone is not enough — qB reports seeding states while still
    flushing/rechecking — so the byte counters have to agree as well.
    """
    if (torrent.get('state') or '').lower() not in QBT_DONE_STATES:
        return False
    try:
        progress = float(torrent.get('progress') or 0)
    except (TypeError, ValueError):
        progress = 0.0
    try:
        amount_left = int(torrent.get('amount_left') or 0)
    except (TypeError, ValueError):
        amount_left = 1
    return progress >= 1.0 and amount_left == 0


def _library_root_for(media_type: str) -> str:
    setting = 'tv_path' if (media_type or 'movie') == 'tv' else 'movies_path'
    return (store.get_setting(setting) or '').strip()


def _place_video_in_library(source_video: str, dest_file: str) -> str | None:
    """Put a finished download at its canonical library path.

    Hardlink first: it is instant, consumes no extra space, and leaves the
    torrent's own copy untouched so qBittorrent keeps seeding. Falls back to a
    copy when the staging area and library sit on different volumes.
    Returns the strategy used, or None on failure.
    """
    try:
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        app.logger.warning('Could not create library folder for %s: %s', dest_file, exc)
        return None

    if os.path.exists(dest_file):
        if os.path.samefile(source_video, dest_file):
            return 'already-linked'
        app.logger.warning('Canonical destination already occupied: %s', dest_file)
        return None

    try:
        os.link(source_video, dest_file)
        return 'hardlink'
    except OSError:
        pass  # different volume, or filesystem without hardlink support

    try:
        shutil.copy2(source_video, dest_file)
        return 'copy'
    except Exception as exc:  # noqa: BLE001
        app.logger.warning('Could not place %s into library: %s', source_video, exc)
        return None


def _videos_in(folder_path: str) -> list[str]:
    found = []
    if not folder_path or not os.path.isdir(folder_path):
        return found
    for root, _dirs, files in os.walk(folder_path):
        for name in files:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
                found.append(os.path.join(root, name))
    return found


def _finalize_tv_episode(row, new_video: str, new_quality: str | None) -> None:
    """File a finished TV download into its show as an episode.

    A show's path is a folder of many episodes, so there is no single "old file"
    a download replaces. Nothing here retires anything: the episode is filed
    alongside the others, or left in place if it cannot be identified.
    """
    media_id = int(row['id'])
    show_path = (row['path'] or '').strip()
    marker = EPISODE_MARKER.search(os.path.basename(new_video))

    if not show_path or not os.path.isdir(show_path) or not marker:
        app.logger.info(
            'Media %s: leaving TV download in place (show folder or SxxExx missing).', media_id
        )
        store.clear_download_state(media_id)
        return

    season, episode = int(marker.group(1)), int(marker.group(2))
    stem = canonical_stem(row['title'] or '', row['year'], 'tv') or (row['title'] or 'Show')
    season_folder = os.path.join(show_path, f'Season {season:02d}')
    extension = os.path.splitext(new_video)[1]
    dest_file = os.path.join(season_folder, f'{stem} - S{season:02d}E{episode:02d}{extension}')

    existing_episodes, _unmatched = scan_local_episodes(show_path, row['title'] or '')
    existing_file = existing_episodes.get((season, episode))

    if existing_file:
        existing_quality = detect_quality_from_file(existing_file, ffprobe_exe=FFPROBE_EXE)
        if compare_quality(existing_quality, new_quality) <= 0:
            store.set_download_state(
                media_item_id=media_id,
                status='needs_review',
                source=row['download_source'] or 'qb_webui',
                message=(f'S{season:02d}E{episode:02d}: downloaded {new_quality or "unknown"} is not '
                         f'better than existing {existing_quality or "unknown"}'),
            )
            return

    if os.path.exists(dest_file) and not (existing_file and os.path.samefile(existing_file, dest_file)):
        app.logger.info('Media %s: %s already exists, leaving download in place.', media_id, dest_file)
        store.clear_download_state(media_id)
        return

    landing = f'{dest_file}.incoming' if os.path.exists(dest_file) else dest_file
    if _place_video_in_library(new_video, landing) is None:
        return

    if existing_file:
        # A file covering several episodes must survive: retiring the S04E01-E02
        # file because E02 was upgraded would take E01 with it.
        covered = _episodes_covered(os.path.basename(existing_file))
        covers_only_this = bool(covered) and covered[1] == [episode]
        if covers_only_this and not os.path.samefile(existing_file, landing):
            _retire_path(existing_file)
            app.logger.info('Media %s: retired superseded S%02dE%02d file %s',
                            media_id, season, episode, existing_file)
        elif not covers_only_this:
            app.logger.info(
                'Media %s: kept %s, it also covers other episodes.', media_id, existing_file)

    if landing != dest_file:
        try:
            os.replace(landing, dest_file)
        except OSError as exc:  # noqa: BLE001
            app.logger.warning('Could not rename %s into place: %s', landing, exc)
            return

    app.logger.info('Media %s: filed S%02dE%02d at %s', media_id, season, episode, dest_file)
    try:
        _refresh_local_media_signals(media_id, show_path)
    except Exception:
        pass
    if new_quality:
        store.update_quality(media_id, new_quality)
    store.clear_download_state(media_id)


def _finalize_completed_download(row, torrent: dict) -> None:
    """Move one finished download into the library under its canonical name."""
    media_id = int(row['id'])
    media_type = row['media_type'] or 'movie'
    mode = (row['download_mode'] or 'fill').strip().lower()
    previous_path = (row['download_previous_path'] or '').strip() or (row['path'] or '').strip()

    new_video = _best_local_video_path((torrent.get('content_path') or '').strip())
    if not new_video:
        return  # complete per qB but nothing playable yet; retry next pass

    new_quality = (
        detect_quality_from_file(new_video, ffprobe_exe=FFPROBE_EXE)
        or detect_quality(os.path.basename(new_video))
    )

    # A TV item points at a whole show, so the movie logic below — which replaces
    # "the" file and retires what it supersedes — would recycle every episode.
    if media_type == 'tv':
        _finalize_tv_episode(row, new_video, new_quality)
        return

    # Resolve the outgoing file *before* placing the replacement. When the old
    # file already sits in the canonical folder the new one lands beside it,
    # and _best_local_video_path picks the largest — which would then resolve
    # to the file we just placed and retire the wrong one.
    outgoing_video = _best_local_video_path(previous_path) if previous_path else None

    # An upgrade must actually be an upgrade. Without this, a mislabelled
    # release could retire a better file than the one it replaces.
    if mode == 'upgrade' and outgoing_video:
        old_quality = row['current_quality'] or detect_quality_from_file(outgoing_video, ffprobe_exe=FFPROBE_EXE)
        if compare_quality(old_quality, new_quality) <= 0:
            store.set_download_state(
                media_item_id=media_id,
                status='needs_review',
                source=row['download_source'] or 'qb_webui',
                message=f'Downloaded {new_quality or "unknown"} is not better than existing {old_quality or "unknown"}',
            )
            app.logger.info(
                'Upgrade for media %s rejected: %s is not better than %s',
                media_id, new_quality, old_quality,
            )
            return

    library_root = _library_root_for(media_type)
    destination = canonical_paths(
        library_root, row['title'] or '', row['year'],
        os.path.splitext(new_video)[1], media_type,
    )
    if not destination:
        app.logger.warning('No canonical name for media %s; leaving download in place.', media_id)
        return
    dest_folder, dest_file = destination

    # The canonical destination is often occupied by the very file being
    # replaced (an earlier release already correctly named). Land the
    # replacement beside it and only take its name once the old one is safely
    # recycled, so nothing is destroyed before the new file is really there.
    replacing_in_place = bool(
        outgoing_video
        and os.path.isfile(dest_file)
        and os.path.samefile(dest_file, outgoing_video)
    )
    landing = f'{dest_file}.incoming' if replacing_in_place else dest_file

    strategy = _place_video_in_library(new_video, landing)
    if strategy is None:
        return

    if replacing_in_place:
        if not _retire_path(outgoing_video):
            try:
                os.remove(landing)  # leave the existing library file untouched
            except OSError:
                pass
            app.logger.warning(
                'Could not recycle %s; upgrade for media %s abandoned.', outgoing_video, media_id
            )
            return
        try:
            os.replace(landing, dest_file)
        except OSError as exc:  # noqa: BLE001
            app.logger.warning('Could not rename %s into place: %s', landing, exc)
            return
        outgoing_video = None  # retired above; skip the generic retire below

    app.logger.info('Placed media %s at %s (%s).', media_id, dest_file, strategy)

    # Only retire the old file once the replacement is verifiably in place, and
    # never when it resolved to the very file we just wrote.
    if mode == 'upgrade' and outgoing_video and os.path.isfile(dest_file):
        if not os.path.samefile(outgoing_video, dest_file):
            outgoing_folder = os.path.dirname(outgoing_video)
            same_folder = os.path.samefile(outgoing_folder, dest_folder)
            siblings = _videos_in(previous_path) if os.path.isdir(previous_path) else []

            retire_target = outgoing_video
            if not same_folder and os.path.isdir(previous_path):
                if len(siblings) <= 1:
                    retire_target = previous_path
                else:
                    # Several videos share the folder, so which one belongs to this
                    # item is a guess — _best_local_video_path just picks the
                    # largest. Retiring either the folder or that guess could take
                    # an unrelated title with it, so nothing is retired here.
                    retire_target = None
                    app.logger.info(
                        'Media %s: %s holds %d videos, so nothing was retired. The superseded '
                        'file may need clearing up by hand.',
                        media_id, previous_path, len(siblings),
                    )
            if retire_target and _retire_path(retire_target):
                app.logger.info('Recycled superseded media for %s: %s', media_id, retire_target)

    store.update_path(media_id, dest_folder)
    try:
        _refresh_local_media_signals(media_id, dest_folder)
    except Exception:
        pass
    # Canonical filenames carry no quality token, so re-detection after the
    # rename must read the file itself; fall back to what the release claimed.
    placed_quality = detect_quality_from_file(dest_file, ffprobe_exe=FFPROBE_EXE) or new_quality
    if placed_quality:
        store.update_quality(media_id, placed_quality)
    # The stored upgrade result was measured against the file we just replaced.
    store.clear_quality_checks(media_id)
    store.clear_download_state(media_id)


def _readopt_orphaned_downloads(torrents: list[dict]) -> None:
    """Relink items that lost their download state while a torrent still runs.

    Without this a title with no file and no download state is invisible: the
    library view lists items by local file or active download, so it silently
    drops out and only reappears as a gap in Discover. qBittorrent is the source
    of truth here, so the link can simply be rebuilt from it.
    """
    for row in store.list_media_items():
        if (row['download_status'] or '').strip():
            continue
        if _best_local_video_path(row['path']):
            continue

        torrent = _qbt_match_torrent_for_item(dict(row), torrents=torrents)
        if not torrent:
            continue

        # The matcher accepts a title-only hit; requiring the year as well keeps
        # a sequel or same-named release from being adopted by mistake.
        year = str(row['year'] or '').strip()
        if year and year not in _norm_match_text(torrent.get('name')):
            continue

        info_hash = (torrent.get('hash') or '').strip().upper()
        store.set_download_state(
            media_item_id=int(row['id']),
            status='downloading',
            source='qb_webui',
            message='Relinked to an active qBittorrent download',
            torrent_hash=info_hash if re.fullmatch(r'[0-9A-F]{40}', info_hash) else None,
            mode='fill',
        )
        app.logger.info(
            'Relinked media %s (%s) to torrent %r', row['id'], row['title'], torrent.get('name'),
        )


def _auto_finalize_qb_completed_downloads() -> None:
    if not _qbt_webui_enabled():
        return
    if not _FINALIZE_LOCK.acquire(blocking=False):
        return  # another request is already finalising; skip this pass

    try:
        active_states = {'starting', 'handed_off', 'downloading'}
        stale_after = timedelta(hours=12)
        try:
            torrents = _qbt_webui_torrents_info()
        except QbtUnavailableError as exc:
            # Reconciling against an unknown state would read a brief outage as
            # proof that every download had vanished, and clear them all.
            app.logger.warning('Skipping download reconciliation, qBittorrent unreachable: %s', exc)
            return

        for row in store.list_media_items():
            status = (row['download_status'] or '').strip().lower()
            if status not in active_states:
                continue
            media_id = int(row['id'])
            effective_path = (row['path'] or '').strip()

            source = (row['download_source'] or '').strip()
            torrent_hash = (row['download_torrent_hash'] or '').strip().upper()
            torrent = None
            try:
                if source == 'qb_webui' and re.fullmatch(r'[0-9A-F]{40}', torrent_hash):
                    torrent = _qbt_webui_torrent_info(torrent_hash)
                if not torrent:
                    torrent = _qbt_match_torrent_for_item(dict(row), torrents=torrents)
            except Exception:
                continue

            if not torrent:
                # qB no longer reports this torrent; reconcile against local media
                # and stale age to prevent permanently stuck download badges.
                if _best_local_video_path(effective_path):
                    try:
                        _refresh_local_media_signals(media_id, effective_path or None)
                    except Exception:
                        pass
                    store.clear_download_state(media_id)
                    continue
                if _is_download_state_stale(row['download_updated_at'], stale_after):
                    store.clear_download_state(media_id)
                continue

            if not _torrent_is_complete(torrent):
                continue

            try:
                _finalize_completed_download(row, torrent)
            except Exception as exc:  # noqa: BLE001
                app.logger.warning('Finalising download for media %s failed: %s', media_id, exc)

        _readopt_orphaned_downloads(torrents)
    finally:
        _FINALIZE_LOCK.release()


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


def _feature_videos_in(folder_path: str) -> list[str]:
    """Video files in a folder large enough to be features rather than extras.

    Used to spot multi-film pack folders; the size floor keeps samples,
    trailers and featurettes from being mistaken for separate films.
    """
    found: list[str] = []
    try:
        for root, _dirs, files in os.walk(folder_path):
            for name in files:
                if os.path.splitext(name)[1].lower() not in VIDEO_EXTENSIONS:
                    continue
                full = os.path.join(root, name)
                try:
                    if os.path.getsize(full) >= PACK_MIN_FEATURE_BYTES:
                        found.append(full)
                except OSError:
                    continue
    except Exception:
        return []
    return sorted(found)


EPISODE_MARKER = re.compile(r'[Ss](\d{1,2})[ ._-]*[Ee](\d{1,3})')

# One file can cover several episodes, e.g. "S04E01-E02" for a two-part premiere
# that aired as one. Without this the later episodes look missing.
# The second number must carry its own E, or be joined by a bare hyphen, so
# "S01E01 - 1984" and "S01E01.2160p" are never read as ranges.
EPISODE_RANGE_TAIL = re.compile(r'^(?:[ ._-]*[Ee](\d{1,3})|-(\d{1,3})(?![\dp]))', re.I)
# A guard against a misparse inventing a huge span of episodes.
EPISODE_RANGE_MAX_SPAN = 8


def _episodes_covered(filename: str) -> tuple[int, list[int]] | None:
    """Season and every episode number a filename claims, or None."""
    marker = EPISODE_MARKER.search(filename)
    if not marker:
        return None

    season = int(marker.group(1))
    first = last = int(marker.group(2))
    tail = filename[marker.end():]
    while True:
        step = EPISODE_RANGE_TAIL.match(tail)
        if not step:
            break
        following = int(step.group(1) or step.group(2))
        if following <= last or following - first > EPISODE_RANGE_MAX_SPAN:
            break
        last = following
        tail = tail[step.end():]
    return season, list(range(first, last + 1))

_SEASON_DIR_EXACT = re.compile(r'^(?:season\s*|s)(\d{1,2})$', re.I)
_SPECIALS_DIR = re.compile(r'^specials?$', re.I)
# A season embedded in a longer folder name, e.g.
# "Harley Quinn (2019) Season 3 S03 (1080p ...)". Ranges such as "Season 1-9" /
# "S01-S09" span a whole series and cannot be attributed to one season, so both
# ends of a range are rejected — the lookbehind matters because otherwise the
# tail of "S01-S09" matches on its own.
_SEASON_DIR_EMBEDDED = re.compile(r'(?<![-–])\bseason\s*(\d{1,2})(?!\s*[-–]\s*\d)', re.I)
_SEASON_DIR_SXX = re.compile(r'(?<![-–])\bs(\d{2})(?![\d\-–])', re.I)


def _infer_season_from_path(show_path: str, file_path: str) -> int | None:
    """Season a non-episode file belongs to, taken from its folders.

    Only directory names are considered — a featurette called "Season 4 Overview"
    sitting in Season 1 belongs to Season 1. The deepest folder wins, so a nested
    season folder beats a release folder above it. Returns None when no folder
    names a single season, which is the case for show-wide extras.
    """
    try:
        relative = os.path.relpath(file_path, show_path)
    except ValueError:
        return None

    parts = [p for p in os.path.dirname(relative).split(os.sep) if p and p != '.']
    for name in reversed(parts):
        if _SPECIALS_DIR.match(name):
            return 0
        for pattern in (_SEASON_DIR_EXACT, _SEASON_DIR_EMBEDDED, _SEASON_DIR_SXX):
            found = pattern.search(name)
            if found:
                return int(found.group(1))
    return None


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def scan_local_episodes(show_path: str,
                        show_title: str = '') -> tuple[dict[tuple[int, int], str], list[str]]:
    """Index a show folder by (season, episode), plus any files with no marker.

    Only an SxxExx marker in the filename is trusted, and the folder it sits in is
    ignored — files do turn up under the wrong season folder. About a fifth of
    this library has no marker at all, and those are overwhelmingly featurettes
    and extras rather than episodes, so inferring numbers from folder order would
    invent episodes that do not exist. Unmatched files are returned separately so
    they can be listed without being given an episode number.

    A marker is not trusted when the filename names a different show — see
    `names_other_show`. Such a file is reported as unmatched, so it stays visible
    without being played, counted or renamed as an episode it is not.
    """
    matched: dict[tuple[int, int], str] = {}
    unmatched: list[str] = []
    if not show_path or not os.path.isdir(show_path):
        return matched, unmatched

    show_title = show_title or os.path.basename(os.path.normpath(show_path))
    for root, _dirs, files in os.walk(show_path):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            full = os.path.join(root, name)
            covered = _episodes_covered(name)
            if not covered or names_other_show(name, show_title):
                unmatched.append(full)
                continue
            season, episode_numbers = covered
            for episode in episode_numbers:
                key = (season, episode)
                previous = matched.get(key)
                # Duplicate rips of one episode are common; keep the largest.
                if previous and _file_size(previous) >= _file_size(full):
                    continue
                matched[key] = full
    return matched, unmatched


def _pack_films_in(folder_path: str) -> list[str]:
    """Feature-sized videos in a folder that each carry their own release year.

    This is what separates a box set from a single film shipped with extras: a
    collection names every film with its year ("... Dead Mans Chest 2006"),
    while featurettes and deleted scenes never do. Size alone is not enough —
    bonus features routinely run past the feature size floor.
    """
    films = []
    for video in _feature_videos_in(folder_path):
        _title, year = normalize_media_name(os.path.basename(video), 'movie')
        if year:
            films.append(video)
    return films


def scan_media_entries(folder_path: str, media_type: str) -> list[dict[str, str]]:
    """Return importable media entries from a configured library folder."""
    if not folder_path or not os.path.isdir(folder_path):
        return []

    # Guards against a staging folder configured inside the library root: a
    # part-finished download must never be imported as a library title.
    staging = (store.get_setting('downloads_path') or '').strip()
    staging_norm = os.path.normcase(os.path.normpath(staging)) if staging else ''

    entries: list[dict[str, str]] = []
    try:
        for entry in os.scandir(folder_path):
            if entry.name.startswith('.'):
                continue
            if entry.is_dir():
                if staging_norm and os.path.normcase(os.path.normpath(entry.path)) == staging_norm:
                    continue
                if _best_local_video_path(entry.path) is None:
                    continue
                # A movie folder holding several year-stamped films is a
                # collection pack. Treating it as one title would import
                # whichever film matched first and leave the rest invisible,
                # so emit one entry per film.
                films = _pack_films_in(entry.path) if media_type == 'movie' else []
                if len(films) > 1:
                    for video in films:
                        entries.append({'name': os.path.basename(video), 'path': video})
                    continue
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


def _detect_release_year(text: str) -> tuple[int | None, int | None]:
    """Find the release year and where the release noise starts.

    Returns (year, cut_index). Everything from cut_index onwards is release
    metadata (quality, codec, group) rather than part of the title.

    A parenthesised year wins outright. Otherwise the *last* plausible year is
    taken, so a numeral that belongs to the title keeps its place — in
    "Blade Runner 2049 2017 1080p" the title is "Blade Runner 2049", not
    "Blade Runner".
    """
    max_year = datetime.now().year + 2

    for match in re.finditer(r'\(\s*(19\d{2}|20\d{2})\s*\)', text):
        year = int(match.group(1))
        if 1900 <= year <= max_year:
            return year, match.start()

    found: list[tuple[int, int]] = []
    for match in re.finditer(r'\b(19\d{2}|20\d{2})\b', text):
        year = int(match.group(1))
        if 1900 <= year <= max_year:
            found.append((year, match.start()))
    return found[-1] if found else (None, None)


def normalize_media_name(raw_name: str, media_type: str) -> tuple[str, int | None]:
    """Convert a filename or folder name into a cleaner IMDb search query."""
    base_name = raw_name.rstrip('/\\')
    stem, ext = os.path.splitext(base_name)
    if ext.lower() in VIDEO_EXTENSIONS:
        base_name = stem

    text = re.sub(r'[._]+', ' ', base_name)
    text = re.sub(r'\[[^\]]*\]', ' ', text)

    year, cut = _detect_release_year(text)
    # A cut at position 0 means the year opens the name, so it is the title
    # itself ("2012") rather than a suffix — keep the text intact.
    if cut:
        text = text[:cut]

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
    existing_by_imdb = {item['imdb_id']: item for item in existing_items}
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

        # Don't let a duplicate/alternate folder (e.g. a second copy or a stale
        # decoy) hijack the path of an item that already resolves to a playable
        # local file. Only adopt the newly-found path if the existing one is
        # missing/broken, so a moved or renamed file can still self-heal.
        existing_item = existing_by_imdb.get(meta['imdb_id'])
        if existing_item and not _is_local_media_missing(existing_item['path']):
            existing_paths.add(normalized_path)
            continue

        poster_url = cache_poster(meta.get('imdb_id') or entry['name'], meta.get('poster_url') or '') or meta.get('poster_url')

        # Resolve the actual video file for quality detection; entry path may be a folder (e.g. TV show)
        quality_target = entry['path']
        if os.path.isdir(quality_target):
            for _root, _dirs, _files in os.walk(quality_target):
                for fname in _files:
                    if os.path.splitext(fname)[1].lower() in VIDEO_EXTENSIONS:
                        quality_target = os.path.join(_root, fname)
                        break
                else:
                    continue
                break

        store.add_media_item(
            imdb_id=meta['imdb_id'],
            tmdb_id=meta.get('tmdb_id'),
            title=meta['title'],
            year=meta['year'],
            media_type=meta['media_type'],
            collection_id=meta.get('collection_id'),
            collection_name=meta.get('collection_name'),
            current_quality=detect_quality_from_file(quality_target, ffprobe_exe=FFPROBE_EXE) or detect_quality(entry['name']),
            path=entry['path'],
            poster_url=poster_url,
            synopsis=meta.get('synopsis'),
            actors=meta.get('actors'),
            genre_1=meta.get('genre_1'),
            genre_2=meta.get('genre_2'),
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


app = Flask(__name__)
store = Storage(DB_PATH)
app.secret_key = _session_secret_key()
app.permanent_session_lifetime = timedelta(days=SESSION_LIFETIME_DAYS)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # Secure is deliberately not set: this is served over plain HTTP on a LAN,
    # and the flag would stop the cookie being sent at all.
)
tmdb = None
_refresh_tmdb_client()
qb = QBSearch(nova_path=QBT_NOVA_PATH)

# Clean up old HLS cache on startup
_cleanup_hls_cache()


def configured_mirror_urls() -> list[str]:
    raw = store.get_setting('mirror_urls') or ''
    urls = [line.strip() for line in raw.splitlines() if line.strip()]
    return urls or list(DEFAULT_MIRROR_URLS)


AUTH_EXEMPT_ENDPOINTS = {'login', 'static'}

# Failed sign-ins per client address. In-memory is enough: a restart clearing
# the counters costs an attacker more time than it saves them.
_failed_logins: dict[str, tuple[int, datetime]] = {}


def _client_address() -> str:
    return request.remote_addr or 'unknown'


def _login_locked_until(address: str) -> datetime | None:
    attempts, last_failure = _failed_logins.get(address, (0, None))
    if attempts < AUTH_MAX_ATTEMPTS or last_failure is None:
        return None
    unlock_at = last_failure + AUTH_LOCKOUT
    return unlock_at if unlock_at > datetime.now(timezone.utc) else None


def _record_failed_login(address: str) -> None:
    attempts, _ = _failed_logins.get(address, (0, None))
    _failed_logins[address] = (attempts + 1, datetime.now(timezone.utc))


def _wants_json_response() -> bool:
    return (
        request.path.startswith('/api/')
        or request.headers.get('X-Requested-With') == 'fetch'
        or 'application/json' in (request.headers.get('Accept') or '')
    )


def _safe_next_target(raw: str | None) -> str:
    """Only allow same-site relative paths, so ?next= cannot bounce elsewhere."""
    target = (raw or '').strip()
    if not target.startswith('/') or target.startswith('//'):
        return url_for('index')
    return target


@app.before_request
def _require_login():
    if not _auth_required() or request.endpoint in AUTH_EXEMPT_ENDPOINTS:
        return None
    if _is_signed_in():
        return None
    if _wants_json_response():
        return jsonify({'ok': False, 'error': 'auth_required'}), 401
    return redirect(url_for('login', next=request.full_path.rstrip('?')))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not _auth_configured():
        return redirect(url_for('index'))
    if _is_signed_in():
        return redirect(_safe_next_target(request.args.get('next')))

    address = _client_address()
    locked_until = _login_locked_until(address)
    if locked_until:
        wait_seconds = int((locked_until - datetime.now(timezone.utc)).total_seconds())
        return render_template('login.html', error=f'Too many attempts. Try again in {wait_seconds // 60 + 1} minute(s).',
                               next_target=request.args.get('next', '')), 429

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        username_ok = secrets.compare_digest(username, _auth_username())
        password_ok = check_password_hash(_auth_password_hash(), password)

        if username_ok and password_ok:
            _failed_logins.pop(address, None)
            # Start a clean session so nothing from the signed-out state carries over.
            session.clear()
            session['auth_user'] = _auth_username()
            session.permanent = bool(request.form.get('stay_signed_in'))
            return redirect(_safe_next_target(request.form.get('next')))

        _record_failed_login(address)
        app.logger.warning('Failed sign-in for %r from %s', username, address)
        return render_template('login.html', error='Incorrect username or password.',
                               next_target=request.form.get('next', '')), 401

    return render_template('login.html', error=None, next_target=request.args.get('next', ''))


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


def _cache_meta_poster(cache_key: str, meta: dict, force_replace: bool = False) -> str | None:
    remote_url = meta.get('poster_url') or ''
    return cache_poster(cache_key, remote_url, force_replace=force_replace) or meta.get('poster_url')


def _torrent_candidates_for(meta: dict, limit: int = 25) -> list[dict]:
    title = (meta.get('title') or '').strip()
    year = meta.get('year')
    if not title:
        return []
    query = f'{title} {year}' if year else title
    # SearchEngineError is deliberately not caught here: an unreachable search
    # engine must not look like a title with no available releases.
    qb.set_mirror_urls(configured_mirror_urls())
    rows = qb._run_search(query)

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
    """Every torrent qBittorrent knows about.

    Raises QbtUnavailableError when qBittorrent cannot be reached. Returning an
    empty list there would be indistinguishable from "no torrents", and callers
    that reconcile download state would treat a brief outage as proof that every
    download had vanished.
    """
    if not _qbt_webui_enabled():
        raise QbtUnavailableError('qbt_webui_not_configured')

    try:
        body = _qbt_webui_open('/api/v2/torrents/info?filter=all', method='GET').decode('utf-8', errors='replace')
    except Exception as exc:
        raise QbtUnavailableError(str(exc) or 'qbt_unreachable') from exc
    try:
        items = json.loads(body)
    except Exception as exc:
        raise QbtUnavailableError('qbt_bad_response') from exc
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _norm_match_text(value: str | None) -> str:
    text = unicodedata.normalize('NFKD', value or '').lower()
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _qbt_match_torrent_for_item(item: dict, torrents: list[dict] | None = None) -> dict | None:
    if torrents is None:
        try:
            torrents = _qbt_webui_torrents_info()
        except QbtUnavailableError:
            return None
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


def _cached_tv_status(tmdb_id: int) -> dict:
    """Airing state and season list for a show, from the database cache."""
    overview = store.get_cached_tv_status(tmdb_id, DISCOVER_COLLECTION_CACHE_HOURS)
    if overview is None:
        overview = tmdb.tv_status(tmdb_id) if tmdb else {}
        if overview.get('seasons'):
            store.set_cached_tv_status(tmdb_id, overview)
    return overview or {'status': None, 'in_production': None, 'next_air_date': None, 'seasons': []}


def _missing_episodes_for_show(item, ignored_seasons: set[int]) -> dict | None:
    """Aired-but-unowned episodes for one show, or None when it has no gaps."""
    if not tmdb or (item['media_type'] or '') != 'tv' or not item['tmdb_id']:
        return None
    tmdb_id = int(item['tmdb_id'])
    if Storage.IGNORE_WHOLE_SHOW in ignored_seasons:
        return None

    owned, _unmatched = scan_local_episodes(item['path'] or '', item['title'] or '')
    if not owned:
        return None  # nothing identifiable locally; not a gap we can reason about

    overview = _cached_tv_status(tmdb_id)
    today = date.today()
    seasons_missing = []

    for season in overview.get('seasons') or []:
        number = season['season_number']
        if number == 0 or number in ignored_seasons:
            continue  # specials are optional by nature

        episodes = store.get_cached_season(tmdb_id, number, DISCOVER_COLLECTION_CACHE_HOURS)
        if episodes is None:
            episodes = tmdb.season_episodes(tmdb_id, number)
            if episodes:
                store.set_cached_season(tmdb_id, number, episodes)
        if not episodes:
            continue

        missing = []
        for episode in episodes:
            number_in_season = episode.get('episode_number')
            if number_in_season is None or (number, number_in_season) in owned:
                continue
            aired = (episode.get('air_date') or '').strip()
            if not aired:
                continue
            try:
                if date.fromisoformat(aired) > today:
                    continue
            except ValueError:
                continue
            missing.append(episode)

        if missing:
            seasons_missing.append({
                'season_number': number,
                'name': season['name'],
                'owned_count': sum(1 for s, _e in owned if s == number),
                'episode_count': season['episode_count'],
                'missing': missing,
            })

    if not seasons_missing:
        return None

    return {
        'media_id': int(item['id']),
        'tmdb_id': tmdb_id,
        'title': item['title'],
        'poster_url': item['poster_url'],
        'status': overview.get('status'),
        'in_production': overview.get('in_production'),
        'next_air_date': overview.get('next_air_date'),
        'missing_count': sum(len(s['missing']) for s in seasons_missing),
        'seasons': seasons_missing,
    }


def _discover_missing_episodes() -> list[dict]:
    """Every owned TV show that is missing episodes which have already aired.

    Only aired episodes count. A season part-way through broadcast lists episodes
    that do not exist yet, and offering those as "missing" would be noise — the
    same reason the movie collection view filters on release date.
    """
    if not tmdb:
        return []

    ignored = store.list_ignored_tv()
    out = []
    for item in store.list_media_items():
        show = _missing_episodes_for_show(item, ignored.get(int(item['tmdb_id'] or 0), set()))
        if show:
            out.append(show)

    # Shows still in production first — new episodes are the point of this view —
    # then by how much is missing.
    out.sort(key=lambda show: (not show['in_production'], -show['missing_count'], (show['title'] or '').lower()))
    return out


def backfill_genres() -> dict:
    if not tmdb:
        return {'updated': 0, 'skipped': 0, 'error': 'tmdb_not_configured'}

    updated = 0
    skipped = 0
    for item in store.list_media_items():
        current_1 = (item['genre_1'] or '').strip() if 'genre_1' in item.keys() else ''
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
    if not tmdb:
        return
    if not any(not (item['genre_1'] or '').strip() for item in store.list_media_items()):
        return
    result = backfill_genres()
    if result.get('error'):
        return
    app.logger.info('Genre backfill: %s updated, %s skipped.',
                    result.get('updated'), result.get('skipped'))


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
        i for i in all_flagged
        if (i.get('path') or '').strip() or i.get('download_status') in _active_dl
    ]
    movies = [i for i in local_flagged if i['media_type'] == 'movie']
    tv_shows = [i for i in local_flagged if i['media_type'] == 'tv']
    _run_genre_backfill_once()
    favourites   = [i for i in all_flagged if i['favourite']]

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
            'genre_1': meta.get('genre_1'),
            'genre_2': meta.get('genre_2'),
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
        genre_1=meta.get('genre_1'),
        genre_2=meta.get('genre_2'),
        rating=meta.get('rating'),
        subtitles=None,
    )
    row = store.get_media_item_by_imdb_id(meta['imdb_id'])
    media_id = row['id'] if row else None
    try:
        candidates = _torrent_candidates_for(meta)
    except SearchEngineError as exc:
        return jsonify({
            'ok': False,
            'error': 'search_unavailable',
            'message': str(exc),
            'media_id': media_id,
        }), 503

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
    has_upgrade = bool(item.get('upgrade_available'))
    if status not in {'starting', 'handed_off', 'downloading'} and not item.get('file_missing') and not has_upgrade:
        return jsonify({'ok': False, 'error': 'not_missing_or_active'}), 409

    meta = {
        'title': item['title'] or item['imdb_id'] or '',
        'year': item['year'],
    }
    try:
        candidates = _torrent_candidates_for(meta)
    except SearchEngineError as exc:
        return jsonify({
            'ok': False,
            'error': 'search_unavailable',
            'message': str(exc),
            'media_id': int(item['id']),
        }), 503
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
        library_path = (store.get_setting(target_setting) or '').strip()
        # Download into staging so a part-finished release is never visible to
        # the library scanner; it only enters the library once finalised.
        target_path = _staging_path_for(media_type) or library_path
        if target_path:
            # Captured before submitting: an item that already plays is being
            # upgraded, so finalisation must replace rather than just adopt.
            existing_path = (media_item['path'] or '').strip()
            is_upgrade = bool(existing_path) and not _is_local_media_missing(existing_path)
            try:
                store.set_download_state(
                    media_item_id=int(media_item['id']),
                    status='starting',
                    source='qb_webui',
                    message='Submitting to qBittorrent',
                    mode='upgrade' if is_upgrade else 'fill',
                    previous_path=existing_path or None,
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


@app.route('/api/quality/<int:media_id>/scan', methods=['POST'])
def scan_quality_for_item(media_id: int):
    """Scan a single library item's video file to detect and store its local quality."""
    item = store.get_media_item(media_id)
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
        store.update_quality(media_id, quality)

    payload = _ui_item_payload(media_id)
    return jsonify({'ok': True, 'item': payload})


def _fetch_best_metadata(item) -> dict:
    """Return TMDB metadata for a library item.

    Strategy:
    0. If a TMDB id is stored, use it. It identifies the title exactly, so no
       guessing is needed and a wrong stored imdb_id cannot drag the item onto
       a different film via the name search below.
    1. If the stored imdb_id is a valid title ID (starts with 'tt'), try /find.
    2. If that yields no poster (bad/missing ID), fall back to a text search
       using the folder/file name from the stored path.
    """
    imdb_id = (item['imdb_id'] or '').strip()
    media_type = item['media_type'] or 'movie'

    stored_tmdb_id = item['tmdb_id'] if 'tmdb_id' in item.keys() else None
    if stored_tmdb_id:
        exact = tmdb.metadata_by_tmdb_id(stored_tmdb_id, media_type)
        if exact.get('poster_url') or exact.get('genre_1'):
            return exact

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
        status = (payload.get('download_status') or '').strip().lower()
        payload['file_missing'] = _is_local_media_missing(path) and status not in {'starting', 'handed_off', 'downloading'}
        payload['upgrade_available'] = _upgrade_available(row)
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


@app.route('/api/library/fetch-subtitles/<int:media_id>', methods=['POST'])
def fetch_subtitles_api(media_id: int):
    """Fetch and save English subtitles for a library item via subliminal."""
    item_row = store.get_media_item(media_id)
    item = dict(item_row) if item_row else None
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    if not item.get('path') or item.get('file_missing'):
        return jsonify({'ok': False, 'error': 'no_file'}), 409

    video_path = _best_local_video_path(item['path'])
    if not video_path:
        return jsonify({'ok': False, 'error': 'no_video_file'}), 409

    try:
        from subtitle_client import fetch_subtitles
        found = fetch_subtitles(
            video_path,
            title=item.get('title'),
            year=item.get('year'),
        )
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500

    if not found:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    store.update_subtitles(media_id, scan_subtitles(item['path']))
    payload = _ui_item_payload(media_id) or {}
    return jsonify({'ok': True, 'item': payload})


@app.route('/check-all', methods=['POST'])
def check_all():
    """Run a quality search for all items, or a specific media_type if supplied."""
    media_type = request.form.get('media_type') or None
    all_items = store.list_media_items()
    items = [i for i in all_items if i['media_type'] == media_type] if media_type else list(all_items)

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
        except Exception as exc:  # noqa: BLE001
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
        store.set_setting('movies_path', movies_path.strip())
    if tv_path is not None:
        store.set_setting('tv_path', tv_path.strip())
    if downloads_path is not None:
        store.set_setting('downloads_path', downloads_path.strip())
    if preferred_quality is not None:
        store.set_setting('preferred_quality', preferred_quality.strip() or '2160p')

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
                return redirect(url_for('index', section='settings', status='port_invalid'))

        username = (auth_username_raw or '').strip()
        password = auth_password_raw or ''
        new_password_hash = None
        if password or auth_password_confirm:
            if password != (auth_password_confirm or ''):
                return redirect(url_for('index', section='settings', status='password_mismatch'))
            if len(password) < 8:
                return redirect(url_for('index', section='settings', status='password_too_short'))
            new_password_hash = generate_password_hash(password)

        will_have_username = username or _auth_username()
        will_have_hash = new_password_hash or _auth_password_hash()
        if wants_public and not (will_have_username and will_have_hash):
            # Refusing here is the whole point of the feature: exposing the
            # library to the network with no credentials set has no safe path.
            return redirect(url_for('index', section='settings', status='auth_required_for_public'))

        if username:
            store.set_setting('auth_username', username)
        if new_password_hash:
            store.set_setting('auth_password_hash', new_password_hash)
        store.set_setting('public_access', '1' if wants_public else '0')
        if port is not None:
            store.set_setting('server_port', str(port))
        return redirect(url_for('index', section='settings', status='public_access_saved'))
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
# Keys: media_id
# Values: list of subtitle dicts
_subtitle_cache: dict[int, list[dict]] = {}


def _probe_embedded_subtitles(video_file: str) -> list[dict]:
    """Return embedded subtitle stream metadata from a video file."""
    results: list[dict] = []
    try:
        probe = subprocess.run(
            [
                FFPROBE_EXE,
                '-v', 'quiet',
                '-print_format', 'json',
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
            tags = stream.get('tags') or {}
            lang = (tags.get('language') or 'und').lower()
            title = tags.get('title') or f'Embedded {lang.upper()}'
            stream_index = stream.get('index')
            if stream_index is None:
                continue
            results.append({
                'stream_index': int(stream_index),
                'lang': 'en' if lang in {'en', 'eng', 'english'} else lang,
                'name': title,
            })
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
            '-i', video_file,
            '-map', f'0:{stream_index}',
            '-c:s', 'webvtt',
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


def _find_subtitle_files(media_path: str | None, media_id: int | None = None) -> list[dict]:
    """Find all subtitle files (.srt, .vtt, etc.) near a video file.
    
    Returns a list of dicts: [{'name': 'English', 'lang': 'en', 'index': 0}]
    Caches file paths keyed by media_id for later serving via API.
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
                subtitles.append({
                    'name': 'English',
                    'lang': 'en',
                    'index': index,
                })
                entries.append({
                    'type': 'external',
                    'path': full_path,
                    'ext': ext_lower,
                })
    except Exception:
        pass

    # Include embedded subtitle streams as additional CC tracks.
    try:
        embedded = _probe_embedded_subtitles(video_file)
        for sub in embedded:
            index = len(subtitles)
            subtitles.append({
                'name': sub.get('name') or 'Embedded Subtitle',
                'lang': sub.get('lang') or 'und',
                'index': index,
            })
            entries.append({
                'type': 'embedded',
                'video_file': video_file,
                'stream_index': sub.get('stream_index'),
                'path': os.path.join(HLS_CACHE_DIR, _playback_cache_key(media_id) if media_id else 'tmp', f"subtitle_{sub.get('stream_index')}.vtt"),
                'ext': '.vtt',
            })
    except Exception:
        pass
    
    # Cache the file paths if media_id provided
    if media_id and entries:
        _subtitle_cache[media_id] = entries
    
    return subtitles


def _resolve_episode_file(show_path: str, relative: str) -> str | None:
    """Absolute path for an episode inside a show folder.

    Returns None if the resolved path escapes the show folder, so a crafted
    ?episode= cannot be used to read arbitrary files from disk.
    """
    if not show_path or not relative:
        return None
    base = os.path.realpath(show_path)
    target = os.path.realpath(os.path.join(base, relative))
    if target != base and not target.startswith(base + os.sep):
        return None
    if not os.path.isfile(target):
        return None
    if os.path.splitext(target)[1].lower() not in VIDEO_EXTENSIONS:
        return None
    return target


# Season metadata rarely changes, and a single user browsing seasons should not
# re-query TMDB on every click. Kept in process rather than in the database
# because losing it on restart costs one request.
_TMDB_SEASON_CACHE: dict[tuple[int, int], tuple[datetime, list[dict]]] = {}
TMDB_SEASON_CACHE_TTL = timedelta(hours=24)


def _cached_season_episodes(tmdb_id: int, season_number: int) -> list[dict]:
    if not tmdb:
        return []
    key = (int(tmdb_id), int(season_number))
    cached = _TMDB_SEASON_CACHE.get(key)
    if cached and (datetime.now(timezone.utc) - cached[0]) < TMDB_SEASON_CACHE_TTL:
        return cached[1]
    episodes = tmdb.season_episodes(tmdb_id, season_number)
    if episodes:
        _TMDB_SEASON_CACHE[key] = (datetime.now(timezone.utc), episodes)
    return episodes


def _request_episode() -> str:
    # Transcode helpers are also reached from background threads, where there is
    # no request to read; treating that as "no episode" keeps the cache key stable.
    try:
        return (request.args.get('episode') or '').strip()
    except RuntimeError:
        return ''


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


def _playback_cache_key(media_id: int) -> str:
    """Transcode cache folder name for this request, unique per episode.

    Keyed on media_id alone, two episodes of one show would share a single HLS
    cache and serve each other's segments.
    """
    episode = _request_episode()
    if not episode:
        return str(media_id)
    return f'{media_id}-{hashlib.sha1(episode.encode("utf-8")).hexdigest()[:12]}'


@app.route('/api/library/<int:media_id>/posters')
def library_poster_options(media_id: int):
    """Alternative artwork TMDB holds for a title."""
    item = store.get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    if not tmdb:
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503
    if not item['tmdb_id']:
        return jsonify({'ok': False, 'error': 'no_tmdb_id'}), 409

    posters = tmdb.poster_options(item['tmdb_id'], item['media_type'] or 'movie')
    wanted = (request.args.get('language') or '').strip().lower()
    if wanted and wanted != 'all':
        target = None if wanted == 'none' else wanted
        posters = [p for p in posters if (p['language'] or None) == target]

    return jsonify({
        'ok': True,
        'media_id': media_id,
        'current_poster_url': item['poster_url'],
        'posters': posters[:60],
    })


@app.route('/api/library/<int:media_id>/poster', methods=['POST'])
def library_set_poster(media_id: int):
    """Adopt a chosen TMDB poster as this title's artwork."""
    item = store.get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    payload = request.get_json(silent=True) or {}
    poster_url = (request.form.get('poster_url') or payload.get('poster_url') or '').strip()
    if not poster_url.startswith(TMDB_IMAGE_PREFIX):
        # Only TMDB's own image host is accepted, so this cannot be pointed at
        # an arbitrary URL for the server to fetch.
        return jsonify({'ok': False, 'error': 'invalid_poster_url'}), 400

    cache_key = item['imdb_id'] or str(media_id)
    cached = cache_poster(cache_key, poster_url, force_replace=True)
    if not cached:
        return jsonify({'ok': False, 'error': 'poster_download_failed'}), 502

    store.update_poster(media_id, cached)
    return jsonify({'ok': True, 'media_id': media_id, 'poster_url': cached})


@app.route('/api/discover/missing-episodes')
def discover_missing_episodes():
    if not tmdb:
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503
    shows = _discover_missing_episodes()
    return jsonify({
        'ok': True,
        'shows': shows,
        'show_count': len(shows),
        'episode_count': sum(show['missing_count'] for show in shows),
    })


@app.route('/api/discover/ignore-tv', methods=['POST'])
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
    store.ignore_tv_season(tmdb_id, season_number, title)
    return jsonify({'ok': True, 'tmdb_id': tmdb_id, 'season': season_number})


@app.route('/api/discover/unignore-tv', methods=['POST'])
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
    store.unignore_tv(tmdb_id, season_number)
    return jsonify({'ok': True})


@app.route('/api/tv/<int:media_id>/missing')
def tv_missing_episodes(media_id: int):
    """Aired-but-unowned episodes for one show, for the hero's check button."""
    item = store.get_media_item(media_id)
    if not item or (item['media_type'] or '') != 'tv':
        return jsonify({'ok': False, 'error': 'not_a_tv_show'}), 404
    if not tmdb:
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503

    ignored = store.list_ignored_tv().get(int(item['tmdb_id'] or 0), set())
    show = _missing_episodes_for_show(item, ignored)
    return jsonify({
        'ok': True,
        'media_id': media_id,
        'title': item['title'],
        'show': show,
        'missing_count': show['missing_count'] if show else 0,
    })


@app.route('/api/tv/<int:media_id>/episode-candidates')
def tv_episode_candidates(media_id: int):
    """Torrent candidates for one episode, for the add-to-library flow."""
    item = store.get_media_item(media_id)
    if not item or (item['media_type'] or '') != 'tv':
        return jsonify({'ok': False, 'error': 'not_a_tv_show'}), 404
    try:
        season = int(request.args.get('season'))
        episode = int(request.args.get('episode'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'invalid_episode'}), 400

    title = (item['title'] or '').strip()
    if not title:
        return jsonify({'ok': False, 'error': 'missing_title'}), 400

    # Searched as "Show S04E02" — the year is deliberately left out, since a
    # series year rarely appears in an episode release name.
    query = f'{title} S{season:02d}E{episode:02d}'
    try:
        qb.set_mirror_urls(configured_mirror_urls())
        rows = qb._run_search(query)
    except SearchEngineError as exc:
        return jsonify({'ok': False, 'error': 'search_unavailable', 'detail': str(exc)}), 503

    candidates = []
    for row in rows:
        name = row.get('name') or ''
        candidates.append({
            'name': name,
            'size': row.get('size') or '',
            'seeds': row.get('seeds') or '0',
            'leech': row.get('leech') or '0',
            'desc_link': row.get('desc_link') or '',
            'link': row.get('link') or '',
            'quality': detect_quality(name),
            'trusted': any(group in name.lower() for group in TRUSTED_RELEASE_GROUPS),
        })
    candidates.sort(key=lambda c: (not c['trusted'], -int(c['seeds'] or 0)))
    return jsonify({
        'ok': True,
        'media_id': media_id,
        'query': query,
        'title': f'{title} S{season:02d}E{episode:02d}',
        'candidates': candidates[:25],
    })


@app.route('/api/tv/<int:media_id>/seasons')
def tv_seasons_listing(media_id: int):
    item = store.get_media_item(media_id)
    if not item or (item['media_type'] or '') != 'tv':
        return jsonify({'ok': False, 'error': 'not_a_tv_show'}), 404

    show_path = (item['path'] or '').strip()
    matched, unmatched = scan_local_episodes(show_path, item['title'] or '')

    owned_counts: dict[int, int] = {}
    for season_number, _episode in matched:
        owned_counts[season_number] = owned_counts.get(season_number, 0) + 1

    # The cached status carries the season list too, so the hero learns whether
    # the show is still airing without a second request.
    overview = _cached_tv_status(int(item['tmdb_id'])) if (tmdb and item['tmdb_id']) else {}
    tmdb_names = {}
    tmdb_counts = {}
    for season in overview.get('seasons') or []:
        tmdb_names[season['season_number']] = season['name']
        tmdb_counts[season['season_number']] = season['episode_count']

    # A show whose files carry no SxxExx has no seasons to list, so its folders
    # would offer nothing at all without the extras entry below.
    featurette_seasons: dict[int, int] = {}
    show_level_extras = 0
    for path in unmatched:
        season = _infer_season_from_path(show_path, path)
        if season is None:
            show_level_extras += 1
        else:
            featurette_seasons[season] = featurette_seasons.get(season, 0) + 1

    seasons = [{
        'season_number': number,
        'name': tmdb_names.get(number) or ('Specials' if number == 0 else f'Season {number}'),
        'owned_count': count,
        'episode_count': tmdb_counts.get(number) or count,
        'featurette_count': featurette_seasons.get(number, 0),
    } for number, count in sorted(owned_counts.items())]

    # Seasons that hold only featurettes still deserve an entry.
    for number in sorted(set(featurette_seasons) - set(owned_counts)):
        seasons.append({
            'season_number': number,
            'name': tmdb_names.get(number) or ('Specials' if number == 0 else f'Season {number}'),
            'owned_count': 0,
            'episode_count': tmdb_counts.get(number) or 0,
            'featurette_count': featurette_seasons[number],
        })
    seasons.sort(key=lambda entry: entry['season_number'])

    # Included so the hero can offer its "missing" action to any show with gaps,
    # not just one still airing — an ended show can be missing whole seasons.
    ignored = store.list_ignored_tv().get(int(item['tmdb_id'] or 0), set())
    gaps = _missing_episodes_for_show(item, ignored)

    return jsonify({
        'ok': True,
        'media_id': media_id,
        'title': item['title'],
        'seasons': seasons,
        'extras_count': show_level_extras,
        'unmatched_count': len(unmatched),
        'status': overview.get('status'),
        'in_production': bool(overview.get('in_production')),
        'next_air_date': overview.get('next_air_date'),
        'missing_count': gaps['missing_count'] if gaps else 0,
    })


@app.route('/api/tv/<int:media_id>/season/<int:season_number>')
def tv_season_episodes(media_id: int, season_number: int):
    item = store.get_media_item(media_id)
    if not item or (item['media_type'] or '') != 'tv':
        return jsonify({'ok': False, 'error': 'not_a_tv_show'}), 404

    show_path = (item['path'] or '').strip()
    matched, unmatched = scan_local_episodes(show_path, item['title'] or '')
    metadata = {
        entry['episode_number']: entry
        for entry in (_cached_season_episodes(item['tmdb_id'], season_number) if item['tmdb_id'] else [])
    }

    owned = {number: path for (season, number), path in matched.items() if season == season_number}

    episodes = []
    for number in sorted(owned):
        meta = metadata.get(number, {})
        episodes.append({
            'season_number': season_number,
            'episode_number': number,
            'title': meta.get('title') or f'Episode {number}',
            'synopsis': meta.get('synopsis'),
            'air_date': meta.get('air_date'),
            'runtime': meta.get('runtime'),
            'still_url': meta.get('still_url'),
            'file': os.path.relpath(owned[number], show_path),
        })

    # Files with no SxxExx are listed for the specials/unknown view only, so a
    # show that uses another convention is still playable rather than invisible.
    extras = [] if season_number != 0 else [
        {'name': os.path.basename(path), 'file': os.path.relpath(path, show_path)}
        for path in unmatched
    ]

    return jsonify({
        'ok': True,
        'media_id': media_id,
        'season_number': season_number,
        'episodes': episodes,
        'unmatched': extras,
    })


def _featurette_label(filename: str) -> str:
    """Readable name for a bonus feature, from a release-style filename."""
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', stem)
    stem = re.sub(r'[._]+', ' ', stem)
    # Codec/source tails are left over once the brackets go, e.g. "..._H.264".
    stem = re.sub(
        r'\b(1080p|2160p|720p|480p|x264|x265|HEVC|AAC\d?|AC3|DDP?\d?|H ?26[45]|WEB-?DL|WEBRip|BluRay|DVD|AI Upscale|10bit)\b',
        ' ', stem, flags=re.IGNORECASE,
    )
    stem = re.sub(r'\s+', ' ', stem).strip(' -–_')
    return stem or os.path.basename(filename)


@app.route('/api/tv/<int:media_id>/unmatched')
def tv_unmatched_files(media_id: int):
    """Video files in a show folder with no SxxExx marker.

    These are overwhelmingly featurettes and extras. They are listed separately so
    they stay playable without being given episode numbers they do not have.
    """
    item = store.get_media_item(media_id)
    if not item or (item['media_type'] or '') != 'tv':
        return jsonify({'ok': False, 'error': 'not_a_tv_show'}), 404

    show_path = (item['path'] or '').strip()
    _matched, unmatched = scan_local_episodes(show_path, item['title'] or '')

    # 'season=4' narrows to that season's folder; 'season=extras' returns the
    # show-wide ones that no folder attributes to a season.
    requested = (request.args.get('season') or '').strip().lower()
    files = []
    for path in sorted(unmatched):
        season = _infer_season_from_path(show_path, path)
        if requested == 'extras':
            if season is not None:
                continue
        elif requested:
            if str(season) != requested:
                continue
        basename = os.path.basename(path)
        files.append({
            'name': basename,
            'label': _featurette_label(basename),
            'season': season,
            'file': os.path.relpath(path, show_path),
        })
    return jsonify({'ok': True, 'media_id': media_id, 'files': files})


@app.route('/video/<int:media_id>')
def watch_video(media_id: int):
    """Serve the video player page for a media item."""
    item = store.get_media_item(media_id)
    if not item:
        return 'Not found', 404
    
    item = dict(item)
    video_file = _request_video_file(item)
    if not video_file:
        return 'Video file not found', 404
    
    # Get playback position
    playback = store.get_playback_position(media_id)
    playback = dict(playback) if playback else None
    position_seconds = playback.get('position_seconds', 0) if playback else 0
    duration_seconds = playback.get('duration_seconds') if playback else None

    # Prefer the actual media duration from ffprobe so the player can render a full
    # timeline even when HLS is still being generated and native controls omit it.
    video_info = _get_video_info(video_file)
    if video_info and video_info.get('duration'):
        duration_seconds = video_info['duration']
    
    # Resolve subtitles from the episode being played, not the show folder, or a
    # series would offer every episode's sidecars at once.
    subtitles = _find_subtitle_files(video_file, media_id)

    return render_template(
        'player.html',
        media_id=media_id,
        episode=_request_episode(),
        title=item.get('title') or 'Video Player',
        position_seconds=position_seconds,
        duration_seconds=duration_seconds,
        subtitles=subtitles,
    )


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
# Per-media lock guards segment-endpoint restarts so concurrent hls.js requests cooperate.
_hls_segment_locks: dict[int, threading.Lock] = {}
_hls_segment_locks_guard = threading.Lock()


def _hls_lock_for(media_id: int) -> threading.Lock:
    """Return (creating if needed) the per-media lock used by segment requests."""
    with _hls_segment_locks_guard:
        lock = _hls_segment_locks.get(media_id)
        if lock is None:
            lock = threading.Lock()
            _hls_segment_locks[media_id] = lock
        return lock


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


def _build_vod_playlist(duration: float, seg_len: int = _HLS_SEGMENT_LENGTH,
                        segment_query: str = '') -> str:
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
        with open(manifest_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        return '#EXT-X-ENDLIST' in content
    except Exception:
        return False


def _is_hls_playable(manifest_path: str, min_segments: int = 3) -> bool:
    """Return True when an HLS manifest has enough segments to start playback."""
    if not os.path.isfile(manifest_path):
        return False
    try:
        with open(manifest_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        if not content.startswith('#EXTM3U'):
            return False
        segment_count = content.count('#EXTINF:')
        if segment_count == 0:
            segment_count = content.count('.ts') + content.count('.m4s')
        return segment_count >= min_segments
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
            [FFPROBE_EXE, '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-show_format', video_file],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
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


def _is_direct_play_compatible(info: dict) -> bool:
    """Return True when the file can be served directly without any transcoding.

    Requires H.264 video and a browser-native audio codec. Container is not
    the limiting factor for modern Chromium/Firefox which handle MKV fine.
    """
    return (
        info.get('video_codec') in _DIRECT_PLAY_VIDEO_CODECS
        and info.get('audio_codec') in _DIRECT_PLAY_AUDIO_CODECS
    )


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
        log_handle = open(log_path, 'ab')
    except Exception:
        return None

    seg_len = _HLS_SEGMENT_LENGTH
    # GOP aligned to segment boundary so each segment starts on a keyframe.
    gop_size = seg_len * 30
    internal_playlist = os.path.join(cache_dir, 'internal.m3u8')
    cmd = [
        FFMPEG_EXE,
        '-hide_banner',
        '-loglevel', 'warning',
        '-ss', f'{start_seconds:.3f}',
        '-i', video_file,
        '-map_metadata', '-1',
        '-map_chapters', '-1',
        '-map', '0:v:0',
        '-map', '0:a:0?',
        '-sn',
        '-dn',
        '-max_muxing_queue_size', '2048',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '23',
        '-pix_fmt', 'yuv420p',
        '-force_key_frames:0', f'expr:gte(t,n_forced*{seg_len})',
        '-sc_threshold:v:0', '0',
        '-g', str(gop_size),
        '-keyint_min', str(seg_len),
        '-c:a', 'aac',
        '-ac', '2',
        '-ar', '48000',
        '-b:a', '192k',
        # Keep input timestamps and align the output timeline from the seek point.
        '-copyts',
        '-avoid_negative_ts', 'disabled',
        '-start_at_zero',
        '-f', 'hls',
        '-hls_time', str(seg_len),
        '-hls_list_size', '0',
        '-hls_playlist_type', 'vod',
        '-hls_segment_type', 'mpegts',
        '-hls_flags', 'independent_segments+temp_file',
        '-start_number', str(start_segment),
        '-hls_segment_filename', os.path.join(cache_dir, 'segment_%05d.ts'),
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
        return os.path.join(HLS_CACHE_DIR, _playback_cache_key(media_id), 'direct_stream', 'master.m3u8')

    cache_dir = os.path.join(HLS_CACHE_DIR, _playback_cache_key(media_id), 'direct_stream')
    os.makedirs(cache_dir, exist_ok=True)

    master_m3u8 = os.path.join(cache_dir, 'master.m3u8')
    init_segment = os.path.join(cache_dir, 'init.mp4')
    if _is_hls_complete(master_m3u8) and os.path.isfile(init_segment):
        return master_m3u8
    if os.path.isfile(master_m3u8):
        shutil.rmtree(cache_dir, ignore_errors=True)
        os.makedirs(cache_dir, exist_ok=True)

    try:
        log_path = os.path.join(cache_dir, 'ffmpeg.log')
        log_handle = open(log_path, 'ab')

        cmd = [
            FFMPEG_EXE,
            '-i', video_file,
            '-map', '0:v:0',
            '-map', '0:a:0?',
            '-map', '-0:s',
            '-sn',
            '-dn',
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-ac', '2',
            '-ar', '48000',
            '-b:a', '192k',
            '-f', 'hls',
            '-hls_time', '2',
            '-hls_list_size', '0',
            '-hls_playlist_type', 'event',
            '-hls_flags', 'independent_segments',
            '-hls_segment_type', 'fmp4',
            '-hls_fmp4_init_filename', 'init.mp4',
            '-hls_segment_filename', os.path.join(cache_dir, 'segment_%03d.m4s'),
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
        try:
            shutil.rmtree(cache_dir, ignore_errors=True)
        except Exception:
            pass
        return None


@app.route('/api/video/<int:media_id>/stream')
def stream_video(media_id: int):
    """Stream a video file with range request support (for seeking)."""
    item = store.get_media_item(media_id)
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


@app.route('/api/video/<int:media_id>/subtitle/<int:index>')
def subtitle_file(media_id: int, index: int):
    """Serve a subtitle file by media_id and subtitle index."""
    subtitle_entries = _subtitle_cache.get(media_id)
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
        with open(sub_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        if ext == '.vtt':
            return content, 200, {'Content-Type': 'text/vtt; charset=utf-8'}
        if ext == '.srt':
            return _srt_to_vtt(content), 200, {'Content-Type': 'text/vtt; charset=utf-8'}

        return content, 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception:
        return 'Error reading file', 500


@app.route('/api/video/<int:media_id>/strategy')
def video_strategy(media_id: int):
    """Return the recommended playback strategy for a media item.

    direct_play — file can be served as-is; player should use /stream with range requests.
    hls         — file needs re-encoding; player should use the HLS transcode pipeline.
    """
    item = store.get_media_item(media_id)
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
        return jsonify({
            'ok': True,
            'strategy': 'direct_play',
            'video_codec': info['video_codec'],
            'audio_codec': info['audio_codec'],
        })

    if info.get('video_codec') in _DIRECT_PLAY_VIDEO_CODECS:
        return jsonify({
            'ok': True,
            'strategy': 'direct_stream',
            'video_codec': info['video_codec'],
            'audio_codec': info['audio_codec'],
            'reason': 'audio_codec_incompatible',
        })

    return jsonify({
        'ok': True,
        'strategy': 'hls',
        'video_codec': info['video_codec'],
        'audio_codec': info['audio_codec'],
        'reason': 'codec_incompatible',
    })


@app.route('/api/video/<int:media_id>/direct-stream/status')
def direct_stream_status(media_id: int):
    """Report whether a direct-stream HLS manifest is ready to start playback."""
    item = store.get_media_item(media_id)
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


@app.route('/api/video/<int:media_id>/direct-stream/master.m3u8')
def direct_stream_master_playlist(media_id: int):
    """Serve direct-stream HLS playlist (video copy + audio transcode)."""
    import time

    item = store.get_media_item(media_id)
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
                with open(manifest_path, 'r') as f:
                    content = f.read()
                if content and content.startswith('#EXTM3U'):
                    return _rewrite_playlist_segments(content), 200, {
                        'Content-Type': 'application/vnd.apple.mpegurl',
                        'Cache-Control': 'no-store, max-age=0',
                    }
            except Exception:
                pass

        job = _direct_stream_jobs.get(media_id)
        if job and job['process'].poll() is not None:
            _cleanup_finished_direct_stream_job(media_id)
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, 'r') as f:
                        content = f.read()
                    if content and content.startswith('#EXTM3U'):
                        return _rewrite_playlist_segments(content), 200, {
                            'Content-Type': 'application/vnd.apple.mpegurl',
                            'Cache-Control': 'no-store, max-age=0',
                        }
                except Exception:
                    pass
            return 'Direct streaming error', 500

        time.sleep(wait_interval)
        elapsed += wait_interval

    return 'Stream preparing', 425


@app.route('/api/video/<int:media_id>/direct-stream/<path:filename>')
def direct_stream_segment(media_id: int, filename: str):
    """Serve direct-stream HLS segments and playlists."""
    cache_dir = os.path.join(HLS_CACHE_DIR, _playback_cache_key(media_id), 'direct_stream')

    if '..' in filename or filename.startswith('/'):
        return 'Invalid filename', 400

    file_path = os.path.join(cache_dir, filename)
    if not os.path.isfile(file_path):
        return 'File not found', 404

    try:
        if filename.endswith('.m3u8'):
            with open(file_path, 'r') as f:
                content = f.read()
            return content, 200, {
                'Content-Type': 'application/vnd.apple.mpegurl',
                'Cache-Control': 'no-store, max-age=0',
            }
        if filename.endswith('.m4s'):
            with open(file_path, 'rb') as f:
                content = f.read()
            return content, 200, {
                'Content-Type': 'video/iso.segment',
                'Cache-Control': 'no-store, max-age=0',
                'Pragma': 'no-cache',
                'Expires': '0',
            }
        if filename.endswith('.mp4'):
            with open(file_path, 'rb') as f:
                content = f.read()
            return content, 200, {
                'Content-Type': 'video/mp4',
                'Cache-Control': 'no-store, max-age=0',
                'Pragma': 'no-cache',
                'Expires': '0',
            }
        return 'Unsupported file type', 400
    except Exception:
        return 'Error reading file', 500


@app.route('/api/video/compatibility-report')
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

    rows = store.list_media_items()
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

        report_items.append({
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
        })

        if limit and len(report_items) >= limit:
            break

    return jsonify({
        'ok': True,
        'summary': counts,
        'total_returned': len(report_items),
        'filters': {
            'only_issues': only_issues,
            'limit': limit,
        },
        'items': report_items,
    })


@app.route('/api/video/<int:media_id>/hls/ping', methods=['POST'])
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


@app.route('/api/video/<int:media_id>/hls/status')
def hls_status(media_id: int):
    """Report whether the video can be served via HLS (always immediately ready).

    The Jellyfin pattern generates the playlist from probed duration, so
    readiness is purely a function of being able to probe the source.
    Segments are produced on demand by the segment endpoint.
    """
    item = store.get_media_item(media_id)
    if not item:
        return jsonify({'ok': False, 'error': 'not_found'}), 404

    item = dict(item)
    video_file = _request_video_file(item)
    if not video_file or not os.path.isfile(video_file):
        return jsonify({'ok': False, 'error': 'video_not_found'}), 404

    info = _get_video_info(video_file)
    if not info or not info.get('duration'):
        return jsonify({'ok': False, 'error': 'probe_failed'}), 500

    return jsonify({
        'ok': True,
        'ready': True,
        'duration': info.get('duration'),
        'segment_length': _HLS_SEGMENT_LENGTH,
    })


@app.route('/api/video/<int:media_id>/hls/master.m3u8')
def hls_master_playlist(media_id: int):
    """Return a complete VOD playlist for the entire source duration.

    Segments are not produced here; they are transcoded on demand by the
    segment endpoint when hls.js requests them. This lets the player seek
    anywhere in the timeline without restarting the player.
    """
    item = store.get_media_item(media_id)
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
    return playlist, 200, {
        'Content-Type': 'application/vnd.apple.mpegurl',
        'Cache-Control': 'no-store, max-age=0',
    }


@app.route('/api/video/<int:media_id>/hls/<path:filename>')
def hls_segment(media_id: int, filename: str):
    """Serve HLS segments, transcoding on demand using the Jellyfin pattern.

    For segment_NNNNN.ts: if the file is already on disk and complete,
    serve it. Otherwise check whether the running transcoder is close
    enough to catch up; if so, wait. Otherwise kill the old job and
    start a new FFmpeg run beginning at this segment.
    """
    import time

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
    item = store.get_media_item(media_id)
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

    return content, 200, {
        'Content-Type': 'video/mp2t',
        'Cache-Control': 'no-store, max-age=0',
        'Pragma': 'no-cache',
        'Expires': '0',
    }


@app.route('/api/video/<int:media_id>/playback', methods=['GET', 'POST'])
def playback_position_api(media_id: int):
    """Get or set the playback position for a media item."""
    if request.method == 'GET':
        item = store.get_media_item(media_id)
        if not item:
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        
        playback = store.get_playback_position(media_id)
        playback = dict(playback) if playback else None
        if playback:
            return jsonify({
                'ok': True,
                'position_seconds': playback.get('position_seconds', 0),
                'duration_seconds': playback.get('duration_seconds'),
                'last_updated': playback.get('last_updated'),
            })
        return jsonify({'ok': True, 'position_seconds': 0, 'duration_seconds': None})
    
    elif request.method == 'POST':
        item = store.get_media_item(media_id)
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
        
        store.set_playback_position(media_id, position_seconds, duration_seconds)
        return jsonify({'ok': True, 'position_seconds': position_seconds})
    
    return jsonify({'ok': False, 'error': 'method_not_allowed'}), 405


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
    except Exception as exc:  # noqa: BLE001
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
    store.initialize()
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
            bind_host, RUNNING_PORT,
        )

    app.run(debug=debug_enabled, host=bind_host, port=RUNNING_PORT)
