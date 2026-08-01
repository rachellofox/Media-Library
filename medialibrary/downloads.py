"""Turning a finished download into a library file.

This is the code that moves and deletes things, and it has caused data loss
twice, so the guards here are load-bearing rather than defensive habit:

- nothing is finalised until qBittorrent reports the download fully written,
  because a torrent that is still checking looks complete by size alone;
- a finished TV download is filed as a single episode, never by recycling the
  show folder it belongs to;
- the file being replaced is resolved *before* the new one is put in place, or
  the resolution finds the new file and retires it;
- when a folder holds more than one video, nothing is retired at all, because
  which one belongs to this item is a guess and the wrong guess takes an
  unrelated title with it;
- files are moved with os.rename, never shutil.move, which silently degrades to
  copy-then-delete across a locked file and once duplicated 10 GB.

Finalisation also runs from a page load, so concurrent requests are serialised
through one lock rather than racing into the same media item.

Like the other extracted modules this one is handed its dependencies rather than
importing the application, and they are getters because the tests swap the store
wholesale.
"""

import os
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone

# Imported as a module, not by name: these are the functions the tests stub to
# simulate qBittorrent being down or holding a particular torrent, and a
# from-import would bind them here at import time and ignore the stub.
from medialibrary import qbt
from medialibrary.config import FFPROBE_EXE
from medialibrary.episode_match import names_other_show
from medialibrary.identify import (
    EPISODE_MARKER,
    _best_local_video_path,
    _episodes_covered,
    _videos_in,
    scan_local_episodes,
)
from medialibrary.naming import canonical_paths, canonical_stem
from medialibrary.qbt import QbtUnavailableError
from medialibrary.quality import compare_quality, detect_quality, detect_quality_from_file
from medialibrary.subtitles import scan_subtitles

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

_get_store = None
_get_logger = None


def configure(*, get_store, get_logger) -> None:
    """Give this module its database and somewhere to log to."""
    global _get_store, _get_logger
    _get_store = get_store
    _get_logger = get_logger


def _store():
    if _get_store is None:
        raise RuntimeError('medialibrary.downloads was never configured')
    return _get_store()


class _NullLogger:
    """Used only before configure(), so a stray call cannot raise."""

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _log():
    return _get_logger() if _get_logger else _NullLogger()


def _refresh_local_media_signals(media_id: int, media_path: str | None) -> None:
    best_video = _best_local_video_path(media_path)
    if best_video:
        quality = detect_quality_from_file(best_video, ffprobe_exe=FFPROBE_EXE) or detect_quality(
            os.path.basename(best_video)
        )
        if quality:
            _store().update_quality(media_id, quality)
    subs = scan_subtitles(media_path)
    if subs:
        _store().update_subtitles(media_id, subs)


def _staging_path_for(media_type: str) -> str:
    """Where in-progress downloads land before being finalised into the library.

    Falls back to '' when unset so callers keep the previous behaviour of
    downloading straight into the library root.
    """
    configured = (_store().get_setting('downloads_path') or '').strip()
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
        _log().warning('send2trash unavailable; leaving superseded file in place: %s', target)
        return False
    try:
        send2trash(os.path.abspath(target))
        return True
    except Exception as exc:
        _log().warning('Could not recycle %s: %s', target, exc)
        return False


def _is_download_state_stale(updated_at_text: str | None, stale_after: timedelta) -> bool:
    if not updated_at_text:
        return False
    try:
        updated_at = datetime.strptime(updated_at_text.strip(), '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=timezone.utc
        )
    except Exception:
        return False
    return (datetime.now(timezone.utc) - updated_at) >= stale_after


# Finalisation moves files, and _auto_finalize runs on every page load, so
# concurrent requests must not race each other into the same media item.
_FINALIZE_LOCK = threading.Lock()


def _library_root_for(media_type: str) -> str:
    setting = 'tv_path' if (media_type or 'movie') == 'tv' else 'movies_path'
    return (_store().get_setting(setting) or '').strip()


