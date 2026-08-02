"""
TV naming preview.
Usage: python scripts/_plan_tv_naming.py [--apply] [--show "Name"]

Brings TV episodes to the canonical layout, matching the movie tool:

    <Show>/Season NN/<Show> - SxxExx - Episode Title.<ext>

Episodes already carrying an SxxExx marker are renamed from that. Files that
carry only a title ("EP01 - Night of the Sentinels.mkv") are identified against
TMDB's episode list, refusing anything ambiguous — see episode_match.py.

Nothing is renamed without --apply, and nothing is ever deleted. Files that
cannot be identified are reported and left exactly where they are.
"""

import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from medialibrary import tmdb_state
from medialibrary.config import DISCOVER_COLLECTION_CACHE_HOURS
from medialibrary.episode_match import (
    is_extras_path,
    match_episode_files,
    match_episode_title,
    names_other_show,
)
from medialibrary.episode_ordering import _resolve_ordering
from medialibrary.identify import (
    _SEASON_DIR_EXACT,
    _SPECIALS_DIR,
    VIDEO_EXTENSIONS,
    _episodes_covered,
    _infer_season_from_path,
    scan_local_episodes,
)
from medialibrary.naming import canonical_episode_name, canonical_season_folder

APPLY = '--apply' in sys.argv
ONLY_SHOW = None
if '--show' in sys.argv:
    index = sys.argv.index('--show')
    if index + 1 < len(sys.argv):
        ONLY_SHOW = sys.argv[index + 1].lower()


def tmdb_episodes(tmdb_id):
    """Every episode of a show, specials excluded, from the database cache."""
    if not tmdb_id or not tmdb_state.client():
        return []
    episodes = []
    overview = app._cached_tv_status(int(tmdb_id))
    for season in overview.get('seasons') or []:
        number = season['season_number']
        if number == 0:
            continue
        cached = app.store.get_cached_season(int(tmdb_id), number, DISCOVER_COLLECTION_CACHE_HOURS)
        if cached is None:
            cached = tmdb_state.client().season_episodes(tmdb_id, number)
            if cached:
                app.store.set_cached_season(int(tmdb_id), number, cached)
        episodes.extend(cached or [])
    return episodes


def episode_title_for(episodes, season, number):
    for episode in episodes:
        if episode['season_number'] == season and episode['episode_number'] == number:
            return episode.get('title')
    return None


SUBTITLE_EXTENSIONS = {'.srt', '.sub', '.ass', '.ssa', '.vtt', '.idx', '.sup'}
# Language/flag suffixes worth carrying over; anything else trailing a dot is a
# release group, not a language, so it is dropped with the rest of the old name.
SUBTITLE_SUFFIXES = {
    'en',
    'eng',
    'english',
    'fr',
    'fre',
    'french',
    'de',
    'ger',
    'german',
    'es',
    'spa',
    'spanish',
    'it',
    'ita',
    'nl',
    'dut',
    'pt',
    'por',
    'sv',
    'da',
    'no',
    'fi',
    'pl',
    'ru',
    'ja',
    'jpn',
    'ko',
    'zh',
    'chi',
    'forced',
    'sdh',
    'cc',
}


def _below_season_folder(path: str) -> str:
    """The part of a path after its deepest season-naming folder.

    ".../Featurettes/Season 6/The Cast's Favourite Scenes/Kate A.mkv" gives
    "The Cast's Favourite Scenes/Kate A.mkv", so the grouping survives the move.
    """
    parts = os.path.normpath(path).split(os.sep)
    for index in range(len(parts) - 2, -1, -1):
        if _SEASON_DIR_EXACT.match(parts[index]) or _SPECIALS_DIR.match(parts[index]):
            return os.path.join(*parts[index + 1 :])
    return os.path.basename(path)


def subtitle_suffix(stem: str) -> str:
    """Trailing ".en" style marker on a subtitle name, or ''."""
    parts = stem.replace('_', '.').split('.')
    if len(parts) > 1 and parts[-1].lower() in SUBTITLE_SUFFIXES:
        return '.' + parts[-1].lower()
    return ''


