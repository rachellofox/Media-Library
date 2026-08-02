"""Library-wide sweeps: quality, genres, subtitles.

Work that runs over the whole library rather than one title — filling in a
quality that was never detected, backfilling genres TMDB has since supplied, and
the startup sync. Each is written to be safe to re-run: they skip what is already
complete rather than redoing it, because they are triggered by a button someone
may press twice.
"""

import logging
import os

from medialibrary import runtime, tmdb_state
from medialibrary.config import FFPROBE_EXE
from medialibrary.episode_ordering import (
    _resolve_ordering,
    default_episodes,
    planned_from_matched,
)
from medialibrary.identify import VIDEO_EXTENSIONS, scan_local_episodes
from medialibrary.importer import _fetch_best_metadata, import_from_configured_folders
from medialibrary.quality import detect_quality_from_file
from medialibrary.subtitles import _find_video_file


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


def backfill_genres() -> dict:
    if not tmdb_state.client():
        return {'updated': 0, 'skipped': 0, 'error': 'tmdb_not_configured'}

    updated = 0
    skipped = 0
    for item in runtime.store().list_media_items():
        # sqlite3.Row has no __contains__, so `in item` would test the column
        # *values*, not the column names. .keys() is required here.
        has_genre_1 = 'genre_1' in item.keys()  # noqa: SIM118
        current_1 = (item['genre_1'] or '').strip() if has_genre_1 else ''
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
            runtime.store().update_metadata(
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
    if not tmdb_state.client():
        return
    if not any(not (item['genre_1'] or '').strip() for item in runtime.store().list_media_items()):
        return
    result = backfill_genres()
    if result.get('error'):
        return
    logging.getLogger(__name__).info(
        'Genre backfill: %s updated, %s skipped.', result.get('updated'), result.get('skipped')
    )


def _startup_library_sync():
    # Runs once in the background after startup so new files are picked up
    # without requiring a manual sync-library trigger.
    try:
        imported = import_from_configured_folders()
        if imported:
            logging.getLogger(__name__).info(
                'Startup library sync: %d new item(s) imported.', imported
            )

        # Also heal items that were imported before their downloads completed.
        scanned = _scan_missing_quality_items()
        if scanned:
            logging.getLogger(__name__).info(
                'Startup quality scan: %d missing-quality item(s) updated.', scanned
            )

        checked = _detect_tv_episode_orderings()
        if checked:
            logging.getLogger(__name__).info('Startup ordering check: %d show(s) checked.', checked)
    except Exception as exc:
        logging.getLogger(__name__).warning('Startup library sync failed: %s', exc)


def _scan_missing_quality_items() -> int:
    """Detect local quality for items that currently have no stored quality."""
    updated = 0
    for item in runtime.store().list_media_items():
        # Subscript, not .get: these are sqlite3.Row, which has no .get. Using it
        # raised on the first item, and the caller logs the exception as a
        # warning and carries on — so this had silently never run.
        if str(item['current_quality'] or '').strip():
            continue
        path = item['path']
        if not path:
            continue

        target = _find_video_file(path)
        if not target:
            continue

        quality = detect_quality_from_file(target, ffprobe_exe=FFPROBE_EXE)
        if not quality:
            continue

        runtime.store().update_quality(item['id'], quality)
        updated += 1
    return updated


def _detect_tv_episode_orderings() -> int:
    """Work out, once per show, which episode numbering its files actually use.

    Reads every marked file's running time and (where present) its embedded
    title — see `medialibrary.episode_ordering` — which is too slow to repeat
    per page load. So this runs once here, in the startup background thread,
    and persists what it finds; `/api/tv/<id>/season/<n>` and
    `/api/tv/<id>/next-episode` read the stored answer rather than detecting
    again. A show already checked (row present, whatever it says) is skipped,
    so a large library is only ever probed once — see
    `Storage.clear_episode_ordering` for how a stale answer gets forgotten.

    One show's failure — a TMDB outage mid-loop, an unreadable file — is caught
    per item rather than at the top, so it costs that show's check, not every
    show queued after it.
    """
    checked = 0
    for item in runtime.store().list_media_items():
        if (item['media_type'] or '') != 'tv':
            continue
        if not item['tmdb_id']:
            continue
        if runtime.store().get_episode_ordering(item['id']) is not None:
            continue  # already checked, whatever the answer was

        show_path = (item['path'] or '').strip()
        if not show_path or not os.path.isdir(show_path):
            continue

        try:
            matched, _unmatched = scan_local_episodes(show_path, item['title'] or '')
            planned = planned_from_matched(show_path, matched)
            episodes = default_episodes(item['tmdb_id'])
            if not planned or not episodes:
                # Nothing to compare yet — recorded anyway, so an empty or
                # not-yet-imported show does not get re-walked every startup.
                runtime.store().set_episode_ordering(item['id'], None)
                checked += 1
                continue

            notes: list = []
            left_alone: list = []
            _episodes, untitled, ordering = _resolve_ordering(
                item['title'] or '', item['tmdb_id'], planned, episodes, notes, left_alone
            )
            runtime.store().set_episode_ordering(
                item['id'],
                ordering['id'] if ordering else None,
                ordering['name'] if ordering else None,
                ordering['kind'] if ordering else None,
                untitled_seasons=untitled,
            )
            checked += 1
            if ordering:
                logging.getLogger(__name__).info(
                    'Episode ordering for %s: adopted %s (%s).',
                    item['title'],
                    ordering['name'],
                    ordering['kind'],
                )
            elif untitled:
                logging.getLogger(__name__).info(
                    'Episode ordering for %s: no published ordering fits; '
                    'season(s) %s left untitled.',
                    item['title'],
                    sorted(untitled),
                )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                'Episode ordering check failed for %s: %s', item['title'], exc
            )
    return checked