def _place_video_in_library(source_video: str, dest_file: str) -> str | None:
    """Put a finished download at its canonical library path.

    Hardlink first: it is instant, consumes no extra space, and leaves the
    torrent's own copy untouched so qBittorrent keeps seeding. Falls back to a
    copy when the staging area and library sit on different volumes.
    Returns the strategy used, or None on failure.
    """
    try:
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
    except Exception as exc:
        _log().warning('Could not create library folder for %s: %s', dest_file, exc)
        return None

    if os.path.exists(dest_file):
        if os.path.samefile(source_video, dest_file):
            return 'already-linked'
        _log().warning('Canonical destination already occupied: %s', dest_file)
        return None

    try:
        os.link(source_video, dest_file)
        return 'hardlink'
    except OSError:
        pass  # different volume, or filesystem without hardlink support

    try:
        shutil.copy2(source_video, dest_file)
        return 'copy'
    except Exception as exc:
        _log().warning('Could not place %s into library: %s', source_video, exc)
        return None


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
        _log().info(
            'Media %s: leaving TV download in place (show folder or SxxExx missing).', media_id
        )
        _store().clear_download_state(media_id)
        return

    # The scan refuses a file whose name belongs to a different show, but filing
    # a download *renames* it to this show's convention — so the evidence is gone
    # a moment later and the guard can never fire. It has to be applied here or
    # not at all. This is how a Chernobyl episode became Parks and Recreation
    # S01E01 once already; that arrived by being misplaced on disk, and this is
    # the same outcome reached by downloading it.
    if names_other_show(os.path.basename(new_video), row['title'] or ''):
        _store().set_download_state(
            media_item_id=media_id,
            status='needs_review',
            source=row['download_source'] or 'qb_webui',
            message=(
                f'{os.path.basename(new_video)} looks like a different show, so it '
                f'was not filed under {row["title"]}. The file is untouched.'
            ),
        )
        _log().warning('Media %s: refused %s — its name is not this show.', media_id, new_video)
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
            _store().set_download_state(
                media_item_id=media_id,
                status='needs_review',
                source=row['download_source'] or 'qb_webui',
                message=(
                    f'S{season:02d}E{episode:02d}: downloaded '
                    f'{new_quality or "unknown"} is not '
                    f'better than existing {existing_quality or "unknown"}'
                ),
            )
            return

    if os.path.exists(dest_file) and not (
        existing_file and os.path.samefile(existing_file, dest_file)
    ):
        _log().info('Media %s: %s already exists, leaving download in place.', media_id, dest_file)
        _store().clear_download_state(media_id)
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
            _log().info(
                'Media %s: retired superseded S%02dE%02d file %s',
                media_id,
                season,
                episode,
                existing_file,
            )
        elif not covers_only_this:
            _log().info(
                'Media %s: kept %s, it also covers other episodes.', media_id, existing_file
            )

    if landing != dest_file:
        try:
            os.replace(landing, dest_file)
        except OSError as exc:
            _log().warning('Could not rename %s into place: %s', landing, exc)
            return

    _log().info('Media %s: filed S%02dE%02d at %s', media_id, season, episode, dest_file)
    try:
        _refresh_local_media_signals(media_id, show_path)
    except Exception:
        pass
    if new_quality:
        _store().update_quality(media_id, new_quality)
    _store().clear_download_state(media_id)