renames = []
sidecars = []
strays = []
already = 0
left_alone = []
# Shows whose files are numbered in something other than TMDB's default order.
ordering_notes = []

for item in app.store.list_media_items():
    if (item['media_type'] or '') != 'tv':
        continue
    show_path = (item['path'] or '').strip()
    if not show_path or not os.path.isdir(show_path):
        continue
    show_title = item['title'] or os.path.basename(show_path)
    if ONLY_SHOW and ONLY_SHOW not in show_title.lower():
        continue

    episodes = tmdb_episodes(item['tmdb_id'])
    marked, unmarked = scan_local_episodes(show_path, show_title)

    # A file naming another show is never renamed, whatever its marker says. It
    # is a misplacement to be moved out by hand, not an episode of this show.
    # The scan already refused it, which is why it arrives here as unmarked.
    intruders = [
        p
        for p in unmarked
        if _episodes_covered(os.path.basename(p))
        and names_other_show(os.path.basename(p), show_title)
    ]
    unmarked = [p for p in unmarked if p not in set(intruders)]
    for path in intruders:
        left_alone.append((show_title, os.path.relpath(path, show_path), 'names a different show'))

    # Every file, mapped to the episode numbers it covers. A marker is not enough
    # on its own: a file filed under Featurettes has been put there deliberately —
    # an unaired pilot or a bonus episode kept out of the run — and moving it back
    # among the episodes because its name carries a number would undo that choice.
    planned = {}
    for (season, number), path in marked.items():
        if is_extras_path(os.path.relpath(path, show_path)):
            continue
        planned.setdefault(path, []).append((season, number))

    # Bonus material is identified by its folder, never matched against the
    # episode list — a featurette's title can sit very close to an episode's.
    extras = [p for p in unmarked if is_extras_path(os.path.relpath(p, show_path))]
    unmarked = [p for p in unmarked if p not in set(extras)]
    for path in extras:
        left_alone.append((show_title, os.path.relpath(path, show_path), 'bonus material'))

    # Title-only files, matched against TMDB by name.
    if unmarked and episodes:
        by_name = {}
        for path in unmarked:
            by_name.setdefault(os.path.basename(path), []).append(path)
        resolved = match_episode_files(sorted(by_name), episodes)
        for name, episode in resolved.items():
            for path in by_name[name]:
                planned.setdefault(path, []).append(
                    (episode['season_number'], episode['episode_number'])
                )
        for name in sorted(set(by_name) - set(resolved)):
            _episode, reason = match_episode_title(name, episodes)
            for path in by_name[name]:
                left_alone.append((show_title, os.path.relpath(path, show_path), reason))
    elif unmarked:
        for path in unmarked:
            left_alone.append((show_title, os.path.relpath(path, show_path), 'no-tmdb-episodes'))

    episodes, untitled, _ordering = _resolve_ordering(
        show_title, item['tmdb_id'], planned, episodes, ordering_notes, left_alone
    )

    for path, covered in planned.items():
        covered.sort()
        season = covered[0][0]
        first, last = covered[0][1], covered[-1][1]
        title = '' if season in untitled else episode_title_for(episodes, season, first)
        name = canonical_episode_name(show_title, season, first, title, os.path.splitext(path)[1])
        if last != first:
            # A file covering several episodes keeps the range in its name, or the
            # scanner would stop seeing it as covering them.
            marker = f'S{season:02d}E{first:02d}'
            name = name.replace(marker, f'{marker}-E{last:02d}', 1)

        target = os.path.join(show_path, canonical_season_folder(season), name)
        if os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(target)):
            already += 1
            continue
        renames.append(
            {
                'show': show_title,
                'from': path,
                'to': target,
                'rel_from': os.path.relpath(path, show_path),
                'rel_to': os.path.relpath(target, show_path),
                'conflict': os.path.exists(target),
            }
        )

    # Where each episode ends up, whether it is moving or already in place.
    final_for = {}
    for path, covered in planned.items():
        season, first = covered[0][0], covered[0][1]
        moved = next((e['to'] for e in renames if e['from'] == path), path)
        final_for[(season, first)] = moved

    # A subtitle has to sit beside its video and share its name, or the player
    # will not find it. Renaming episodes without their sidecars orphaned every
    # subtitle in the library.
    for root, _dirs, files in os.walk(show_path):
        for name in files:
            extension = os.path.splitext(name)[1].lower()
            if extension not in SUBTITLE_EXTENSIONS:
                continue
            covered = _episodes_covered(name)
            if not covered:
                continue
            season, numbers = covered
            episode_path = final_for.get((season, numbers[0]))
            if not episode_path:
                continue
            stem = os.path.splitext(os.path.basename(episode_path))[0]
            suffix = subtitle_suffix(os.path.splitext(name)[0])
            source = os.path.join(root, name)
            target = os.path.join(os.path.dirname(episode_path), f'{stem}{suffix}{extension}')
            if os.path.normcase(os.path.normpath(source)) == os.path.normcase(
                os.path.normpath(target)
            ):
                continue
            sidecars.append(
                {
                    'show': show_title,
                    'from': source,
                    'to': target,
                    'rel_from': os.path.relpath(source, show_path),
                    'rel_to': os.path.relpath(target, show_path),
                    'conflict': os.path.exists(target),
                }
            )

    # Non-episode files stranded in a folder that a canonical season folder now
    # duplicates — this is what leaves a show with both "Season 1" and "Season 01".
    for path in extras + list(unmarked):
        # Anything already being renamed as an episode is not a stray.
        if path in planned:
            continue
        season = _infer_season_from_path(show_path, path)
        if season is None:
            continue
        season_folder = os.path.join(show_path, canonical_season_folder(season))
        if os.path.normcase(os.path.dirname(path)).startswith(os.path.normcase(season_folder)):
            continue  # already under the canonical season folder
        # Keep whatever grouping sits below the season folder. Flattening to the
        # basename collides: Wentworth has a "Kate A.mkv" under more than one
        # season's "The Cast's Favourite Scenes".
        target = os.path.join(season_folder, 'Featurettes', _below_season_folder(path))
        strays.append(
            {
                'show': show_title,
                'from': path,
                'to': target,
                'rel_from': os.path.relpath(path, show_path),
                'rel_to': os.path.relpath(target, show_path),
                'conflict': os.path.exists(target),
            }
        )

    # Everything else stranded in a superseded season folder: waveform caches,
    # .nfo, artwork. Not media, but they are what keeps a duplicate "Season 1"
    # alive next to "Season 01". These belong beside the episodes rather than
    # under Featurettes, since they are per-episode sidecar files.
    for root, _dirs, files in os.walk(show_path):
        for name in files:
            extension = os.path.splitext(name)[1].lower()
            if extension in VIDEO_EXTENSIONS or extension in SUBTITLE_EXTENSIONS:
                continue  # handled above
            path = os.path.join(root, name)
            season = _infer_season_from_path(show_path, path)
            if season is None:
                continue
            season_folder = os.path.join(show_path, canonical_season_folder(season))
            if os.path.normcase(root).startswith(os.path.normcase(season_folder)):
                continue
            target = os.path.join(season_folder, _below_season_folder(path))
            strays.append(
                {
                    'show': show_title,
                    'from': path,
                    'to': target,
                    'rel_from': os.path.relpath(path, show_path),
                    'rel_to': os.path.relpath(target, show_path),
                    'conflict': os.path.exists(target),
                }
            )


