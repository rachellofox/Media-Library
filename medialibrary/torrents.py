"""Finding torrent candidates for a title.

Wraps the search client with the ranking the UI needs: preferred quality first,
trusted release groups next, seeders as the tiebreak. A search that could not
reach its mirrors raises rather than returning an empty list, because "nothing
found" and "could not look" are different answers and were once the same bug.
"""

from medialibrary import runtime
from medialibrary.config import TRUSTED_RELEASE_GROUPS
from medialibrary.qb_search import configured_mirror_urls
from medialibrary.quality import compare_quality, detect_quality


def _torrent_candidates_for(meta: dict, limit: int = 25) -> list[dict]:
    title = (meta.get('title') or '').strip()
    year = meta.get('year')
    if not title:
        return []
    query = f'{title} {year}' if year else title
    # SearchEngineError is deliberately not caught here: an unreachable search
    # engine must not look like a title with no available releases.
    runtime.qb().set_mirror_urls(configured_mirror_urls())
    rows = runtime.qb()._run_search(query)

    candidates = []
    for row in rows:
        quality = detect_quality(row.get('name') or '')
        name = row.get('name') or ''
        lowered_name = name.lower()
        trusted = any(group in lowered_name for group in TRUSTED_RELEASE_GROUPS)
        candidates.append(
            {
                'name': name,
                'size': row.get('size') or '',
                'seeds': row.get('seeds') or '0',
                'leech': row.get('leech') or '0',
                'desc_link': row.get('desc_link') or '',
                'link': row.get('link') or '',
                'pub_date': row.get('pub_date') or '',
                'quality': quality,
                'trusted': trusted,
            }
        )

    def _seed_count(item: dict) -> int:
        try:
            return int(item.get('seeds') or 0)
        except Exception:
            return 0

    preferred_quality = (runtime.store().get_setting('preferred_quality') or '2160p').lower()
    rank_map = {'480p': 1, '720p': 2, '1080p': 3, '1440p': 4, '2160p': 5, '4k': 5}

    def _quality_rank(item: dict) -> int:
        return rank_map.get((item.get('quality') or '').lower(), 0)

    def _meets_preferred(item: dict) -> int:
        return 1 if compare_quality(preferred_quality, item.get('quality')) >= 0 else 0

    def _trusted_rank(item: dict) -> int:
        return 1 if item.get('trusted') else 0

    candidates.sort(
        key=lambda i: (
            _meets_preferred(i),
            _quality_rank(i),
            _trusted_rank(i),
            _seed_count(i),
        ),
        reverse=True,
    )
    return candidates[:limit]
