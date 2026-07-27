"""The shape a library item takes in the UI.

One payload built the same way wherever an item is returned, so the grid, the
hero card and the API never disagree about whether something is missing or
upgradable. `_upgrade_available` is deliberately derived from the last search
result against the file's current quality rather than stored — a stored flag
went stale and kept advertising an upgrade after one had been applied.
"""

from medialibrary import runtime
from medialibrary.identify import _is_local_media_missing
from medialibrary.quality import compare_quality


def _upgrade_available(item) -> bool:
    """Whether a genuinely better release is on offer for this item.

    `quality_checks.found` is a boolean frozen at the moment the search ran, so
    on its own it keeps advertising an upgrade long after the file has been
    upgraded — it will even offer a 1080p release for a file that is now 2160p.
    Re-comparing the recorded result against the current quality makes the
    badge self-correcting no matter how stale the stored check is.
    """
    try:
        if int(item['found'] or 0) != 1:
            return False
    except (KeyError, IndexError, TypeError, ValueError):
        return False

    try:
        best_found = item['best_found_quality']
    except (KeyError, IndexError):
        return False
    if not best_found:
        return False

    try:
        current = item['current_quality']
    except (KeyError, IndexError):
        current = None
    return compare_quality(current, best_found) > 0


def _ui_item_payload(media_id: int) -> dict | None:
    for row in runtime.store().list_media_items():
        if int(row['id']) != int(media_id):
            continue
        payload = dict(row)
        path = payload.get('path') or ''
        status = (payload.get('download_status') or '').strip().lower()
        payload['file_missing'] = _is_local_media_missing(path) and status not in {
            'starting',
            'handed_off',
            'downloading',
        }
        payload['upgrade_available'] = _upgrade_available(row)
        return payload
    return None