# Two sources planned onto one destination means the identification is wrong
# somewhere. os.rename would fail on the second rather than overwrite, but the
# plan should be refused outright rather than half-applied.
targets = {}
for entry in renames + sidecars + strays:
    targets.setdefault(os.path.normcase(os.path.normpath(entry['to'])), []).append(entry)
collisions = {t: e for t, e in targets.items() if len(e) > 1}
for entry_list in collisions.values():
    for entry in entry_list:
        entry['conflict'] = True

print('=' * 78)
print(f'TV NAMING PREVIEW{"  (APPLYING)" if APPLY else "  (dry run)"}')
print('=' * 78)

if collisions:
    print(f'\n!! {len(collisions)} destination(s) claimed by more than one file — all skipped:')
    for target, entry_list in list(collisions.items())[:5]:
        print(f'   {os.path.basename(target)}')
        for entry in entry_list:
            print(f'      <- {entry["rel_from"][:66]}')

by_show = {}
for entry in renames:
    by_show.setdefault(entry['show'], []).append(entry)

for show, entries in sorted(by_show.items()):
    conflicts = sum(1 for e in entries if e['conflict'])
    print(
        f'\n{show}  ({len(entries)} to rename'
        + (f', {conflicts} blocked by an existing file' if conflicts else '')
        + ')'
    )
    for entry in entries[:6]:
        flag = '   [CONFLICT, skipped]' if entry['conflict'] else ''
        print(f'    {entry["rel_from"][:74]}')
        print(f'      -> {entry["rel_to"][:70]}{flag}')
    if len(entries) > 6:
        print(f'    … and {len(entries) - 6} more')

