"""Identify episode files that carry no SxxExx marker.

Some releases name episodes only by title ("EP01 - Night of the Sentinels.mkv"),
so the season and episode have to come from somewhere else. Matching the title
against TMDB's episode list works, but only with guards: X-Men The Animated
Series has four "Beyond Good and Evil" parts and two "Days of Future Past"
parts, and a bare title that could be any of them must be refused rather than
attributed to whichever one sorts first.
"""

import difflib
import os
import re

# A fuzzy match below this is not trusted at all.
MATCH_CUTOFF = 0.86
# A fuzzy match must beat the runner-up by this much, or the two are too close
# to tell apart and the file is left alone.
MATCH_MARGIN = 0.06

_LEADING_EPISODE_TAG = re.compile(r'^\s*(?:ep|episode|e)\s*\d+\s*(?:[-–_.]\s*)?', re.I)
# "Pt. 2", "Part Two", "Part II" and "(Part 2)" all mean TMDB's "(2)". The
# part word is required before the numeral, so a title like "I Am Legend" is
# never read as a part number.
_PART_ALTERNATIVES = r'[0-9]+|one|two|three|four|five|six|i{1,3}v?|iv|vi?'
_PART_WORD = re.compile(rf'\(\s*(?:part|pt)\.?\s*({_PART_ALTERNATIVES})\s*\)', re.I)
_BARE_PART_WORD = re.compile(rf'\b(?:part|pt)\.?\s*({_PART_ALTERNATIVES})\b', re.I)
_WORD_NUMBERS = {
    'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6',
    'i': '1', 'ii': '2', 'iii': '3', 'iv': '4', 'v': '5', 'vi': '6',
}
_RELEASE_NOISE = re.compile(
    r'\b(1080p|2160p|720p|480p|x264|x265|hevc|aac\d?|ac3|ddp?\d?|h ?26[45]|web-?dl|'
    r'webrip|bluray|dvd|ai upscale|10bit|remux|proper|repack)\b',
    re.I,
)


_EXTRAS_FOLDERS = re.compile(
    r'^(featurettes?|extras?|bonus|deleted[ _-]?scenes|behind[ _-]the[ _-]scenes|'
    r'interviews?|outtakes?|specials?[ _-]features?|making[ _-]of)$',
    re.I,
)


def is_extras_path(relative_path: str) -> bool:
    """Whether a file sits in a folder that marks it as bonus material.

    Extras must never be matched against the episode list. "Campaign Ads", a
    Parks and Recreation featurette, otherwise matches the episode "Campaign Ad"
    closely enough to be renamed as it — and if the real episode were absent,
    nothing would catch the mistake.
    """
    parts = os.path.dirname(str(relative_path or '')).replace('\\', '/').split('/')
    return any(_EXTRAS_FOLDERS.match(part.strip()) for part in parts if part.strip())


_SHOW_STOPWORDS = frozenset({'the', 'a', 'an', 'of', 'and', 'season', 'series', 'complete'})
_EPISODE_MARKER_SPLIT = re.compile(r's\d{1,2}\s*e\d{1,3}', re.I)


def _show_tokens(text: str) -> set:
    """Significant words of a show name, punctuation and release noise removed."""
    text = _RELEASE_NOISE.sub(' ', str(text or ''))
    text = re.sub(r'\((?:19|20)\d{2}\)|\b(?:19|20)\d{2}\b', ' ', text)
    words = re.split(r'[^a-z0-9]+', text.lower())
    return {w for w in words if w and w not in _SHOW_STOPWORDS and not w.isdigit()}


def names_other_show(filename: str, show_title: str) -> bool:
    """Whether a filename's leading text names a show other than this one.

    A file from another show that happens to sit in this folder still carries a
    usable SxxExx marker, so nothing stops it being indexed as this show's
    episode — and if it is also the larger file, it wins. That is how a
    Chernobyl episode came to be filed, renamed and played as Parks and
    Recreation S01E01.

    Only an outright disagreement counts. Releases abbreviate ("Parks.and.Rec"),
    so a single shared significant word is enough to accept; the text before the
    marker naming nothing recognisable at all is also accepted, because plenty
    of files are named bare ("S01E01.mkv"). What is refused is a prefix with
    real words, none of which belong to this show.

    Only meaningful for a file carrying a marker. With no marker there is no
    prefix to read — the whole name is the title — and a title-only file like
    "Night of the Sentinels.mkv" shares no word with its show by design, so
    judging it here would refuse the very files worth matching by title.
    """
    marker = _EPISODE_MARKER_SPLIT.search(os.path.basename(str(filename or '')))
    if not marker:
        return False
    prefix_tokens = _show_tokens(os.path.basename(str(filename))[:marker.start()])
    title_tokens = _show_tokens(show_title)
    if not prefix_tokens or not title_tokens:
        return False
    return not (prefix_tokens & title_tokens)


def normalise_episode_title(raw: str) -> str:
    """Reduce a filename or TMDB title to a comparable form.

    "(Part 2)" and "(2)" are the same episode written two ways, so both collapse
    to the same text; TMDB uses the latter and release names often use the former.
    """
    def as_number(match):
        value = match.group(1).lower()
        return f'({_WORD_NUMBERS.get(value, value)})'

    text = os.path.splitext(str(raw or ''))[0]
    text = _LEADING_EPISODE_TAG.sub('', text)
    text = re.sub(r'\[[^\]]*\]', ' ', text)
    text = _PART_WORD.sub(as_number, text)
    text = _BARE_PART_WORD.sub(as_number, text)
    text = _RELEASE_NOISE.sub(' ', text)
    text = re.sub(r'[._]+', ' ', text)
    # Keep the digits inside "(2)" but drop decorative punctuation.
    text = re.sub(r'[^a-z0-9()\s]+', ' ', text.lower())
    return re.sub(r'\s+', ' ', text).strip()


