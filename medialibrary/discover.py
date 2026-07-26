"""Discover: what you could add, and what you are missing.

Three questions, each answered against a different source — collections that are
part-owned, the Trakt watchlist, and episodes that have aired but are not on
disk. All three are slow to compute from scratch (checking 38 shows cold takes
about a minute), so seasons and show status are cached in the database and the
whole block is fetched separately from the rest of the page.

Like the other extracted modules this one must not import the application, so it
is handed accessors at startup. They are *getters* rather than objects on
purpose: `tmdb` is rebuilt whenever the API key changes and `store` is swapped
wholesale by the tests, and holding either directly would leave this module
reading something the rest of the app has replaced.
"""

import re
from datetime import date, datetime, timedelta, timezone

from medialibrary.config import (
    DISCOVER_COLLECTION_CACHE_HOURS,
    DISCOVER_WATCHLIST_CACHE_HOURS,
)
from medialibrary.identify import scan_local_episodes
from storage import Storage

_get_store = None
_get_tmdb = None
_get_trakt_client = None


def configure(*, get_store, get_tmdb, get_trakt_client) -> None:
    """Give this module its way in to the database, TMDB and Trakt."""
    global _get_store, _get_tmdb, _get_trakt_client
    _get_store = get_store
    _get_tmdb = get_tmdb
    _get_trakt_client = get_trakt_client


def _store():
    if _get_store is None:
        raise RuntimeError('medialibrary.discover was never configured')
    return _get_store()


def _tmdb():
    return _get_tmdb() if _get_tmdb else None


def _trakt_client():
    return _get_trakt_client() if _get_trakt_client else None


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
    _store().prune_watchlist_cache(watchlist_ids)

    cached = _store().get_cached_watchlist_entries(DISCOVER_WATCHLIST_CACHE_HOURS)
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
            meta = _tmdb().metadata_by_imdb_id(imdb_id) if _tmdb() else {}
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
        _store().upsert_watchlist_cache_entries(to_cache)
    return out

def _discover_incomplete_collections() -> list[dict]:
    if not _tmdb():
        return []

    ignored_title_ids = _store().list_discover_ignored_title_ids()
    ignored_collection_ids = _store().list_discover_ignored_collection_ids()

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

    for row in _store().list_collection_items():
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
        parts = _store().get_cached_collection_parts(collection_id, DISCOVER_COLLECTION_CACHE_HOURS)
        if parts is None:
            parts = _tmdb().collection_parts(collection_id)
            if parts:
                _store().set_cached_collection_parts(collection_id, parts)
        if not parts:
            continue
        missing = [
            part
            for part in parts
            if (
                part['tmdb_id'] not in group['owned_tmdb_ids']
                and (normalized_title(part.get('title')),
                     part.get('year')) not in group['owned_title_years']
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

    out.sort(key=lambda item: (item['total_count'] - item['owned_count'],
                               item['collection_name'].lower()))
    return out

def _cached_tv_status(tmdb_id: int) -> dict:
    """Airing state and season list for a show, from the database cache."""
    overview = _store().get_cached_tv_status(tmdb_id, DISCOVER_COLLECTION_CACHE_HOURS)
    if overview is None:
        overview = _tmdb().tv_status(tmdb_id) if _tmdb() else {}
        if overview.get('seasons'):
            _store().set_cached_tv_status(tmdb_id, overview)
    return overview or {'status': None, 'in_production': None, 'next_air_date': None, 'seasons': []}

def _missing_episodes_for_show(item, ignored_seasons: set[int]) -> dict | None:
    """Aired-but-unowned episodes for one show, or None when it has no gaps."""
    if not _tmdb() or (item['media_type'] or '') != 'tv' or not item['tmdb_id']:
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

        episodes = _store().get_cached_season(tmdb_id, number, DISCOVER_COLLECTION_CACHE_HOURS)
        if episodes is None:
            episodes = _tmdb().season_episodes(tmdb_id, number)
            if episodes:
                _store().set_cached_season(tmdb_id, number, episodes)
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
    if not _tmdb():
        return []

    ignored = _store().list_ignored_tv()
    out = []
    for item in _store().list_media_items():
        show = _missing_episodes_for_show(item, ignored.get(int(item['tmdb_id'] or 0), set()))
        if show:
            out.append(show)

    # Shows still in production first — new episodes are the point of this view —
    # then by how much is missing.
    out.sort(key=lambda show: (not show['in_production'], -show['missing_count'],
                               (show['title'] or '').lower()))
    return out

# Season metadata rarely changes, and a single user browsing seasons should not
# re-query TMDB on every click. Kept in process rather than in the database
# because losing it on restart costs one request.
_TMDB_SEASON_CACHE: dict[tuple[int, int], tuple[datetime, list[dict]]] = {}

TMDB_SEASON_CACHE_TTL = timedelta(hours=24)

def _cached_season_episodes(tmdb_id: int, season_number: int) -> list[dict]:
    if not _tmdb():
        return []
    key = (int(tmdb_id), int(season_number))
    cached = _TMDB_SEASON_CACHE.get(key)
    if cached and (datetime.now(timezone.utc) - cached[0]) < TMDB_SEASON_CACHE_TTL:
        return cached[1]
    episodes = _tmdb().season_episodes(tmdb_id, season_number)
    if episodes:
        _TMDB_SEASON_CACHE[key] = (datetime.now(timezone.utc), episodes)
    return episodes
