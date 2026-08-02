"""Working out what a file or folder actually holds.

Release names are the only evidence available before anything is opened, and
they are unreliable: a folder may be one film, a film plus its bonus features, or
a box set of several; a filename may name an episode, a range of episodes, or
nothing useful at all. The rules here are the ones that survived contact with a
real library — a title is cut at its release year so "Blade Runner 2049" keeps
its numerals, a pack is only a pack when two or more feature-sized videos each
carry their own year, and an episode marker is trusted only when the text in
front of it does not name a different show.

Pure functions over paths and strings: nothing here reads the database, calls
TMDB, or needs the Flask application.
"""

import os
import re
import unicodedata
from datetime import datetime

from medialibrary.episode_match import names_other_show

VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'}

# Size floor for treating a video as a feature film rather than a sample or
# extra, used when deciding whether a folder holds several distinct films.
PACK_MIN_FEATURE_BYTES = 300 * 1024 * 1024


def _best_local_video_path(media_path: str | None) -> str | None:
    path = (media_path or '').strip()
    if not path:
        return None

    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
        return path if ext in VIDEO_EXTENSIONS else None

    if not os.path.isdir(path):
        return None

    candidates: list[tuple[int, str]] = []
    try:
        for root, _dirs, files in os.walk(path):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in VIDEO_EXTENSIONS:
                    continue
                full = os.path.join(root, fname)
                try:
                    size = os.path.getsize(full)
                except Exception:
                    size = 0
                candidates.append((size, full))
    except Exception:
        return None

    if not candidates:
        return None
    return max(candidates, key=lambda t: t[0])[1]


def _videos_in(folder_path: str) -> list[str]:
    found = []
    if not folder_path or not os.path.isdir(folder_path):
        return found
    for root, _dirs, files in os.walk(folder_path):
        for name in files:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
                found.append(os.path.join(root, name))
    return found


def _feature_videos_in(folder_path: str) -> list[str]:
    """Video files in a folder large enough to be features rather than extras.

    Used to spot multi-film pack folders; the size floor keeps samples,
    trailers and featurettes from being mistaken for separate films.
    """
    found: list[str] = []
    try:
        for root, _dirs, files in os.walk(folder_path):
            for name in files:
                if os.path.splitext(name)[1].lower() not in VIDEO_EXTENSIONS:
                    continue
                full = os.path.join(root, name)
                try:
                    if os.path.getsize(full) >= PACK_MIN_FEATURE_BYTES:
                        found.append(full)
                except OSError:
                    continue
    except Exception:
        return []
    return sorted(found)


EPISODE_MARKER = re.compile(r'[Ss](\d{1,2})[ ._-]*[Ee](\d{1,3})')

# One file can cover several episodes, e.g. "S04E01-E02" for a two-part premiere
# that aired as one. Without this the later episodes look missing.
# The second number must carry its own E, or be joined by a bare hyphen, so
# "S01E01 - 1984" and "S01E01.2160p" are never read as ranges.
EPISODE_RANGE_TAIL = re.compile(r'^(?:[ ._-]*[Ee](\d{1,3})|-(\d{1,3})(?![\dp]))', re.I)

# A guard against a misparse inventing a huge span of episodes.
EPISODE_RANGE_MAX_SPAN = 8


def _episodes_covered(filename: str) -> tuple[int, list[int]] | None:
    """Season and every episode number a filename claims, or None."""
    marker = EPISODE_MARKER.search(filename)
    if not marker:
        return None

    season = int(marker.group(1))
    first = last = int(marker.group(2))
    tail = filename[marker.end() :]
    while True:
        step = EPISODE_RANGE_TAIL.match(tail)
        if not step:
            break
        following = int(step.group(1) or step.group(2))
        if following <= last or following - first > EPISODE_RANGE_MAX_SPAN:
            break
        last = following
        tail = tail[step.end() :]
    return season, list(range(first, last + 1))


_SEASON_DIR_EXACT = re.compile(r'^(?:season\s*|s)(\d{1,2})$', re.I)

_SPECIALS_DIR = re.compile(r'^specials?$', re.I)

# A season embedded in a longer folder name, e.g.
# "Harley Quinn (2019) Season 3 S03 (1080p ...)". Ranges such as "Season 1-9" /
# "S01-S09" span a whole series and cannot be attributed to one season, so both
# ends of a range are rejected — the lookbehind matters because otherwise the
# tail of "S01-S09" matches on its own.
_SEASON_DIR_EMBEDDED = re.compile(r'(?<![-–])\bseason\s*(\d{1,2})(?!\s*[-–]\s*\d)', re.I)

_SEASON_DIR_SXX = re.compile(r'(?<![-–])\bs(\d{2})(?![\d\-–])', re.I)