def match_episode_title(filename: str, episodes: list[dict]) -> tuple[dict | None, str]:
    """Find the episode a title-only filename refers to.

    Returns (episode, reason). `episode` is None when nothing can be said with
    confidence, and `reason` explains which it was — 'exact', 'fuzzy',
    'ambiguous', or 'no-match' — so a dry run can show why a file was skipped.
    """
    target = normalise_episode_title(filename)
    if not target or not episodes:
        return None, 'no-match'

    by_title: dict[str, list[dict]] = {}
    for episode in episodes:
        key = normalise_episode_title(episode.get('title') or '')
        if key:
            by_title.setdefault(key, []).append(episode)

    if target in by_title:
        candidates = by_title[target]
        # Two episodes sharing a title cannot be told apart by title alone.
        return (candidates[0], 'exact') if len(candidates) == 1 else (None, 'ambiguous')

    # A release can add a subtitle TMDB does not carry: "Beyond Good and Evil
    # (Part 1) The End of Time" against TMDB's "Beyond Good and Evil (1)". The
    # TMDB title being a whole leading phrase — part number included — is a
    # confident match, and the part number is what stops (1) matching (2).
    prefix_hits = [
        episode for episode in episodes
        if _is_leading_phrase(normalise_episode_title(episode.get('title') or ''), target)
    ]
    if len(prefix_hits) == 1:
        return prefix_hits[0], 'prefix'
    if len(prefix_hits) > 1:
        return None, 'ambiguous'

    # Releases reorder the parts of a title: "The Phoenix Saga, Part I Sacrifice"
    # against TMDB's "The Phoenix Saga: Sacrifice (1)". The same words in a
    # different order is still a confident match, and because the part number is
    # one of those words, part 1 cannot be mistaken for part 2.
    target_tokens = _token_set(target)
    if target_tokens:
        token_hits = [
            episode for episode in episodes
            if _token_set(normalise_episode_title(episode.get('title') or '')) == target_tokens
        ]
        if len(token_hits) == 1:
            return token_hits[0], 'tokens'
        if len(token_hits) > 1:
            return None, 'ambiguous'

    scored = sorted(
        ((difflib.SequenceMatcher(None, target, key).ratio(), key) for key in by_title),
        reverse=True,
    )
    if not scored or scored[0][0] < MATCH_CUTOFF:
        return None, 'no-match'

    best_ratio, best_key = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_ratio - runner_up < MATCH_MARGIN:
        # "Days of Future Past" sits this close to both of its numbered parts.
        return None, 'ambiguous'
    if len(by_title[best_key]) != 1:
        return None, 'ambiguous'
    return by_title[best_key][0], 'fuzzy'


def match_episode_files(filenames: list[str], episodes: list[dict]) -> dict[str, dict]:
    """Match a show's title-only files to episodes, resolving what it can.

    Runs the confident matches first, then settles the leftovers by elimination:
    if "Night of the Sentinels" could be part 1 or 2 and part 2 is already
    claimed by a file that says so, part 1 is the only remaining option. That is
    deduction from the rest of the set, not a guess about this one file.

    Returns {filename: episode} for what could be identified.
    """
    # Grouped by title first: a library often holds the same episode twice (an
    # .mkv and an .mp4), and those must not compete for the one episode slot —
    # otherwise the second copy looks unidentifiable.
    groups: dict[str, list[str]] = {}
    for filename in filenames:
        groups.setdefault(normalise_episode_title(filename), []).append(filename)

    resolved: dict[str, dict] = {}
    claimed: set[tuple[int, int]] = set()
    undecided: list[str] = []

    for title, group in groups.items():
        episode, reason = match_episode_title(group[0], episodes)
        if episode and reason in ('exact', 'prefix', 'tokens', 'fuzzy'):
            for filename in group:
                resolved[filename] = episode
            claimed.add((episode['season_number'], episode['episode_number']))
        else:
            undecided.append(title)

    for title in undecided:
        # Candidates are the numbered parts of the same story that nothing
        # confident has claimed yet.
        candidates = [
            episode for episode in episodes
            if (episode['season_number'], episode['episode_number']) not in claimed
            and _same_story(title, normalise_episode_title(episode.get('title') or ''))
        ]
        if len(candidates) == 1:
            for filename in groups[title]:
                resolved[filename] = candidates[0]
            claimed.add((candidates[0]['season_number'], candidates[0]['episode_number']))

    return resolved


# Short titles make careless prefixes: "Reunion" leads far too many names.
MIN_PREFIX_LENGTH = 10


def _is_leading_phrase(tmdb_title: str, local_title: str) -> bool:
    """Whether the TMDB title is the opening phrase of the local one."""
    if len(tmdb_title) < MIN_PREFIX_LENGTH or tmdb_title == local_title:
        return False
    # The break must fall on a word boundary, so "Obsession" cannot lead
    # "Obsessionology".
    return local_title.startswith(tmdb_title + ' ')


def _token_set(text: str) -> frozenset:
    """Words of a normalised title, order discarded, tiny filler words dropped."""
    tokens = {t.strip('()') for t in text.split() if t.strip('()')}
    return frozenset(t for t in tokens if t not in {'the', 'a', 'an', 'of', 'and'})


def _same_story(local: str, tmdb_title: str) -> bool:
    """Whether two titles name the same story, ignoring any part number."""
    if not local or not tmdb_title:
        return False
    strip_part = lambda text: re.sub(r'\s*\(\d+\)\s*$', '', text).strip()
    return strip_part(local) == strip_part(tmdb_title)
