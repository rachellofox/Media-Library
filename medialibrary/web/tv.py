"""TV routes: seasons, episodes, and what is missing from a show.

Seasons and episodes are assembled from two sources that disagree more often
than they should — the files on disk and TMDB's episode list — so the local
scan leads and TMDB supplies titles, synopses and stills where the numbering
lines up. Files that carry no usable episode number are returned separately
rather than being given one.
"""

import os

from flask import Blueprint, jsonify, request

from medialibrary import runtime
from medialibrary.config import TRUSTED_RELEASE_GROUPS
from medialibrary.discover import (
    _cached_tv_status,
    _missing_episodes_for_show,
)
from medialibrary.episode_ordering import resolved_episodes
from medialibrary.identify import (
    _episodes_covered,
    _featurette_label,
    _infer_season_from_path,
    duplicate_episode_files,
    scan_local_episodes,
)
from medialibrary.qb_search import SearchEngineError, configured_mirror_urls
from medialibrary.quality import detect_quality

bp = Blueprint('tv', __name__)


@bp.route('/api/tv/<int:media_id>/missing')
def tv_missing_episodes(media_id: int):
    """Aired-but-unowned episodes for one show, for the hero's check button."""
    item = runtime.store().get_media_item(media_id)
    if not item or (item['media_type'] or '') != 'tv':
        return jsonify({'ok': False, 'error': 'not_a_tv_show'}), 404
    if not runtime.tmdb():
        return jsonify({'ok': False, 'error': 'tmdb_not_configured'}), 503

    ignored = runtime.store().list_ignored_tv().get(int(item['tmdb_id'] or 0), set())
    show = _missing_episodes_for_show(item, ignored)
    return jsonify(
        {
            'ok': True,
            'media_id': media_id,
            'title': item['title'],
            'show': show,
            'missing_count': show['missing_count'] if show else 0,
        }
    )


@bp.route('/api/tv/<int:media_id>/episode-candidates')
def tv_episode_candidates(media_id: int):
    """Torrent candidates for one episode, for the add-to-library flow."""
    item = runtime.store().get_media_item(media_id)
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
        runtime.qb().set_mirror_urls(configured_mirror_urls())
        rows = runtime.qb()._run_search(query)
    except SearchEngineError as exc:
        return jsonify({'ok': False, 'error': 'search_unavailable', 'detail': str(exc)}), 503

    candidates = []
    for row in rows:
        name = row.get('name') or ''
        candidates.append(
            {
                'name': name,
                'size': row.get('size') or '',
                'seeds': row.get('seeds') or '0',
                'leech': row.get('leech') or '0',
                'desc_link': row.get('desc_link') or '',
                'link': row.get('link') or '',
                'quality': detect_quality(name),
                'trusted': any(group in name.lower() for group in TRUSTED_RELEASE_GROUPS),
            }
        )
    candidates.sort(key=lambda c: (not c['trusted'], -int(c['seeds'] or 0)))
    return jsonify(
        {
            'ok': True,
            'media_id': media_id,
            'query': query,
            'title': f'{title} S{season:02d}E{episode:02d}',
            'candidates': candidates[:25],
        }
    )


@bp.route('/api/tv/<int:media_id>/seasons')
def tv_seasons_listing(media_id: int):
    item = runtime.store().get_media_item(media_id)
    if not item or (item['media_type'] or '') != 'tv':
        return jsonify({'ok': False, 'error': 'not_a_tv_show'}), 404

    show_path = (item['path'] or '').strip()
    matched, unmatched = scan_local_episodes(show_path, item['title'] or '')

    owned_counts: dict[int, int] = {}
    for season_number, _episode in matched:
        owned_counts[season_number] = owned_counts.get(season_number, 0) + 1

    # The cached status carries the season list too, so the hero learns whether
    # the show is still airing without a second request.
    overview = (
        _cached_tv_status(int(item['tmdb_id'])) if (runtime.tmdb() and item['tmdb_id']) else {}
    )
    tmdb_names = {}
    tmdb_counts = {}
    for season in overview.get('seasons') or []:
        tmdb_names[season['season_number']] = season['name']
        tmdb_counts[season['season_number']] = season['episode_count']

    # Batman is 28/28/29 to a season on disk against TMDB's default 60/10/10 —
    # if a different ordering was adopted for this show, its own season sizes
    # replace the default's, or "28 of 60" would show for a season that is
    # actually complete. Names are left to fall back to "Season N": an
    # ordering's own season labels are not published the way a show's are, so a
    # generic name is more honest than borrowing ones that no longer match.
    ordering = runtime.store().get_episode_ordering(media_id)
    if ordering and ordering['ordering_id']:
        tmdb_counts = {}
        for entry in resolved_episodes(media_id, item['tmdb_id']):
            tmdb_counts[entry['season_number']] = tmdb_counts.get(entry['season_number'], 0) + 1
        tmdb_names = {}

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

    seasons = [
        {
            'season_number': number,
            'name': tmdb_names.get(number) or ('Specials' if number == 0 else f'Season {number}'),
            'owned_count': count,
            'episode_count': tmdb_counts.get(number) or count,
            'featurette_count': featurette_seasons.get(number, 0),
        }
        for number, count in sorted(owned_counts.items())
    ]

    # Seasons that hold only featurettes still deserve an entry.
    for number in sorted(set(featurette_seasons) - set(owned_counts)):
        seasons.append(
            {
                'season_number': number,
                'name': tmdb_names.get(number)
                or ('Specials' if number == 0 else f'Season {number}'),
                'owned_count': 0,
                'episode_count': tmdb_counts.get(number) or 0,
                'featurette_count': featurette_seasons[number],
            }
        )
    seasons.sort(key=lambda entry: entry['season_number'])

    # Included so the hero can offer its "missing" action to any show with gaps,
    # not just one still airing — an ended show can be missing whole seasons.
    ignored = runtime.store().list_ignored_tv().get(int(item['tmdb_id'] or 0), set())
    gaps = _missing_episodes_for_show(item, ignored)

    return jsonify(
        {
            'ok': True,
            'media_id': media_id,
            'title': item['title'],
            'seasons': seasons,
            'extras_count': show_level_extras,
            # A flag only. Which copy to keep is a judgement about codecs,
            # subtitles and disc space that no rule here can make, so the app
            # says how many episodes need looking at and changes nothing.
            'duplicate_count': len(duplicate_episode_files(show_path, item['title'] or '')),
            'unmatched_count': len(unmatched),
            'status': overview.get('status'),
            'in_production': bool(overview.get('in_production')),
            'next_air_date': overview.get('next_air_date'),
            'missing_count': gaps['missing_count'] if gaps else 0,
        }
    )


