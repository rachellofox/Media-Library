"""Planning canonical renames for TV episodes (F-0808.01).

Separate from `rename_plan` because episodes are the dangerous case. An
earlier hand-run script (`scripts/_plan_tv_naming.py`) named episodes from
TMDB's episode list, and that silently rewrote Buffy season 3 episodes 18-22
into TMDB's aired order when the files were in production order — the titles
ended up on the wrong episodes. This module exists to make that class of
mistake impossible.

The rule, in one line: **a file's own name decides which episode it is, and
this module only tidies the formatting around that.**

Concretely:

- An `SxxExx` marker in the filename is the source of truth for season and
  episode, and is never reassigned. Nothing this module does can change what
  episode a file claims to be.
- The episode title already in the filename is kept, with only release tags
  stripped, so a legitimate non-TMDB ordering survives untouched.
- TMDB is consulted *only* to fill in a title the filename does not have, and
  *only* for a show whose own files prove TMDB's numbering agrees with them —
  see `ordering_is_trusted`. For every other show a missing title stays
  missing rather than being guessed.
- A file with neither a marker nor a recognisable title is reported for manual
  intervention and never touched.
"""

import os
import re

from medialibrary.episode_match import is_extras_path, match_episode_title
from medialibrary.identify import _episodes_covered, scan_local_episodes
from medialibrary.naming import sanitize_title
from medialibrary.rename_plan import ACTIVE_DOWNLOAD_STATES, entry_key
from medialibrary.subtitles import SUBTITLE_EXTENSIONS

_MARKER = re.compile(r'S(\d{1,2})E(\d{1,3})(?:-E(\d{1,3}))?', re.IGNORECASE)

# A trailing "(1080p BluRay x265 RZeroX)" or ".1080p.WEB.h264-EDITH" tag. These
# are the release's name for itself, not part of the episode's title.
_RELEASE_TAG = re.compile(
    r'[\(\[\.\s-]*\b(?:\d{3,4}p|bluray|blu-ray|web-?dl|webrip|hdtv|hevc|x26[45]|h\.?26[45]'
    r'|aac|ac3|eac3|ddp?5|dts|remux|proper|repack|internal|amzn|nf|dsnp|atmos)\b.*$',
    re.IGNORECASE,
)

# Language/flag markers worth carrying over onto a renamed subtitle.
_SUBTITLE_SUFFIXES = {
    'en', 'eng', 'english', 'fr', 'fre', 'french', 'de', 'ger', 'german',
    'es', 'spa', 'spanish', 'it', 'ita', 'nl', 'dut', 'pt', 'por', 'sv',
    'da', 'no', 'fi', 'pl', 'ru', 'ja', 'jpn', 'ko', 'zh', 'chi',
    'forced', 'sdh', 'cc',
}