def _infer_season_from_path(show_path: str, file_path: str) -> int | None:
    """Season a non-episode file belongs to, taken from its folders.

    Only directory names are considered — a featurette called "Season 4 Overview"
    sitting in Season 1 belongs to Season 1. The deepest folder wins, so a nested
    season folder beats a release folder above it. Returns None when no folder
    names a single season, which is the case for show-wide extras.
    """
    try:
        relative = os.path.relpath(file_path, show_path)
    except ValueError:
        return None

    parts = [p for p in os.path.dirname(relative).split(os.sep) if p and p != '.']
    for name in reversed(parts):
        if _SPECIALS_DIR.match(name):
            return 0
        for pattern in (_SEASON_DIR_EXACT, _SEASON_DIR_EMBEDDED, _SEASON_DIR_SXX):
            found = pattern.search(name)
            if found:
                return int(found.group(1))
    return None


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _index_show_folder(
    show_path: str, show_title: str
) -> tuple[dict[tuple[int, int], list[str]], list[str]]:
    """Every video in a show folder, grouped by the episode its name claims.

    Keeps *all* the files claiming an episode rather than picking one, so a
    caller can ask either "which file is this episode" or "is this episode held
    more than once".
    """
    by_episode: dict[tuple[int, int], list[str]] = {}
    unmatched: list[str] = []
    if not show_path or not os.path.isdir(show_path):
        return by_episode, unmatched

    show_title = show_title or os.path.basename(os.path.normpath(show_path))
    for root, _dirs, files in os.walk(show_path):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            full = os.path.join(root, name)
            covered = _episodes_covered(name)
            if not covered or names_other_show(name, show_title):
                unmatched.append(full)
                continue
            season, episode_numbers = covered
            for episode in episode_numbers:
                by_episode.setdefault((season, episode), []).append(full)
    return by_episode, unmatched


def scan_local_episodes(
    show_path: str, show_title: str = ''
) -> tuple[dict[tuple[int, int], str], list[str]]:
    """Index a show folder by (season, episode), plus any files with no marker.

    Only an SxxExx marker in the filename is trusted, and the folder it sits in is
    ignored — files do turn up under the wrong season folder. About a fifth of
    this library has no marker at all, and those are overwhelmingly featurettes
    and extras rather than episodes, so inferring numbers from folder order would
    invent episodes that do not exist. Unmatched files are returned separately so
    they can be listed without being given an episode number.

    A marker is not trusted when the filename names a different show — see
    `names_other_show`. Such a file is reported as unmatched, so it stays visible
    without being played, counted or renamed as an episode it is not.

    Where an episode is held more than once the largest is listed, because the
    list has to show something. That choice is not a judgement about which copy
    is better and it discards nothing — see `duplicate_episode_files`, which is
    how the duplicates are surfaced for a person to settle.
    """
    by_episode, unmatched = _index_show_folder(show_path, show_title)
    matched = {key: max(paths, key=_file_size) for key, paths in by_episode.items()}
    return matched, unmatched


def duplicate_episode_files(
    show_path: str, show_title: str = ''
) -> dict[tuple[int, int], list[str]]:
    """Episodes held more than once, largest copy first.

    Two files claiming one episode is not something the app should quietly
    resolve. Which copy to keep depends on codec, subtitles, disc space and
    whether an upscale is wanted over its source — none of which a file can
    settle, and deleting the wrong one cannot be undone. So this reports them
    and touches nothing.
    """
    by_episode, _unmatched = _index_show_folder(show_path, show_title)
    return {
        key: sorted(paths, key=_file_size, reverse=True)
        for key, paths in by_episode.items()
        if len(paths) > 1
    }


def _pack_films_in(folder_path: str) -> list[str]:
    """Feature-sized videos in a folder that each carry their own release year.

    This is what separates a box set from a single film shipped with extras: a
    collection names every film with its year ("... Dead Mans Chest 2006"),
    while featurettes and deleted scenes never do. Size alone is not enough —
    bonus features routinely run past the feature size floor.
    """
    films = []
    for video in _feature_videos_in(folder_path):
        _title, year = normalize_media_name(os.path.basename(video), 'movie')
        if year:
            films.append(video)
    return films


