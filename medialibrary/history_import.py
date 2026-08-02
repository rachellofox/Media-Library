"""Reading a watch-history export from another platform.

Three shapes are recognised on sight, because they are the exports someone is
actually likely to have: a Trakt history export (JSON, `type` + `movie`/`show`
keys), a Letterboxd diary or watched-films export (CSV, has a "Letterboxd URI"
column), and an IMDb ratings export (CSV, has "Const" and "Title Type"
columns) — IMDb has no dedicated watch-history export, but a ratings export is
the closest thing most people have, so a row is trusted as "watched" if it
carries a date at all. Anything else falls back to a generic column-matching
reader, for JSON or CSV whose field names aren't known but describe the same
handful of things: a title, a year, a kind, a date.

A record with no usable title is dropped — nothing can be done with it, and
inventing a title would be worse than leaving a gap. Everything else is kept,
even a record with almost nothing else recorded, since it is still evidence
something was watched.
"""

import csv
import io
import json

TV_TYPE_WORDS = {
    'tv', 'show', 'series', 'episode', 'tvseries', 'tvepisode', 'tv_show', 'tvminiseries',
}


def _clean(value) -> str | None:
    text = str(value).strip() if value is not None else ''
    return text or None


def _to_int(value) -> int | None:
    text = _clean(value)
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _media_type_from(value) -> str:
    text = (_clean(value) or '').lower()
    if text in TV_TYPE_WORDS:
        return 'tv'
    return 'movie'  # the common case, and the safer default of the two


class UnrecognisedFormat(Exception):
    """The file parsed as JSON or CSV but matched no known or generic shape."""


def parse_upload(filename: str, raw_bytes: bytes) -> tuple[list[dict], list[dict], str]:
    """Parse an uploaded export. Returns (raw_records, normalised_records, format_name).

    `raw_records` are the untouched source rows — what HistoryStore hashes to
    detect a re-uploaded file — and `normalised_records` are the same rows in
    watch_history's shape, one-to-one with `raw_records` by position.
    """
    name = (filename or '').lower()
    if name.endswith('.json'):
        raw, normalized, fmt = _parse_json(raw_bytes)
    elif name.endswith('.csv'):
        raw, normalized, fmt = _parse_csv(raw_bytes)
    else:
        # Extension missing or unrecognised: sniff the content itself rather
        # than refuse outright, since a browser download can lose or mangle a
        # name.
        stripped = raw_bytes.lstrip()
        if stripped[:1] in (b'[', b'{'):
            raw, normalized, fmt = _parse_json(raw_bytes)
        else:
            raw, normalized, fmt = _parse_csv(raw_bytes)

    # Dropped here, uniformly, rather than per-format: a record with no title
    # is not evidence of anything, and every parser above can produce one from
    # a row that had the wrong columns filled in.
    kept_raw, kept_normalized = [], []
    for raw_record, record in zip(raw, normalized, strict=True):
        if record['title']:
            kept_raw.append(raw_record)
            kept_normalized.append(record)
    return kept_raw, kept_normalized, fmt


