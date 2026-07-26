"""Canonical library naming rules.

Titles follow how TMDB presents them publicly; this module only adjusts what
Windows will not accept in a path. The layouts are:

    movies: <library_root>/<Title> (<Year>)/<Title> (<Year>).<ext>
    tv:     <library_root>/<Title>/<Title>.<ext>

Both rules were taken from the existing library rather than invented: 288 of
291 movie folders are year-stamped, and all 38 TV folders are not.

Canonical names are always derived from the stored TMDB title and year, never
from a torrent/release name, so a misnamed release cannot rename a library
entry.
"""

import os
import re


# Windows forbids  < > : " / \ | ? *  in path components. Anything not listed
# here is dropped. Keep this map in one place so the house style stays
# reviewable — change a value and every future rename follows.
ILLEGAL_CHAR_MAP = {
    # Dropped rather than turned into a dash: the library already resolves
    # colons this way 51 times to 6 ("Alien: Covenant" -> "Alien Covenant").
    ':': '',
    '/': '-',
    '\\': '-',
    '|': '-',
    '"': "'",
    '<': '',
    '>': '',
    '?': '',
    '*': '',
}

# Bare device names Windows refuses to use as a file or folder name.
_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}


def sanitize_title(title: str) -> str:
    """Make a TMDB title safe for a Windows path while preserving its look."""
    text = (title or '').strip()
    if not text:
        return ''

    for bad, replacement in ILLEGAL_CHAR_MAP.items():
        text = text.replace(bad, replacement)

    # Control characters are illegal too and never meaningful in a title.
    text = ''.join(ch for ch in text if ord(ch) >= 32)

    text = re.sub(r'\s+', ' ', text).strip()

    # Windows silently drops trailing dots/spaces, which would make the name we
    # write differ from the name we later look for.
    text = text.rstrip('. ')

    if text.upper() in _RESERVED_NAMES:
        text = f'{text}_'

    return text


def canonical_stem(title: str, year: int | None, media_type: str = 'movie') -> str:
    """Return the shared folder/file stem, e.g. "Blade Trinity (2004)".

    TV titles are not year-stamped, matching how the library already stores
    them; a series spans years, so a single one would be misleading.
    """
    clean = sanitize_title(title)
    if not clean:
        return ''
    if (media_type or 'movie') == 'tv':
        return clean
    return f'{clean} ({year})' if year else clean


def canonical_season_folder(season_number: int) -> str:
    """Season folder name, zero-padded: "Season 04"."""
    return f'Season {int(season_number):02d}'


def canonical_episode_name(
    show_title: str,
    season_number: int,
    episode_number: int,
    episode_title: str | None = None,
    extension: str = '',
) -> str:
    """Filename for one episode: "Show - S04E02 - Episode Title.mkv".

    The episode title is included when known, since that is how the correctly
    named files in this library already read, but it is optional — an episode
    identified only by its number still gets a valid canonical name.
    """
    show = sanitize_title(show_title) or 'Show'
    marker = f'S{int(season_number):02d}E{int(episode_number):02d}'

    ext = (extension or '').strip()
    if ext and not ext.startswith('.'):
        ext = f'.{ext}'

    episode = sanitize_title(episode_title or '')
    if episode:
        return f'{show} - {marker} - {episode}{ext}'
    return f'{show} - {marker}{ext}'


def canonical_paths(
    library_root: str,
    title: str,
    year: int | None,
    extension: str,
    media_type: str = 'movie',
) -> tuple[str, str] | None:
    """Return (folder_path, file_path) for a title, or None if unnameable."""
    stem = canonical_stem(title, year, media_type)
    if not stem or not library_root:
        return None

    ext = (extension or '').strip()
    if ext and not ext.startswith('.'):
        ext = f'.{ext}'

    folder = os.path.join(library_root, stem)
    return folder, os.path.join(folder, f'{stem}{ext}')
