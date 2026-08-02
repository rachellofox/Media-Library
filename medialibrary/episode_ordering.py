"""Working out which episode numbering a show's files actually use.

A release is not obliged to number episodes the way TMDB does by default.
Firefly shipped on DVD with its double-length pilot first while TMDB lists
that pilot last, so the same "S01E01" means two different episodes; Batman:
The Animated Series is numbered 28/28/29 to a season on disk against TMDB's
60/10/10. Taking TMDB's default on trust puts the wrong title on the file.

This module holds the scoring and resolution logic, shared by the offline
rename tool (`scripts/_plan_tv_naming.py`, which acts on it) and the live app
(which only needs to know which ordering to read titles from — see
`medialibrary/maintenance.py` for where detection runs and
`medialibrary/web/tv.py` for where the result is used).

`_probe`, `_score_ordering`, `ORDERING_PREFERENCE` and `_resolve_ordering` are
moved here verbatim from the rename tool, which is what `tests/test_ordering.py`
still tests directly. Detection is expensive — it reads every file's running
time — so it is meant to run once per show and have its answer persisted, not
be repeated per request.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

from medialibrary.config import FFPROBE_EXE
from medialibrary.episode_match import is_extras_path, normalise_episode_title

# Same TTL as the season cache in medialibrary.discover, for the same reason:
# long enough that browsing a show does not repeatedly hit TMDB, short enough
# that a correction on TMDB's side is not stuck forever.
_ORDERING_CACHE_TTL = timedelta(hours=24)
_ORDERING_CACHE: dict[str, tuple[datetime, list[dict]]] = {}

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
            [FFPROBE_EXE, '-v', 'quiet', '-print_format', 'json', '-show_format', path],
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

    Returns (episodes, seasons_to_leave_untitled, chosen_ordering). chosen_ordering
    is the adopted ordering's own dict (id, name, kind, episodes) or None when the
    default was kept — callers that only rename files can ignore it; a caller
    persisting the decision needs the id to fetch this ordering again later
    without re-probing every file.
    """
    from medialibrary import tmdb_state

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
        return episodes, set(), None

    candidates = []
    for ordering in tmdb_state.client().episode_orderings(tmdb_id) if tmdb_id else []:
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
        return ordering['episodes'], set(), ordering

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
    return episodes, untitled, None


def default_episodes(tmdb_id: int | None) -> list[dict]:
    """Every episode of a show in TMDB's default numbering, specials excluded.

    Flat across seasons, matching what `_score_ordering` and `_resolve_ordering`
    expect. Goes through the app's existing per-process season cache
    (`medialibrary.discover._cached_season_episodes`) rather than adding a
    second one, so this and the season list never disagree about what TMDB
    currently says.
    """
    if not tmdb_id:
        return []
    from medialibrary import tmdb_state
    from medialibrary.discover import _cached_season_episodes, _cached_tv_status

    if not tmdb_state.client():
        return []
    overview = _cached_tv_status(int(tmdb_id))
    episodes = []
    for season in overview.get('seasons') or []:
        number = season['season_number']
        if number == 0:
            continue
        episodes.extend(_cached_season_episodes(tmdb_id, number) or [])
    return episodes


def _cached_ordering_episodes(ordering_id: str) -> list[dict]:
    """One ordering's full episode list, across all the seasons it defines."""
    cached = _ORDERING_CACHE.get(ordering_id)
    if cached and (datetime.now(timezone.utc) - cached[0]) < _ORDERING_CACHE_TTL:
        return cached[1]
    from medialibrary import tmdb_state

    client = tmdb_state.client()
    if not client:
        return []
    episodes = client._episode_group(ordering_id)
    if episodes:
        _ORDERING_CACHE[ordering_id] = (datetime.now(timezone.utc), episodes)
    return episodes


def resolved_episodes(
    media_id: int, tmdb_id: int | None, season_number: int | None = None
) -> list[dict]:
    """Episode metadata for a show, from its adopted ordering if it has one.

    Falls back to TMDB's default numbering when nothing was adopted, or when
    the show has never been checked — `medialibrary.maintenance` runs detection
    once per show, in the background, and persists the answer; nothing here
    re-detects, which is the whole point of persisting it.

    A season withheld by detection (no published ordering fit the files, and
    the default's own runtimes contradicted them) comes back with its titles
    blanked rather than removed, so a caller's existing "Episode N" fallback
    for a missing title handles it without a second code path.
    """
    from medialibrary import runtime

    decision = runtime.store().get_episode_ordering(media_id)
    if decision and decision['ordering_id']:
        episodes = _cached_ordering_episodes(decision['ordering_id'])
    else:
        episodes = default_episodes(tmdb_id)

    if season_number is not None:
        episodes = [e for e in episodes if e['season_number'] == season_number]

    untitled = decision['untitled_seasons'] if decision else set()
    if untitled:
        episodes = [{**e, 'title': ''} if e['season_number'] in untitled else e for e in episodes]
    return episodes


def planned_from_matched(show_path: str, matched: dict[tuple[int, int], str]) -> dict[str, list]:
    """Invert scan_local_episodes' result into path -> [(season, episode), ...].

    A file filed under Featurettes has been put there deliberately — an unaired
    pilot or a bonus episode kept out of the run — so it is excluded here exactly
    as the rename tool excludes it, rather than being read as evidence about the
    show's numbering.
    """
    planned: dict[str, list] = {}
    for (season, number), path in matched.items():
        if is_extras_path(os.path.relpath(path, show_path)):
            continue
        planned.setdefault(path, []).append((season, number))
    return planned