@bp.route('/api/tv/<int:media_id>/season/<int:season_number>')
def tv_season_episodes(media_id: int, season_number: int):
    item = runtime.store().get_media_item(media_id)
    if not item or (item['media_type'] or '') != 'tv':
        return jsonify({'ok': False, 'error': 'not_a_tv_show'}), 404

    show_path = (item['path'] or '').strip()
    matched, unmatched = scan_local_episodes(show_path, item['title'] or '')
    # Reads whichever ordering was adopted for this show (see
    # medialibrary.episode_ordering) — TMDB's default unless detection found
    # the files use a different one, in which case "episode 2" here is that
    # ordering's episode 2, matching what the filenames actually mean.
    metadata = {
        entry['episode_number']: entry
        for entry in resolved_episodes(media_id, item['tmdb_id'], season_number)
    }

    owned = {number: path for (season, number), path in matched.items() if season == season_number}
    positions = runtime.store().list_playback_positions(media_id)

    episodes = []
    for number in sorted(owned):
        meta = metadata.get(number, {})
        file_key = os.path.relpath(owned[number], show_path)
        progress = positions.get(file_key)
        episodes.append(
            {
                'season_number': season_number,
                'episode_number': number,
                'title': meta.get('title') or f'Episode {number}',
                'synopsis': meta.get('synopsis'),
                'air_date': meta.get('air_date'),
                'runtime': meta.get('runtime'),
                'still_url': meta.get('still_url'),
                'file': file_key,
                'watched': bool(progress and progress['watched']),
                # Omitted rather than 0 when nothing is saved, so the front end
                # can tell "never started" from "resumed at the very start".
                'resume_seconds': (
                    progress['position_seconds'] if progress and not progress['watched'] else None
                ),
            }
        )

    # Files with no SxxExx are listed for the specials/unknown view only, so a
    # show that uses another convention is still playable rather than invisible.
    extras = (
        []
        if season_number != 0
        else [
            {'name': os.path.basename(path), 'file': os.path.relpath(path, show_path)}
            for path in unmatched
        ]
    )

    return jsonify(
        {
            'ok': True,
            'media_id': media_id,
            'season_number': season_number,
            'episodes': episodes,
            'unmatched': extras,
        }
    )


@bp.route('/api/tv/<int:media_id>/next-episode')
def tv_next_episode(media_id: int):
    """The episode after the one named by `?episode=`, for "up next".

    Ordered by (season, episode_number) over what scan_local_episodes actually
    found on disk — the same ordering the season list renders — so "next" means
    the next row of that list, never the next filename alphabetically or an
    episode TMDB lists but the library does not hold.
    """
    item = runtime.store().get_media_item(media_id)
    if not item or (item['media_type'] or '') != 'tv':
        return jsonify({'ok': False, 'error': 'not_a_tv_show'}), 404

    current = (request.args.get('episode') or '').strip()
    covered = _episodes_covered(os.path.basename(current)) if current else None
    if not covered:
        return jsonify({'ok': True, 'next': None})

    show_path = (item['path'] or '').strip()
    matched, _unmatched = scan_local_episodes(show_path, item['title'] or '')

    current_key = (covered[0], covered[1][0])
    upcoming = sorted(key for key in matched if key > current_key)
    if not upcoming:
        return jsonify({'ok': True, 'next': None})

    season, number = upcoming[0]
    metadata = resolved_episodes(media_id, item['tmdb_id'], season)
    meta = next((e for e in metadata if e['episode_number'] == number), {})

    return jsonify(
        {
            'ok': True,
            'next': {
                'season_number': season,
                'episode_number': number,
                'title': meta.get('title') or f'Episode {number}',
                'still_url': meta.get('still_url'),
                'file': os.path.relpath(matched[(season, number)], show_path),
            },
        }
    )


@bp.route('/api/tv/<int:media_id>/unmatched')
def tv_unmatched_files(media_id: int):
    """Video files in a show folder with no SxxExx marker.

    These are overwhelmingly featurettes and extras. They are listed separately so
    they stay playable without being given episode numbers they do not have.
    """
    item = runtime.store().get_media_item(media_id)
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
        elif requested and str(season) != requested:
            continue
        basename = os.path.basename(path)
        files.append(
            {
                'name': basename,
                'label': _featurette_label(basename),
                'season': season,
                'file': os.path.relpath(path, show_path),
            }
        )
    return jsonify({'ok': True, 'media_id': media_id, 'files': files})
