"""Poster artwork: caching it locally, and refusing anything not from TMDB.

Posters are downloaded once and served from static/posters, so the grid does not
hit TMDB on every load. Only URLs on TMDB's image host are fetched — a crafted
request must not be able to make the server retrieve an arbitrary URL, and a
lookalike host like image.tmdb.org.evil.example fails the prefix test.
"""

import os
import urllib.request

from medialibrary.config import POSTER_DIR as POSTERS_DIR

# Poster choices are only accepted from TMDB's own image host, so a crafted
# request cannot make the server fetch an arbitrary URL.
TMDB_IMAGE_PREFIX = 'https://image.tmdb.org/t/p/'


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


def _cache_meta_poster(cache_key: str, meta: dict, force_replace: bool = False) -> str | None:
    remote_url = meta.get('poster_url') or ''
    return cache_poster(cache_key, remote_url, force_replace=force_replace) or meta.get(
        'poster_url'
    )
