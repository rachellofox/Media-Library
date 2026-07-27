"""Small shared helpers: JSON-valued settings, and UTC timestamps.

Several settings hold JSON rather than a scalar, and several records carry an
ISO timestamp that may or may not have a timezone on it. Both are needed by more
than one module, so they live here rather than in whichever one got there first.
"""

import json
from datetime import datetime, timezone

from medialibrary import runtime


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except Exception:
        return None


def _load_json_setting(key: str) -> dict | None:
    raw = runtime.store().get_setting(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _save_json_setting(key: str, value: dict | None) -> None:
    if not value:
        runtime.store().delete_setting(key)
        return
    runtime.store().set_setting(key, json.dumps(value))