def film_in_pack_for(
    content_path: str, title: str | None, year: int | None
) -> tuple[str | None, list[str]]:
    """Which film in a multi-film download is the one being filed.

    Returns (the matching video or None, every film found). A pack is only a
    pack when two or more feature-sized videos each carry their own year, so a
    film shipped with extras is unaffected and returns no films at all.

    Size cannot answer this. A Predator pack of five films filed its *largest*
    video as "Predator (1987)", which put Predator 2 in the 1987 folder — the
    same class of mistake as filing a Chernobyl episode under Parks and
    Recreation, and just as invisible afterwards, since the file is renamed on
    the way in. So the year has to agree as well as the title: without it,
    "Predator" matches "Predators" and "The Predator" too.
    """
    films = _pack_films_in(content_path)
    if len(films) < 2:
        return None, []

    matches = []
    for film in films:
        candidate_title, candidate_year = normalize_media_name(os.path.basename(film), 'movie')
        if not _titles_likely_match(title, candidate_title):
            continue
        if year and candidate_year and int(year) != int(candidate_year):
            continue
        matches.append(film)

    # Exactly one, or nothing: two candidates mean the pack cannot be read
    # confidently, and guessing renames a file into a name that hides the error.
    return (matches[0] if len(matches) == 1 else None), films


def _detect_release_year(text: str) -> tuple[int | None, int | None]:
    """Find the release year and where the release noise starts.

    Returns (year, cut_index). Everything from cut_index onwards is release
    metadata (quality, codec, group) rather than part of the title.

    A parenthesised year wins outright. Otherwise the *last* plausible year is
    taken, so a numeral that belongs to the title keeps its place — in
    "Blade Runner 2049 2017 1080p" the title is "Blade Runner 2049", not
    "Blade Runner".
    """
    max_year = datetime.now().year + 2

    for match in re.finditer(r'\(\s*(19\d{2}|20\d{2})\s*\)', text):
        year = int(match.group(1))
        if 1900 <= year <= max_year:
            return year, match.start()

    found: list[tuple[int, int]] = []
    for match in re.finditer(r'\b(19\d{2}|20\d{2})\b', text):
        year = int(match.group(1))
        if 1900 <= year <= max_year:
            found.append((year, match.start()))
    return found[-1] if found else (None, None)


