"""Planning and applying canonical renames for the library (F-0808.01).

The rules live in `medialibrary.naming`; this module only works out which
entries do not match them yet, and renames them on request. Lifted from
`scripts/_plan_canonical_names.py`, which did the same job from the command
line — the logic is unchanged, but as a module it can be previewed in the
Tools section and tested.

Two safety properties the script already had, kept deliberately:

- **Nothing is renamed without being previewed first.** `plan_renames` never
  touches the disk; `apply_renames` only acts on a plan it is handed.
- **Nothing is ever deleted or overwritten.** A rename whose destination
  already exists is refused, not resolved.

Scope is the title level: a library folder, and — for movies only — the video
and subtitle files inside it. TV episodes are named per episode, not per
show, so renaming them needs the TMDB episode list and belongs to the
separate episode-naming tool (`scripts/_plan_tv_naming.py`); a show's loose
files are deliberately left alone here rather than being flattened onto the
show's own name.
"""

import os

from medialibrary.identify import VIDEO_EXTENSIONS
from medialibrary.naming import canonical_stem
from medialibrary.subtitles import SUBTITLE_EXTENSIONS

# A title mid-download is being written to right now, and finalisation records
# where it landed. Renaming underneath either is how a download ends up filed
# to a path nothing points at.
ACTIVE_DOWNLOAD_STATES = {'starting', 'handed_off', 'downloading'}

RENAMEABLE_EXTENSIONS = VIDEO_EXTENSIONS | SUBTITLE_EXTENSIONS


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


def entry_key(path: str) -> str:
    """Stable identifier for one rename, used to select it for applying.

    The source path, since nothing can be renamed twice in one plan. It has to
    survive the plan being recomputed: `apply_renames` re-plans from disk
    rather than trusting a plan the browser has been holding, so the selection
    has to be expressible as something the fresh plan will produce too.
    """
    return os.path.normcase(os.path.normpath(path))


def _plan_for_item(item: dict) -> tuple[dict | None, list[dict], dict | None]:
    """(folder_rename, file_renames, skipped) for one library item."""
    media_id = int(item['id'])
    title = item['title'] or ''
    path = (item['path'] or '').strip()
    media_type = item['media_type'] or 'movie'

    if (item['download_status'] or '').strip().lower() in ACTIVE_DOWNLOAD_STATES:
        return None, [], {'media_id': media_id, 'title': title, 'reason': 'download in progress'}
    if not path:
        return None, [], None  # nothing on disk yet; not a naming problem
    if not os.path.isdir(path):
        return None, [], {'media_id': media_id, 'title': title, 'reason': 'folder not found'}

    stem = canonical_stem(title, item['year'], media_type)
    if not stem:
        return None, [], {'media_id': media_id, 'title': title, 'reason': 'no canonical name'}

    normalized = os.path.normpath(path)
    current_folder = os.path.basename(normalized)
    parent = os.path.dirname(normalized)

    folder_rename = None
    if current_folder != stem:
        target = os.path.join(parent, stem)
        folder_rename = {
            'kind': 'folder',
            'key': entry_key(path),
            'media_id': media_id,
            'title': title,
            'media_type': media_type,
            'from': path,
            'to': target,
            'from_name': current_folder,
            'to_name': stem,
            # A case-only rename on Windows reads as "already exists" but is a
            # legitimate correction, so it is not a conflict.
            'conflict': os.path.exists(target) and not _same_path(target, path),
        }

    # TV episodes carry their own per-episode names; only a movie's files share
    # the folder's stem.
    if media_type == 'tv':
        return folder_rename, [], None

    file_renames = []
    try:
        children = sorted(os.listdir(path))
    except OSError:
        return folder_rename, [], {
            'media_id': media_id,
            'title': title,
            'reason': 'folder could not be read',
        }

    for name in children:
        child = os.path.join(path, name)
        if not os.path.isfile(child):
            continue
        child_stem, ext = os.path.splitext(name)
        if ext.lower() not in RENAMEABLE_EXTENSIONS:
            continue
        # "Movie.en.srt" is already correct — the suffix is the language, not a
        # leftover release name.
        if ext.lower() in SUBTITLE_EXTENSIONS and child_stem.lower().startswith(stem.lower()):
            continue
        if child_stem == stem:
            continue
        target = os.path.join(path, f'{stem}{ext}')
        file_renames.append(
            {
                'kind': 'file',
                'key': entry_key(child),
                'media_id': media_id,
                'title': title,
                'folder': current_folder,
                'from': child,
                'to': target,
                'from_name': name,
                'to_name': f'{stem}{ext}',
                'conflict': os.path.exists(target) and not _same_path(target, child),
            }
        )

    return folder_rename, file_renames, None


def plan_renames(store) -> dict:
    """What would change to bring every library entry to its canonical name.

    Reads the disk but never writes to it.
    """
    folders: list[dict] = []
    files: list[dict] = []
    skipped: list[dict] = []

    for row in store.list_media_items():
        folder_rename, file_renames, skip = _plan_for_item(dict(row))
        if skip:
            skipped.append(skip)
        if folder_rename:
            folders.append(folder_rename)
        files.extend(file_renames)

    # Two sources planned onto one destination means an identification is wrong
    # somewhere. os.rename would fail on the second rather than overwrite, but
    # the pair is refused outright rather than applying half of it.
    claimed: dict[str, list[dict]] = {}
    for entry in folders + files:
        claimed.setdefault(os.path.normcase(os.path.normpath(entry['to'])), []).append(entry)
    for entries in claimed.values():
        if len(entries) > 1:
            for entry in entries:
                entry['conflict'] = True

    blocked = sum(1 for entry in folders + files if entry['conflict'])
    return {
        'folders': folders,
        'files': files,
        'skipped': skipped,
        'total': len(folders) + len(files),
        'blocked': blocked,
        'renameable': len(folders) + len(files) - blocked,
    }


def apply_renames(store, plan: dict, selected_keys: set[str] | None = None) -> dict:
    """Perform the chosen non-conflicting renames in `plan`.

    `selected_keys` limits this to the entries the user actually ticked; None
    means every entry in the plan. Keys not present in the plan are ignored
    rather than erroring — the plan is re-read from disk before applying, so a
    stale selection naming something already renamed is expected, not a fault.

    Files are renamed before folders: a file's path runs through its folder, so
    moving the folder first would leave every file entry pointing at somewhere
    that no longer exists.
    """
    renamed = 0
    failed: list[dict] = []
    skipped_conflict = 0

    def wanted(entry: dict) -> bool:
        return selected_keys is None or entry['key'] in selected_keys

    for entry in plan.get('files') or []:
        if not wanted(entry):
            continue
        if entry['conflict']:
            skipped_conflict += 1
            continue
        try:
            os.rename(entry['from'], entry['to'])
            renamed += 1
        except OSError as exc:
            failed.append({'from': entry['from_name'], 'error': str(exc)})

    for entry in plan.get('folders') or []:
        if not wanted(entry):
            continue
        if entry['conflict']:
            skipped_conflict += 1
            continue
        try:
            os.rename(entry['from'], entry['to'])
            # The database points at the old folder until this lands, so the
            # rename and the recorded path have to move together or the item
            # goes missing from the library view.
            store.update_path(entry['media_id'], entry['to'])
            renamed += 1
        except OSError as exc:
            failed.append({'from': entry['from_name'], 'error': str(exc)})

    return {
        'renamed': renamed,
        'failed': failed,
        'skipped_conflict': skipped_conflict,
    }
