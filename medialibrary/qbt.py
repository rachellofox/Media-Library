"""Talking to qBittorrent's WebUI.

Adding a torrent, asking after one, and working out which running torrent belongs
to a library item. Matching is the awkward part: qBittorrent reports a torrent's
name, save path and content path, and which of them is usable varies by how the
torrent was added, so all three are compared.

The connection details are half configuration and half stored setting, and this
module must not import the application to read them — that would be a circular
import, since the application imports this. Instead the application hands over a
settings getter at startup via `configure()`. With no getter the module falls
back to the environment alone, which is what the maintenance scripts get.
"""

import base64
import binascii
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from medialibrary.config import (
    QBT_WEBUI_PASSWORD,
    QBT_WEBUI_URL,
    QBT_WEBUI_USERNAME,
)


class QbtUnavailableError(RuntimeError):
    """qBittorrent could not be reached, so its torrent state is unknown."""


_get_setting = None


def configure(get_setting) -> None:
    """Give this module a way to read stored settings.

    Called once by the application after its store exists. Kept as an injected
    callable rather than an import so this module has no dependency on the app.
    """
    global _get_setting
    _get_setting = get_setting


def _setting(key: str) -> str:
    """A stored setting, or '' when nothing has been configured to read them."""
    if _get_setting is None:
        return ''
    try:
        return (_get_setting(key) or '').strip()
    except Exception:
        return ''


QBT_DONE_STATES = {'uploading', 'stalledup', 'seeding', 'pausedup', 'forcedup', 'checkingup'}

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

def _qbt_webui_enabled() -> bool:
    return bool(_qbt_webui_url())

def _qbt_webui_url() -> str:
    return (_setting('qbt_webui_url') or QBT_WEBUI_URL or '').strip().rstrip('/')

def _qbt_webui_username() -> str:
    return (_setting('qbt_webui_username') or QBT_WEBUI_USERNAME or '').strip()

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
        body = _qbt_webui_open('/api/v2/torrents/info?filter=all',
                               method='GET').decode('utf-8', errors='replace')
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
        if folder_norm and (folder_norm in t_content_norm or folder_norm in t_save_norm
                            or folder_norm in t_name_norm):
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
        add_body = _qbt_webui_open(
            '/api/v2/torrents/add', method='POST',
            data=add_payload).decode('utf-8', errors='replace').strip()
    except Exception:
        return False

    # qBittorrent may return an empty body for successful submissions.
    return not add_body.lower().startswith('fails')
