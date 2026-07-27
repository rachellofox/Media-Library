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

import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from medialibrary.config import DISCOVER_COLLECTION_CACHE_HOURS
from medialibrary.episode_match import (
    is_extras_path,
    match_episode_files,
    match_episode_title,
    names_other_show,
    normalise_episode_title,
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
    if not tmdb_id or not app.tmdb:
        return []
    episodes = []
    overview = app._cached_tv_status(int(tmdb_id))
    for season in overview.get('seasons') or []:
        number = season['season_number']
        if number == 0:
            continue
        cached = app.store.get_cached_season(int(tmdb_id), number, DISCOVER_COLLECTION_CACHE_HOURS)
        if cached is None:
            cached = app.tmdb.season_episodes(tmdb_id, number)
            if cached:
                app.store.set_cached_season(int(tmdb_id), number, cached)
        episodes.extend(cached or [])
    return episodes


def episode_title_for(episodes, season, number):
    for episode in episodes:
        if episode['season_number'] == season and episode['episode_number'] == number:
            return episode.get('title')
    return None


# How far a file's running time may sit from the runtime TMDB gives for the
# episode it claims before the two are taken to be different episodes. Rips vary
# by a couple of minutes with adverts trimmed, so the band is deliberately wide —
# it is there to catch an 87-minute pilot named as a 43-minute episode, not to
# audit encodes.
DURATION_TOLERANCE = 0.5


def _probe(path: str) -> tuple[float, str]:
    """A video's length in minutes and the episode title written inside it.

    Many rips carry the episode title in the container, which is the one piece of
    evidence a misleading filename cannot touch — the Batman files say
    "S01E01 - The Cat and the Claw" while the container says "On Leather Wings".
    Missing from plenty of files, so it is used when present and never required.
    """
    try:
        probe = subprocess.run(
            [app.FFPROBE_EXE, '-v', 'quiet', '-print_format', 'json', '-show_format', path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        container = json.loads(probe.stdout or '{}').get('format') or {}
        tags = container.get('tags') or {}
        title = next((value for key, value in tags.items() if key.lower() == 'title'), '')
        return float(container.get('duration') or 0) / 60, str(title or '')
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return 0.0, ''


def _score_ordering(planned, episodes, probes) -> tuple[int, int, int, int]:
    """How well an ordering fits the files.

    Returns (titles it gets right, slots it has, lengths it explains, contradictions).

    Three signals. An episode title written inside the file is the strongest when
    it lands: the Batman rip claims "S01E01 - The Cat and the Claw" while the
    container says "On Leather Wings", which is exactly what the DVD ordering puts
    in that slot. It only ever counts *for* an ordering, never against one, because
    most containers hold something other than an episode title — disc labels
    ("WENTWORTH Series 2 Disc 2"), rip artefacts ("S01D01title_t02"), release
    names — and reading those as disagreement condemns orderings that are right.
    A title that matches nothing is simply silent.

    Coverage asks whether the ordering even has the episode a file claims: Batman
    is numbered 28/28/29 to a season on disk against TMDB's 60/10/10, so 57 files
    point at slots that ordering does not have. Running time is the only signal
    trusted to contradict, being the one thing no naming convention can distort —
    it is what caught Firefly's 87-minute pilot sitting in a 43-minute slot.

    A file covering a range is measured against the sum of its episodes so a
    two-parter is not read as double-length, and anything the ordering has no
    answer for is not counted as evidence either way.
    """
    slots = {(episode['season_number'], episode['episode_number']): episode for episode in episodes}
    titles = covered = agree = disagree = 0
    for path, claimed in planned.items():
        covered += sum(1 for key in claimed if key in slots)
        minutes, embedded = probes.get(path, (0.0, ''))

        expected_title = (slots.get(claimed[0], {}) or {}).get('title') or ''
        if (
            embedded
            and expected_title
            and len(claimed) == 1
            and normalise_episode_title(embedded) == normalise_episode_title(expected_title)
        ):
            titles += 1

        expected = sum((slots.get(key, {}) or {}).get('runtime') or 0 for key in claimed)
        if not expected or not minutes:
            continue
        if abs(minutes - expected) > expected * DURATION_TOLERANCE:
            disagree += 1
        else:
            agree += 1
    return titles, covered, agree, disagree


# Which ordering to prefer when more than one explains the files equally well.
# They usually agree — Firefly's DVD and "intended" orders are the same sequence —
# so this is about naming the result the same way twice, not about correctness.
ORDERING_PREFERENCE = ['DVD', 'Digital', 'Production', 'Absolute', 'Story arc', 'TV']


def _resolve_ordering(show_title, tmdb_id, planned, episodes, notes, left_alone):
    """Pick the episode numbering this show's files are actually in.

    The numbering a release uses is a property of the download, not of the show,
    so it has to be read off the files rather than configured. Firefly shipped on
    DVD with its double-length pilot first while TMDB lists that pilot last, so
    the same "S01E01" means two different episodes; taking TMDB's default on
    trust wrote "The Train Job" onto the 87-minute Serenity.

    Every ordering TMDB publishes is scored on the evidence in the files — see
    `_score_ordering` — and one is only adopted if nothing contradicts it and it
    accounts for more of them than the default did. Where nothing fits, the
    episode titles are withheld and the files keep their numbers alone: a bare
    "S01E04" is honest, a wrong title is not.

    Returns (episodes, seasons_to_leave_untitled).
    """
    # Each file is read once; every ordering is then scored against the same
    # evidence, so the cost does not multiply by the number of candidates.
    probes = {path: _probe(path) for path in planned}
    wanted = sum(len(claimed) for claimed in planned.values())
    base_titles, base_covered, base_agree, base_disagree = _score_ordering(
        planned, episodes, probes
    )
    # Settling for the default needs more than the absence of contradiction. Where
    # the files name their own episodes and the default matches none of them, the
    # numbering may be a straight permutation — every slot present and every
    # runtime plausible, yet each title one place out — which is what Batman is.
    embedded_titles = sum(1 for _minutes, title in probes.values() if title)
    if not base_disagree and base_covered >= wanted and (base_titles or not embedded_titles):
        return episodes, set()

    candidates = []
    for ordering in app.tmdb.episode_orderings(tmdb_id) if tmdb_id else []:
        titles, covered, agree, disagree = _score_ordering(planned, ordering['episodes'], probes)
        # Nothing may contradict it, and it has to account for more of the files
        # than the default managed on at least one signal.
        if disagree:
            continue
        if (titles, covered, agree) <= (base_titles, base_covered, base_agree):
            continue
        rank = (
            ORDERING_PREFERENCE.index(ordering['kind'])
            if ordering['kind'] in ORDERING_PREFERENCE
            else len(ORDERING_PREFERENCE)
        )
        candidates.append((-titles, -covered, -agree, rank, ordering['name'], ordering))

    if candidates:
        titles, covered, _agree, _rank, _name, ordering = min(candidates)
        if -titles:
            evidence = (
                f'{-titles} files carry the episode title inside them and all of '
                f'them match this ordering'
            )
        else:
            evidence = (
                f'it has {-covered} of the {wanted} episodes these files claim, '
                f"against TMDB's default {base_covered}"
            )
        notes.append((show_title, ordering['name'], f'{ordering["kind"]} numbering — {evidence}'))
        return ordering['episodes'], set()

    # Nothing fits, so withhold titles for the seasons holding the contradictions.
    slots = {(episode['season_number'], episode['episode_number']): episode for episode in episodes}
    untitled = set()
    for path, claimed in planned.items():
        season_number = claimed[0][0]
        if season_number in untitled:
            continue
        minutes, _embedded = probes.get(path, (0.0, ''))
        relative = os.path.relpath(path, os.path.dirname(os.path.dirname(path)))
        expected = sum((slots.get(key, {}) or {}).get('runtime') or 0 for key in claimed)
        if not expected or not minutes:
            continue
        if abs(minutes - expected) > expected * DURATION_TOLERANCE:
            untitled.add(season_number)
            left_alone.append(
                (
                    show_title,
                    relative,
                    f'runs {minutes:.0f} min but TMDB says {expected:.0f}, and no published '
                    f'ordering fits — Season {season_number:02d} titles withheld',
                )
            )
    return episodes, untitled


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
        if app._SEASON_DIR_EXACT.match(parts[index]) or app._SPECIALS_DIR.match(parts[index]):
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
    marked, unmarked = app.scan_local_episodes(show_path, show_title)

    # A file naming another show is never renamed, whatever its marker says. It
    # is a misplacement to be moved out by hand, not an episode of this show.
    # The scan already refused it, which is why it arrives here as unmarked.
    intruders = [
        p
        for p in unmarked
        if app._episodes_covered(os.path.basename(p))
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

    episodes, untitled = _resolve_ordering(
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
            covered = app._episodes_covered(name)
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
        season = app._infer_season_from_path(show_path, path)
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
            if extension in app.VIDEO_EXTENSIONS or extension in SUBTITLE_EXTENSIONS:
                continue  # handled above
            path = os.path.join(root, name)
            season = app._infer_season_from_path(show_path, path)
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
