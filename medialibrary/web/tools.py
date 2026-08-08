"""Tools routes: maintenance jobs run against the library as a whole.

Currently the canonical rename tool (F-0808.01). The scans that sit alongside
it in the Tools section — folders, quality, subtitles, metadata — are older
and still live on `core`, since they are plain form posts that redirect
rather than the preview/apply pair this needs.

Renaming touches the user's actual files, so the two halves are deliberately
separate routes: nothing here renames anything the user has not seen listed
first, and the apply step re-reads the plan from disk rather than trusting
whatever the browser was holding.
"""

from flask import Blueprint, jsonify, request

from medialibrary import runtime
from medialibrary.rename_plan import apply_renames, plan_renames

bp = Blueprint('tools', __name__)


def _serializable(entry: dict) -> dict:
    """Only what the table needs — full paths stay on the server."""
    return {
        'key': entry['key'],
        'kind': entry['kind'],
        'media_id': entry['media_id'],
        'title': entry['title'],
        'from_name': entry['from_name'],
        'to_name': entry['to_name'],
        'conflict': entry['conflict'],
        'folder': entry.get('folder') or '',
        'media_type': entry.get('media_type') or '',
    }


@bp.route('/api/tools/rename-preview')
def tools_rename_preview():
    plan = plan_renames(runtime.store())
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

    # Re-planned from disk, never taken from the request: the browser's copy
    # may be minutes old, and it is the one thing here that decides what gets
    # renamed to what.
    plan = plan_renames(runtime.store())
    result = apply_renames(runtime.store(), plan, selected_keys=set(raw_keys))
    return jsonify({'ok': True, **result})
