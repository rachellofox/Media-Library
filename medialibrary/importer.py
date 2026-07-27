"""Bringing folders on disk into the library.

The scan is deliberately conservative. A folder with no video in it is not a
title — that was the bug where an empty folder beside a real one kept
resurrecting as "missing" — and a scan may never repoint an item that already
has a playable file. A folder holding several feature-sized videos that each
carry their own year is a pack, and is imported as several titles rather than
skipped.
"""

import os

from medialibrary import runtime, tmdb_state
from medialibrary.config import FFPROBE_EXE
from medialibrary.identify import (
    VIDEO_EXTENSIONS,
    _best_local_video_path,
    _is_local_media_missing,
    _normalized_title_tokens,
    _pack_films_in,
    _titles_likely_match,
    choose_search_result,
    normalize_media_name,
)
from medialibrary.posters import cache_poster
from medialibrary.quality import detect_quality, detect_quality_from_file
from medialibrary.subtitles import scan_subtitles


def scan_media_entries(folder_path: str, media_type: str) -> list[dict[str, str]]:
    """Return importable media entries from a configured library folder."""
    if not folder_path or not os.path.isdir(folder_path):
        return []

    # Guards against a staging folder configured inside the library root: a
    # part-finished download must never be imported as a library title.
    staging = (runtime.store().get_setting('downloads_path') or '').strip()
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
            if entry.is_file() and media_type in ('movie', 'tv'):
                _, ext = os.path.splitext(entry.name)
                if ext.lower() in VIDEO_EXTENSIONS:
                    entries.append({'name': entry.name, 'path': entry.path})
    except PermissionError:
        return []

    return sorted(entries, key=lambda item: item['name'].lower())


def import_media_from_paths(folder_path: str, media_type: str) -> int:
    """Import missing items from a configured media folder into the local library."""
    existing_items = runtime.store().list_media_items()
    existing_paths = {
        os.path.normcase(os.path.normpath(item['path'])) for item in existing_items if item['path']
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
        match = choose_search_result(
            tmdb_state.client().search(query, max_results=10) if tmdb_state.client() else [],
            title,
            media_type,
            year,
        )
        if not match:
            continue

        meta = (
            tmdb_state.client().metadata_by_tmdb_id(match['tmdb_id'], match['media_type'])
            if tmdb_state.client()
            else {}
        )
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

        poster_url = cache_poster(
            meta.get('imdb_id') or entry['name'], meta.get('poster_url') or ''
        ) or meta.get('poster_url')

        # Resolve the actual video file for quality detection; entry path may be a
        # folder (e.g. TV show)
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

        runtime.store().add_media_item(
            imdb_id=meta['imdb_id'],
            tmdb_id=meta.get('tmdb_id'),
            title=meta['title'],
            year=meta['year'],
            media_type=meta['media_type'],
            collection_id=meta.get('collection_id'),
            collection_name=meta.get('collection_name'),
            current_quality=(
                detect_quality_from_file(quality_target, ffprobe_exe=FFPROBE_EXE)
                or detect_quality(entry['name'])
            ),
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
    movies_path = runtime.store().get_setting('movies_path') or ''
    tv_path = runtime.store().get_setting('tv_path') or ''
    imported = 0
    if movies_path:
        imported += import_media_from_paths(movies_path, 'movie')
    if tv_path:
        imported += import_media_from_paths(tv_path, 'tv')
    return imported


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

    # .keys() is required: sqlite3.Row has no __contains__, so `in item` would
    # test the column values instead of the column names.
    stored_tmdb_id = item['tmdb_id'] if 'tmdb_id' in item.keys() else None  # noqa: SIM118
    if stored_tmdb_id:
        exact = tmdb_state.client().metadata_by_tmdb_id(stored_tmdb_id, media_type)
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
        meta = tmdb_state.client().metadata_by_imdb_id(imdb_id)

    if meta.get('poster_url'):
        same_type = (meta.get('media_type') or media_type) == media_type
        expected_title = fallback_title or item.get('title')
        title_matches = (
            _titles_likely_match(expected_title, meta.get('title')) if expected_title else True
        )
        year_matches = (
            year is None or meta.get('year') is None or abs(int(meta.get('year')) - int(year)) <= 1
        )
        if same_type and title_matches and year_matches:
            return meta

    if not fallback_title:
        return meta

    def _pick_match(query: str):
        results = tmdb_state.client().search(query, max_results=15)
        return choose_search_result(results, fallback_title, media_type, year)

    def _year_distance(candidate: dict | None) -> int:
        if year is None or not candidate or not candidate.get('year'):
            return 999
        return abs(int(candidate.get('year')) - int(year))

    match = _pick_match(fallback_title)

    # Short titles are often ambiguous on TMDB (e.g. TAR); retry with year.
    token_count = len(_normalized_title_tokens(fallback_title))
    if year is not None:
        precise_results = tmdb_state.client().search_precise(
            fallback_title, media_type, year=year, max_results=15
        )
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

    return tmdb_state.client().metadata_by_tmdb_id(match['tmdb_id'], match['media_type'])