def normalize_media_name(raw_name: str, media_type: str) -> tuple[str, int | None]:
    """Convert a filename or folder name into a cleaner IMDb search query."""
    base_name = raw_name.rstrip('/\\')
    stem, ext = os.path.splitext(base_name)
    if ext.lower() in VIDEO_EXTENSIONS:
        base_name = stem

    text = re.sub(r'[._]+', ' ', base_name)
    text = re.sub(r'\[[^\]]*\]', ' ', text)

    year, cut = _detect_release_year(text)
    # A cut at position 0 means the year opens the name, so it is the title
    # itself ("2012") rather than a suffix — keep the text intact.
    if cut:
        text = text[:cut]

    cleanup_patterns = [
        r'\bS\d{1,2}E\d{1,2}\b',
        r'\bSeason\s+\d+\b',
        r'\bComplete\b',
        r'\b(2160p|1440p|1080p|720p|480p|4k)\b',
        r'\b(BluRay|BRRip|BDRip|WEBRip|WEB-DL|HDRip|DVDRip|HDTV|REMUX)\b',
        r'\b(x264|x265|h264|h265|HEVC|AAC|DDP?\d?(?:\.\d)?|Atmos)\b',
        r'\b(YIFY|RARBG|ETHEL|PSA|Vyndros|CtrlHD)\b',
        r'\b(Proper|Repack|Extended|Unrated|Criterion|Multi(?:sub)?|Dual Audio)\b',
    ]
    for pattern in cleanup_patterns:
        text = re.sub(pattern, ' ', text, flags=re.IGNORECASE)

    if media_type == 'tv':
        text = re.sub(r'\bEpisode\s+\d+\b', ' ', text, flags=re.IGNORECASE)

    text = re.sub(r'\([^)]*\)', ' ', text)
    text = re.sub(r'[^A-Za-z0-9]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text, year


def _normalized_title_tokens(value: str | None) -> list[str]:
    s = unicodedata.normalize('NFKD', value or '')
    s = ''.join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    roman = {
        'viii': '8',
        'vii': '7',
        'vi': '6',
        'iv': '4',
        'iii': '3',
        'ii': '2',
        'ix': '9',
        'xi': '11',
        'xii': '12',
    }
    parts = [roman.get(part, part) for part in s.split()]
    return [part for part in parts if part]


def _titles_likely_match(expected: str | None, candidate: str | None) -> bool:
    expected_tokens = _normalized_title_tokens(expected)
    candidate_tokens = _normalized_title_tokens(candidate)
    if not expected_tokens or not candidate_tokens:
        return False
    if expected_tokens == candidate_tokens:
        return True

    expected_set = set(expected_tokens)
    candidate_set = set(candidate_tokens)
    overlap = len(expected_set & candidate_set)
    expected_ratio = overlap / max(1, len(expected_set))
    candidate_ratio = overlap / max(1, len(candidate_set))

    # Require substantial overlap so franchise roots do not match specific sequels.
    if expected_ratio >= 0.75 and candidate_ratio >= 0.75:
        return True

    expected_joined = ' '.join(expected_tokens)
    candidate_joined = ' '.join(candidate_tokens)
    if expected_joined in candidate_joined or candidate_joined in expected_joined:
        return len(expected_tokens) <= 2 and len(candidate_tokens) <= 2

    return False


def choose_search_result(results, title: str, media_type: str, year: int | None):
    """Pick the most likely IMDb result for a local library entry."""

    def normalized(value: str | None) -> str:
        """Lowercase, strip punctuation, normalise roman numerals and collapse whitespace."""
        s = unicodedata.normalize('NFKD', value or '')
        s = ''.join(ch for ch in s if not unicodedata.combining(ch)).lower()
        s = re.sub(r'[^a-z0-9\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        # Convert common roman numerals to arabic so '2' matches 'ii', etc.
        _roman = {
            'viii': '8',
            'vii': '7',
            'vi': '6',
            'iv': '4',
            'iii': '3',
            'ii': '2',
            'ix': '9',
            'xi': '11',
            'xii': '12',
        }
        parts = s.split()
        parts = [_roman.get(p, p) for p in parts]
        return ' '.join(parts)

    normalized_title = normalized(title)
    query_tokens = set(normalized_title.split())

    filtered = [item for item in results if item.get('media_type') == media_type] or list(results)

    # Exact title + exact year
    if year is not None:
        for item in filtered:
            if (
                abs((item.get('year') or 0) - year) <= 1
                and normalized(item.get('title')) == normalized_title
            ):
                return item

    # Exact title, any year
    for item in filtered:
        candidate = normalized(item.get('title'))
        if candidate == normalized_title:
            return item

    # Score-based fallback to avoid broad partial matches.
    def score(item: dict) -> float:
        candidate = normalized(item.get('title'))
        if not candidate:
            return -1.0

        candidate_tokens = set(candidate.split())
        if not candidate_tokens or not query_tokens:
            return -1.0

        overlap = len(query_tokens & candidate_tokens)
        recall = overlap / len(query_tokens)
        precision = overlap / len(candidate_tokens)
        phrase_bonus = (
            0.2 if (candidate in normalized_title or normalized_title in candidate) else 0.0
        )

        score_val = (recall * 3.0) + (precision * 2.0) + phrase_bonus

        item_year = item.get('year')
        if year is not None and item_year:
            delta = abs(item_year - year)
            if delta <= 1:
                score_val += 1.2
            elif delta <= 2:
                score_val += 0.4
            else:
                score_val -= 0.6

        # Reject broad subset matches when query is clearly more specific.
        if (
            len(query_tokens) >= 4
            and len(candidate_tokens) <= 2
            and candidate_tokens.issubset(query_tokens)
            and not (year is not None and item_year and abs(item_year - year) <= 1)
        ):
            score_val -= 2.0

        return score_val

    ranked = sorted(filtered, key=score, reverse=True)
    if not ranked:
        return None

    best = ranked[0]
    return best if score(best) >= 1.5 else None


def _featurette_label(filename: str) -> str:
    """Readable name for a bonus feature, from a release-style filename."""
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', stem)
    stem = re.sub(r'[._]+', ' ', stem)
    # Codec/source tails are left over once the brackets go, e.g. "..._H.264".
    stem = re.sub(
        r'\b(1080p|2160p|720p|480p|x264|x265|HEVC|AAC\d?|AC3|DDP?\d?|H ?26[45]|'
        r'WEB-?DL|WEBRip|BluRay|DVD|AI Upscale|10bit)\b',
        ' ',
        stem,
        flags=re.IGNORECASE,
    )
    stem = re.sub(r'\s+', ' ', stem).strip(' -–_')
    return stem or os.path.basename(filename)


def _resolve_episode_file(show_path: str, relative: str) -> str | None:
    """Absolute path for an episode inside a show folder.

    Returns None if the resolved path escapes the show folder, so a crafted
    ?episode= cannot be used to read arbitrary files from disk.
    """
    if not show_path or not relative:
        return None
    base = os.path.realpath(show_path)
    target = os.path.realpath(os.path.join(base, relative))
    if target != base and not target.startswith(base + os.sep):
        return None
    if not os.path.isfile(target):
        return None
    if os.path.splitext(target)[1].lower() not in VIDEO_EXTENSIONS:
        return None
    return target


def _is_local_media_missing(media_path: str | None) -> bool:
    path = (media_path or '').strip()
    if not path:
        return True
    return _best_local_video_path(path) is None