def _normalize(text: str) -> str:
    text = (text or '').lower()
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def title_from_filename(filename: str) -> str:
    """The episode title already in a filename, or '' if it carries none.

    Everything after the SxxExx marker, with any release tag removed. A name
    that is nothing but a release tag yields '' rather than junk.

    Leading dots are kept: "...And the Bag's in the River" and Alias's "...1"
    are the real titles, and stripping punctuation wholesale renamed both into
    something else.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    match = _MARKER.search(stem)
    if not match:
        return ''

    # Whether dots are this filename's word separators is a property of the
    # whole name, not of the title alone: "Show.S01E02.Some.Title.720p" uses
    # them that way, "Alias - S05E10 - S.O.S" does not. Judging it from the
    # tail alone turned "S.O.S." into "S O S" and Mr. Robot's
    # "eps1.0_hellofriend.mov" into nonsense.
    dot_separated = ' ' not in stem

    tail = _RELEASE_TAG.sub('', stem[match.end():]).strip()
    # Only the separator sitting between the marker and the title.
    tail = re.sub(r'^[\s\-_]+', '', tail)
    if dot_separated:
        tail = tail.replace('.', ' ')

    return re.sub(r'[\s\-_]+$', '', tail).strip()


def _subtitle_suffix(stem: str) -> str:
    parts = stem.replace('_', '.').split('.')
    if len(parts) > 1 and parts[-1].lower() in _SUBTITLE_SUFFIXES:
        return '.' + parts[-1].lower()
    return ''


def ordering_is_trusted(marked: dict, tmdb_titles: dict) -> tuple[bool, str]:
    """Whether TMDB's numbering agrees with this show's own files.

    Evidence is only the files that carry *both* a marker and a title: for
    each, TMDB should call that season/episode the same thing. One
    disagreement is enough to distrust the whole show — a show numbered in
    production order disagrees with TMDB on most of its run, and the cost of
    being wrong is a correct name overwritten with the wrong episode's.

    A show with no evidence either way is not trusted. Guessing needs a
    reason, and "no files have titles yet" is not one.
    """
    if not tmdb_titles:
        return False, 'no TMDB episode list'

    compared = 0
    for (season, number), path in marked.items():
        on_disk = title_from_filename(os.path.basename(path))
        expected = tmdb_titles.get((season, number))
        if not on_disk or not expected:
            continue
        compared += 1
        if _normalize(on_disk) != _normalize(sanitize_title(expected)):
            return False, f'file names disagree with TMDB numbering (S{season:02d}E{number:02d})'
    if not compared:
        return False, 'no file carries both an episode number and a title'
    return True, f'{compared} file names agree with TMDB numbering'


def _episode_targets(marked: dict) -> dict:
    """path -> sorted [(season, number), ...] it covers."""
    covered: dict[str, list[tuple[int, int]]] = {}
    for key, path in marked.items():
        covered.setdefault(path, []).append(key)
    for entries in covered.values():
        entries.sort()
    return covered


def plan_show_renames(item: dict, preset, tmdb_titles: dict, tmdb_episodes: list[dict]) -> dict:
    """Rename plan for one show. Reads the disk; never writes to it."""
    show_path = (item['path'] or '').strip()
    show_title = item['title'] or ''
    result = {
        'media_id': int(item['id']),
        'show': show_title,
        'episodes': [],
        'subtitles': [],
        'manual': [],
        'trusted': False,
        'trust_reason': '',
    }
    if not show_path or not os.path.isdir(show_path):
        return result

    marked, unmarked = scan_local_episodes(show_path, show_title)
    # A file filed under Featurettes was put there deliberately; a number in
    # its name does not make it part of the run.
    marked = {
        key: path
        for key, path in marked.items()
        if not is_extras_path(os.path.relpath(path, show_path))
    }

    trusted, reason = ordering_is_trusted(marked, tmdb_titles)
    result['trusted'] = trusted
    result['trust_reason'] = reason

    # Episodes are placed inside the show's existing folder. Renaming the
    # folder itself is the title-level job in `rename_plan`, applied after
    # these, so the paths here stay valid while they move.
    final_for: dict[tuple[int, int], str] = {}

    for path, covered in _episode_targets(marked).items():
        season, first = covered[0]
        last = covered[-1][1]
        on_disk_title = title_from_filename(os.path.basename(path))
        if on_disk_title:
            episode_title = on_disk_title
        elif trusted:
            # The only place TMDB is allowed to contribute, and only because
            # this show's own files just proved its numbering matches.
            episode_title = tmdb_titles.get((season, first)) or ''
        else:
            episode_title = ''

        stem = preset.episode_file_stem(
            show_title, season, first, episode_title, last_episode=last
        )
        extension = os.path.splitext(path)[1]
        target = os.path.join(
            show_path, preset.season_folder_name(season), f'{stem}{extension}'
        )
        final_for[(season, first)] = target
        if entry_key(path) == entry_key(target):
            continue
        result['episodes'].append(
            {
                'kind': 'episode',
                'key': entry_key(path),
                'media_id': int(item['id']),
                'show': show_title,
                'season': season,
                'episode': first,
                'from': path,
                'to': target,
                'from_name': os.path.relpath(path, show_path),
                'to_name': os.path.relpath(target, show_path),
                'title_source': (
                    'filename' if on_disk_title else ('tmdb' if episode_title else 'none')
                ),
                'conflict': os.path.exists(target) and entry_key(target) != entry_key(path),
            }
        )

    # A subtitle has to sit beside its episode and share its name, or the
    # player stops finding it.
    for root, _dirs, files in os.walk(show_path):
        for name in files:
            extension = os.path.splitext(name)[1].lower()
            if extension not in SUBTITLE_EXTENSIONS:
                continue
            covered = _episodes_covered(name)
            if not covered:
                continue
            season, numbers = covered
            episode_target = final_for.get((season, numbers[0]))
            if not episode_target:
                continue
            source = os.path.join(root, name)
            stem = os.path.splitext(os.path.basename(episode_target))[0]
            suffix = _subtitle_suffix(os.path.splitext(name)[0])
            target = os.path.join(
                os.path.dirname(episode_target), f'{stem}{suffix}{extension}'
            )
            if entry_key(source) == entry_key(target):
                continue
            result['subtitles'].append(
                {
                    'kind': 'subtitle',
                    'key': entry_key(source),
                    'media_id': int(item['id']),
                    'show': show_title,
                    'season': season,
                    'episode': numbers[0],
                    'from': source,
                    'to': target,
                    'from_name': os.path.relpath(source, show_path),
                    'to_name': os.path.relpath(target, show_path),
                    'title_source': 'sidecar',
                    'conflict': os.path.exists(target) and entry_key(target) != entry_key(source),
                }
            )

    # Files with no marker at all. A title-only file can still be identified,
    # but only against a show whose numbering is trusted — otherwise deriving
    # its number from TMDB is the Buffy mistake in a different order.
    for path in unmarked:
        relative = os.path.relpath(path, show_path)
        if is_extras_path(relative):
            continue
        reason = 'no episode number in the filename'
        if trusted and tmdb_episodes:
            _episode, why = match_episode_title(os.path.basename(path), tmdb_episodes)
            reason = f'title did not identify one episode ({why})'
        result['manual'].append({'show': show_title, 'file': relative, 'reason': reason})

    return result


def plan_tv_renames(store, preset, episode_titles_for, episodes_for) -> dict:
    """Every show's plan, plus the counts the Tools section reports.

    `episode_titles_for(tmdb_id)` returns {(season, number): title} and
    `episodes_for(tmdb_id)` the raw TMDB episode list. Both are passed in so
    this module never reaches for TMDB or the cache itself, which keeps it
    testable without either.
    """
    shows: list[dict] = []
    skipped: list[dict] = []

    for row in store.list_media_items():
        item = dict(row)
        if (item['media_type'] or '') != 'tv':
            continue
        if (item['download_status'] or '').strip().lower() in ACTIVE_DOWNLOAD_STATES:
            skipped.append(
                {
                    'media_id': int(item['id']),
                    'title': item['title'] or '',
                    'reason': 'download in progress',
                }
            )
            continue
        plan = plan_show_renames(
            item,
            preset,
            episode_titles_for(item['tmdb_id']),
            episodes_for(item['tmdb_id']),
        )
        if plan['episodes'] or plan['subtitles'] or plan['manual']:
            shows.append(plan)

    entries = [entry for show in shows for entry in show['episodes'] + show['subtitles']]

    # Two files planned onto one name means an identification is wrong. Refuse
    # both rather than letting the first win and the second fail.
    claimed: dict[str, list[dict]] = {}
    for entry in entries:
        claimed.setdefault(entry_key(entry['to']), []).append(entry)
    for group in claimed.values():
        if len(group) > 1:
            for entry in group:
                entry['conflict'] = True

    blocked = sum(1 for entry in entries if entry['conflict'])
    return {
        'shows': shows,
        'skipped': skipped,
        'total': len(entries),
        'blocked': blocked,
        'renameable': len(entries) - blocked,
        'manual_count': sum(len(show['manual']) for show in shows),
        'untrusted_shows': [
            {'show': show['show'], 'reason': show['trust_reason']}
            for show in shows
            if not show['trusted']
        ],
    }


def apply_tv_renames(plan: dict, selected_keys: set[str] | None = None) -> dict:
    """Rename the chosen entries. Episodes before subtitles, so a sidecar's
    destination folder exists by the time it moves.

    No database write: an episode lives inside the show's folder, which is
    what `media_items.path` records, and that does not change here.
    """
    renamed = 0
    failed: list[dict] = []
    skipped_conflict = 0

    entries = [entry for show in plan.get('shows') or [] for entry in show['episodes']]
    entries += [entry for show in plan.get('shows') or [] for entry in show['subtitles']]

    for entry in entries:
        if selected_keys is not None and entry['key'] not in selected_keys:
            continue
        if entry['conflict']:
            skipped_conflict += 1
            continue
        try:
            os.makedirs(os.path.dirname(entry['to']), exist_ok=True)
            # os.rename, never shutil.move: on a locked file shutil silently
            # falls back to copy-then-delete and leaves a duplicate behind.
            os.rename(entry['from'], entry['to'])
            renamed += 1
        except OSError as exc:
            failed.append({'from': entry['from_name'], 'error': str(exc)})

    return {'renamed': renamed, 'failed': failed, 'skipped_conflict': skipped_conflict}