def _parse_json(raw_bytes: bytes) -> tuple[list[dict], list[dict], str]:
    try:
        data = json.loads(raw_bytes.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnrecognisedFormat(f'not valid JSON: {exc}') from exc

    if isinstance(data, dict):
        # Some exports wrap the list in an envelope, e.g. {"history": [...]}.
        for value in data.values():
            if isinstance(value, list):
                data = value
                break
    if not isinstance(data, list) or not data:
        raise UnrecognisedFormat('JSON did not contain a non-empty list of records')

    if all(isinstance(item, dict) and 'type' in item and ('movie' in item or 'show' in item)
           for item in data):
        return data, [_from_trakt_entry(item) for item in data], 'trakt-json'

    return data, [_from_generic_record(item) for item in data], 'generic-json'


def _from_trakt_entry(entry: dict) -> dict:
    kind = (_clean(entry.get('type')) or '').lower()
    if kind == 'episode':
        show = entry.get('show') or {}
        episode = entry.get('episode') or {}
        ids = show.get('ids') or {}
        return {
            'title': _clean(show.get('title')),
            'year': _to_int(show.get('year')),
            'media_type': 'tv',
            'season_number': _to_int(episode.get('season')),
            'episode_number': _to_int(episode.get('number')),
            'imdb_id': _clean(ids.get('imdb')),
            'tmdb_id': _to_int(ids.get('tmdb')),
            'watched_at': _clean(entry.get('watched_at')),
        }
    movie = entry.get('movie') or {}
    ids = movie.get('ids') or {}
    return {
        'title': _clean(movie.get('title')),
        'year': _to_int(movie.get('year')),
        'media_type': 'movie',
        'season_number': None,
        'episode_number': None,
        'imdb_id': _clean(ids.get('imdb')),
        'tmdb_id': _to_int(ids.get('tmdb')),
        'watched_at': _clean(entry.get('watched_at')),
    }


_GENERIC_FIELD_ALIASES = {
    'title': ('title', 'name', 'movie', 'show', 'show_title', 'movie_title'),
    'year': ('year', 'release_year'),
    'media_type': ('media_type', 'type', 'kind', 'title_type'),
    'season_number': ('season_number', 'season'),
    'episode_number': ('episode_number', 'episode'),
    'imdb_id': ('imdb_id', 'imdb', 'const', 'imdbid'),
    'tmdb_id': ('tmdb_id', 'tmdb'),
    'watched_at': ('watched_at', 'watched_date', 'date_watched', 'date', 'date_rated', 'watched'),
}


def _lookup(record: dict, field: str, lower_keys: dict):
    for alias in _GENERIC_FIELD_ALIASES[field]:
        if alias in lower_keys:
            return record[lower_keys[alias]]
    return None


def _from_generic_record(record: dict) -> dict:
    lower_keys = {str(k).strip().lower(): k for k in record}
    return {
        'title': _clean(_lookup(record, 'title', lower_keys)),
        'year': _to_int(_lookup(record, 'year', lower_keys)),
        'media_type': _media_type_from(_lookup(record, 'media_type', lower_keys)),
        'season_number': _to_int(_lookup(record, 'season_number', lower_keys)),
        'episode_number': _to_int(_lookup(record, 'episode_number', lower_keys)),
        'imdb_id': _clean(_lookup(record, 'imdb_id', lower_keys)),
        'tmdb_id': _to_int(_lookup(record, 'tmdb_id', lower_keys)),
        'watched_at': _clean(_lookup(record, 'watched_at', lower_keys)),
    }


def _parse_csv(raw_bytes: bytes) -> tuple[list[dict], list[dict], str]:
    try:
        text = raw_bytes.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise UnrecognisedFormat(f'not valid UTF-8 text: {exc}') from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise UnrecognisedFormat('CSV had no header row')
    headers = {h.strip() for h in reader.fieldnames if h}
    rows = [dict(row) for row in reader]
    if not rows:
        raise UnrecognisedFormat('CSV had a header but no data rows')

    if 'Letterboxd URI' in headers:
        return rows, [_from_letterboxd_row(row) for row in rows], 'letterboxd-csv'
    if 'Const' in headers and 'Title Type' in headers:
        return rows, [_from_imdb_row(row) for row in rows], 'imdb-csv'

    normalised = [_from_generic_record(row) for row in rows]
    if not any(record['title'] for record in normalised):
        raise UnrecognisedFormat(
            f'no recognisable title column among: {", ".join(sorted(headers))}'
        )
    return rows, normalised, 'generic-csv'


def _from_letterboxd_row(row: dict) -> dict:
    # "Watched Date" is on a diary export; a plain watched-films export has
    # only "Date", which is itself the watch date on that export.
    watched_at = _clean(row.get('Watched Date')) or _clean(row.get('Date'))
    return {
        'title': _clean(row.get('Name')),
        'year': _to_int(row.get('Year')),
        'media_type': 'movie',  # Letterboxd is films only
        'season_number': None,
        'episode_number': None,
        'imdb_id': None,
        'tmdb_id': None,
        'watched_at': watched_at,
    }


def _from_imdb_row(row: dict) -> dict:
    imdb_id = _clean(row.get('Const'))
    return {
        'title': _clean(row.get('Title')),
        'year': _to_int(row.get('Year')),
        'media_type': _media_type_from(row.get('Title Type')),
        'season_number': None,
        'episode_number': None,
        'imdb_id': imdb_id if (imdb_id or '').startswith('tt') else None,
        'tmdb_id': None,
        'watched_at': _clean(row.get('Date Rated')),
    }