if ordering_notes:
    print(f'\nEpisode ordering detected from the files ({len(ordering_notes)}):')
    for show, name, detail in sorted(ordering_notes):
        print(f'  {show} — using "{name}"')
        print(f'      {detail}')

if left_alone:
    print(f'\nLeft alone ({len(left_alone)}) — could not be identified, nothing will change:')
    reasons = {}
    for show, rel, reason in left_alone:
        reasons.setdefault((show, reason), []).append(rel)
    for (show, reason), files in sorted(reasons.items()):
        print(f'  {show} — {reason} ({len(files)})')
        for rel in files[:3]:
            print(f'      {rel[:70]}')

for label, group in (
    ('Subtitles to move beside their episode', sidecars),
    ('Bonus files to gather into the canonical season folder', strays),
):
    if not group:
        continue
    print(f'\n{label} ({len(group)}):')
    for entry in group[:6]:
        flag = '   [CONFLICT, skipped]' if entry['conflict'] else ''
        print(f'    {entry["rel_from"][:74]}')
        print(f'      -> {entry["rel_to"][:70]}{flag}')
    if len(group) > 6:
        print(f'    … and {len(group) - 6} more')

print(f'\n{"=" * 78}')
print(f'  Already canonical  : {already}')
print(f'  Episodes to rename : {len(renames)}')
print(f'  Subtitles to move  : {len(sidecars)}')
print(f'  Bonus to gather    : {len(strays)}')
print(f'  Blocked            : {sum(1 for e in renames + sidecars + strays if e["conflict"])}')
print(f'  Left alone         : {len(left_alone)}')
print('=' * 78)

if not APPLY:
    print('\nDry run - nothing changed. Re-run with --apply to perform these renames.')
    sys.exit(0)

print('\nApplying...')
done = failed = 0
# Episodes first: each subtitle's destination was derived from where its episode lands.
for entry in renames + sidecars + strays:
    if entry['conflict']:
        print(f'  SKIP (target exists): {entry["rel_to"]}')
        continue
    try:
        os.makedirs(os.path.dirname(entry['to']), exist_ok=True)
        # os.rename, never shutil.move: on a locked file shutil silently falls
        # back to copy-then-delete and leaves a duplicate behind.
        os.rename(entry['from'], entry['to'])
        done += 1
    except OSError as exc:
        failed += 1
        hint = ' (file in use)' if getattr(exc, 'winerror', None) == 32 else ''
        print(f'  FAILED {entry["rel_from"][:56]}: {exc}{hint}')

print(f'\nRenamed {done}, failed {failed}, skipped {sum(1 for e in renames if e["conflict"])}.')
