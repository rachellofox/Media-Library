"""Tools routes: maintenance jobs run against the library as a whole.

The rename tools (F-0808.01) are split into movies and TV because they are
different jobs with different risks. A movie is one folder and one file named
after the title. An episode is named after a number that only its own filename
knows — see medialibrary/tv_rename_plan for why nothing here is allowed to
change that number.

Renaming touches the user's actual files, so preview and apply are separate
routes throughout: nothing is renamed that has not been listed first, and
apply re-reads the plan from disk rather than trusting the browser's copy.
"""

from flask import Blueprint, jsonify, request

from medialibrary import runtime, tmdb_state
from medialibrary.config import DISCOVER_COLLECTION_CACHE_HOURS
from medialibrary.naming_presets import (
    PRESET_SETTING,
    configured_preset,
    get_preset,
    preset_choices,
)
from medialibrary.rename_plan import apply_renames, plan_renames
from medialibrary.tv_rename_plan import apply_tv_renames, plan_tv_renames

bp = Blueprint('tools', __name__)


def _serializable(entry: dict) -> dict:
    """Only what the table needs — full paths stay on the server."""
    return {
        'key': entry['key'],
        'kind': entry['kind'],
        'media_id': entry['media_id'],
        'title': entry.get('title') or entry.get('show') or '',
        'from_name': entry['from_name'],
        'to_name': entry['to_name'],
        'conflict': entry['conflict'],
        'folder': entry.get('folder') or '',
        'media_type': entry.get('media_type') or '',
        'title_source': entry.get('title_source') or '',
    }


def _cached_episode_titles(tmdb_id):
    """{(season, number): title} for a show, from the database cache."""
    out: dict[tuple[int, int], str] = {}
    if not tmdb_id or not tmdb_state.client():
        return out
    # Imported here rather than at module scope: discover reaches for the
    # store and TMDB at import time in some test setups.
    from medialibrary.discover import _cached_tv_status

    overview = _cached_tv_status(int(tmdb_id))
    for season in overview.get('seasons') or []:
        number = season['season_number']
        if number == 0:
            continue
        cached = runtime.store().get_cached_season(
            int(tmdb_id), number, DISCOVER_COLLECTION_CACHE_HOURS
        )
        for episode in cached or []:
            out[(number, episode['episode_number'])] = episode.get('title') or ''
    return out


def _cached_episodes(tmdb_id):
    out: list[dict] = []
    if not tmdb_id or not tmdb_state.client():
        return out
    from medialibrary.discover import _cached_tv_status

    overview = _cached_tv_status(int(tmdb_id))
    for season in overview.get('seasons') or []:
        number = season['season_number']
        if number == 0:
            continue
        out.extend(
            runtime.store().get_cached_season(
                int(tmdb_id), number, DISCOVER_COLLECTION_CACHE_HOURS
            )
            or []
        )
    return out


def _requested_preset():
    """The preset named in the query string, else the saved one."""
    key = (request.args.get('preset') or '').strip()
    return get_preset(key) if key else configured_preset(runtime.store())


@bp.route('/api/tools/naming-presets')
def tools_naming_presets():
    return jsonify(
        {
            'ok': True,
            'presets': preset_choices(),
            'selected': configured_preset(runtime.store()).key,
        }
    )


@bp.route('/api/tools/naming-preset', methods=['POST'])
def tools_set_naming_preset():
    payload = request.get_json(silent=True) or {}
    key = (payload.get('preset') or '').strip()
    preset = get_preset(key)
    if preset.key != key:
        return jsonify({'ok': False, 'error': 'unknown_preset'}), 400
    runtime.store().set_setting(PRESET_SETTING, preset.key)
    return jsonify({'ok': True, 'selected': preset.key})


@bp.route('/api/tools/rename-preview')
def tools_rename_preview():
    """Movie folders and the video/subtitle files inside them."""
    plan = plan_renames(runtime.store(), _requested_preset(), media_types={'movie'})
    return jsonify(
        {
            'ok': True,
            'folders': [_serializable(entry) for entry in plan['folders']],
            'files': [_serializable(entry) for entry in plan['files']],
            'skipped': plan['skipped'],
            'total': plan['total'],
            'blocked': plan['blocked'],
            'renameable': plan['renameable'],
        }
    )


@bp.route('/api/tools/rename-apply', methods=['POST'])
def tools_rename_apply():
    payload = request.get_json(silent=True) or {}
    raw_keys = payload.get('keys')
    if not isinstance(raw_keys, list) or not raw_keys:
        # Refusing an empty selection rather than treating it as "all": a
        # mis-sent request must never rename the whole library by default.
        return jsonify({'ok': False, 'error': 'no_selection'}), 400

    preset = get_preset((payload.get('preset') or '').strip()) if payload.get('preset') else None
    # Re-planned from disk, never taken from the request: the browser's copy
    # may be minutes old, and it is what decides what gets renamed to what.
    plan = plan_renames(runtime.store(), preset, media_types={'movie'})
    result = apply_renames(runtime.store(), plan, selected_keys=set(raw_keys))
    return jsonify({'ok': True, **result})


def _tv_plan(preset):
    return plan_tv_renames(runtime.store(), preset, _cached_episode_titles, _cached_episodes)


@bp.route('/api/tools/tv-rename-preview')
def tools_tv_rename_preview():
    """Show folders come from the movie planner (same title-level rule); the
    episodes come from the hardened TV planner."""
    preset = _requested_preset()
    folder_plan = plan_renames(runtime.store(), preset, media_types={'tv'})
    plan = _tv_plan(preset)
    return jsonify(
        {
            'ok': True,
            'folders': [_serializable(entry) for entry in folder_plan['folders']],
            'shows': [
                {
                    'show': show['show'],
                    'trusted': show['trusted'],
                    'trust_reason': show['trust_reason'],
                    'episodes': [_serializable(entry) for entry in show['episodes']],
                    'subtitles': [_serializable(entry) for entry in show['subtitles']],
                    'strays': [_serializable(entry) for entry in show['strays']],
                    'manual': show['manual'],
                }
                for show in plan['shows']
            ],
            'skipped': plan['skipped'] + folder_plan['skipped'],
            'total': plan['total'] + folder_plan['total'],
            'blocked': plan['blocked'] + folder_plan['blocked'],
            'manual_count': plan['manual_count'],
            'untrusted_shows': plan['untrusted_shows'],
        }
    )


@bp.route('/api/tools/tv-rename-apply', methods=['POST'])
def tools_tv_rename_apply():
    payload = request.get_json(silent=True) or {}
    raw_keys = payload.get('keys')
    if not isinstance(raw_keys, list) or not raw_keys:
        return jsonify({'ok': False, 'error': 'no_selection'}), 400

    preset = get_preset((payload.get('preset') or '').strip()) if payload.get('preset') else None
    if preset is None:
        preset = configured_preset(runtime.store())
    selected = set(raw_keys)

    # Episodes first, then the show folders they sit inside: renaming the
    # folder first would invalidate every episode path in the plan.
    episode_result = apply_tv_renames(_tv_plan(preset), selected_keys=selected)
    folder_plan = plan_renames(runtime.store(), preset, media_types={'tv'})
    folder_result = apply_renames(runtime.store(), folder_plan, selected_keys=selected)

    return jsonify(
        {
            'ok': True,
            'renamed': episode_result['renamed'] + folder_result['renamed'],
            'failed': episode_result['failed'] + folder_result['failed'],
            'skipped_conflict': (
                episode_result['skipped_conflict'] + folder_result['skipped_conflict']
            ),
        }
    )