def _finalize_completed_download(row, torrent: dict) -> None:
    """Move one finished download into the library under its canonical name."""
    media_id = int(row['id'])
    media_type = row['media_type'] or 'movie'
    mode = (row['download_mode'] or 'fill').strip().lower()
    previous_path = (row['download_previous_path'] or '').strip() or (row['path'] or '').strip()

    new_video = _best_local_video_path((torrent.get('content_path') or '').strip())
    if not new_video:
        return  # complete per qB but nothing playable yet; retry next pass

    new_quality = detect_quality_from_file(new_video, ffprobe_exe=FFPROBE_EXE) or detect_quality(
        os.path.basename(new_video)
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
        old_quality = row['current_quality'] or detect_quality_from_file(
            outgoing_video, ffprobe_exe=FFPROBE_EXE
        )
        if compare_quality(old_quality, new_quality) <= 0:
            _store().set_download_state(
                media_item_id=media_id,
                status='needs_review',
                source=row['download_source'] or 'qb_webui',
                message=(
                    f'Downloaded {new_quality or "unknown"} is not better than '
                    f'existing {old_quality or "unknown"}'
                ),
            )
            _log().info(
                'Upgrade for media %s rejected: %s is not better than %s',
                media_id,
                new_quality,
                old_quality,
            )
            return

    library_root = _library_root_for(media_type)
    destination = canonical_paths(
        library_root,
        row['title'] or '',
        row['year'],
        os.path.splitext(new_video)[1],
        media_type,
    )
    if not destination:
        _log().warning('No canonical name for media %s; leaving download in place.', media_id)
        return
    dest_folder, dest_file = destination

    # The canonical destination is often occupied by the very file being
    # replaced (an earlier release already correctly named). Land the
    # replacement beside it and only take its name once the old one is safely
    # recycled, so nothing is destroyed before the new file is really there.
    replacing_in_place = bool(
        outgoing_video and os.path.isfile(dest_file) and os.path.samefile(dest_file, outgoing_video)
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
            _log().warning(
                'Could not recycle %s; upgrade for media %s abandoned.', outgoing_video, media_id
            )
            return
        try:
            os.replace(landing, dest_file)
        except OSError as exc:
            _log().warning('Could not rename %s into place: %s', landing, exc)
            return
        outgoing_video = None  # retired above; skip the generic retire below

    _log().info('Placed media %s at %s (%s).', media_id, dest_file, strategy)

    # Only retire the old file once the replacement is verifiably in place, and
    # never when it resolved to the very file we just wrote.
    if mode == 'upgrade' and outgoing_video and os.path.isfile(dest_file):  # noqa: SIM102
        # Kept nested: the outer test is the guard that makes samefile() safe to
        # call at all, and the two are separate conditions rather than one.
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
                    _log().info(
                        'Media %s: %s holds %d videos, so nothing was retired. The superseded '
                        'file may need clearing up by hand.',
                        media_id,
                        previous_path,
                        len(siblings),
                    )
            if retire_target and _retire_path(retire_target):
                _log().info('Recycled superseded media for %s: %s', media_id, retire_target)

    _store().update_path(media_id, dest_folder)
    try:
        _refresh_local_media_signals(media_id, dest_folder)
    except Exception:
        pass
    # Canonical filenames carry no quality token, so re-detection after the
    # rename must read the file itself; fall back to what the release claimed.
    placed_quality = detect_quality_from_file(dest_file, ffprobe_exe=FFPROBE_EXE) or new_quality
    if placed_quality:
        _store().update_quality(media_id, placed_quality)
    # The stored upgrade result was measured against the file we just replaced.
    _store().clear_quality_checks(media_id)
    _store().clear_download_state(media_id)


def _readopt_orphaned_downloads(torrents: list[dict]) -> None:
    """Relink items that lost their download state while a torrent still runs.

    Without this a title with no file and no download state is invisible: the
    library view lists items by local file or active download, so it silently
    drops out and only reappears as a gap in Discover. qBittorrent is the source
    of truth here, so the link can simply be rebuilt from it.
    """
    for row in _store().list_media_items():
        if (row['download_status'] or '').strip():
            continue
        if _best_local_video_path(row['path']):
            continue

        torrent = qbt._qbt_match_torrent_for_item(dict(row), torrents=torrents)
        if not torrent:
            continue

        # The matcher accepts a title-only hit; requiring the year as well keeps
        # a sequel or same-named release from being adopted by mistake.
        year = str(row['year'] or '').strip()
        if year and year not in qbt._norm_match_text(torrent.get('name')):
            continue

        info_hash = (torrent.get('hash') or '').strip().upper()
        _store().set_download_state(
            media_item_id=int(row['id']),
            status='downloading',
            source='qb_webui',
            message='Relinked to an active qBittorrent download',
            torrent_hash=info_hash if re.fullmatch(r'[0-9A-F]{40}', info_hash) else None,
            mode='fill',
        )
        _log().info(
            'Relinked media %s (%s) to torrent %r',
            row['id'],
            row['title'],
            torrent.get('name'),
        )


def _auto_finalize_qb_completed_downloads() -> None:
    if not qbt._qbt_webui_enabled():
        return
    if not _FINALIZE_LOCK.acquire(blocking=False):
        return  # another request is already finalising; skip this pass

    try:
        active_states = {'starting', 'handed_off', 'downloading'}
        stale_after = timedelta(hours=12)
        try:
            torrents = qbt._qbt_webui_torrents_info()
        except QbtUnavailableError as exc:
            # Reconciling against an unknown state would read a brief outage as
            # proof that every download had vanished, and clear them all.
            _log().warning('Skipping download reconciliation, qBittorrent unreachable: %s', exc)
            return

        for row in _store().list_media_items():
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
                    torrent = qbt._qbt_webui_torrent_info(torrent_hash)
                if not torrent:
                    torrent = qbt._qbt_match_torrent_for_item(dict(row), torrents=torrents)
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
                    _store().clear_download_state(media_id)
                    continue
                if _is_download_state_stale(row['download_updated_at'], stale_after):
                    _store().clear_download_state(media_id)
                continue

            if not qbt._torrent_is_complete(torrent):
                continue

            try:
                _finalize_completed_download(row, torrent)
            except Exception as exc:
                _log().warning('Finalising download for media %s failed: %s', media_id, exc)

        _readopt_orphaned_downloads(torrents)
    finally:
        _FINALIZE_LOCK.release()
