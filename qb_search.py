import os
import re
import subprocess

from quality import detect_quality, compare_quality


DEFAULT_MIRROR_URLS = [
    'https://kickasstorrents.bz',
    'https://kickasstorrents.to',
    'https://kickasstorrents.cr',
]


class QBSearch:
    def __init__(self, nova_path: str, engine_name: str = 'kickasstorrents') -> None:
        self.nova_path = nova_path
        self.engine_name = engine_name
        self.mirror_urls = list(DEFAULT_MIRROR_URLS)

    def set_mirror_urls(self, mirror_urls: list[str] | tuple[str, ...] | None) -> None:
        cleaned = []
        seen = set()
        for value in mirror_urls or []:
            url = (value or '').strip().rstrip('/')
            if not url:
                continue
            if not re.match(r'^https?://', url, re.IGNORECASE):
                continue
            lowered = url.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            cleaned.append(url)
        self.mirror_urls = cleaned or list(DEFAULT_MIRROR_URLS)

    def _engine_file_path(self) -> str:
        return os.path.join(self.nova_path, 'engines', f'{self.engine_name}.py')

    def _sync_mirror_urls(self) -> None:
        engine_file = self._engine_file_path()
        if not os.path.exists(engine_file):
            return

        urls_literal = ',\n    '.join(repr(url) for url in self.mirror_urls)
        replacement = 'MIRROR_URLS = [\n    {urls},\n]'.format(urls=urls_literal)

        with open(engine_file, 'r', encoding='utf-8', errors='replace') as fh:
            content = fh.read()

        updated, count = re.subn(
            r'MIRROR_URLS = \[.*?\]',
            replacement,
            content,
            count=1,
            flags=re.DOTALL,
        )
        if count and updated != content:
            with open(engine_file, 'w', encoding='utf-8', errors='replace') as fh:
                fh.write(updated)

    def _run_search(self, query: str):
        self._sync_mirror_urls()

        # qBittorrent nova2 expects query tokens joined with +
        query = query.replace(' ', '+').strip('+')
        command = ['python', 'nova2.py', self.engine_name, 'all', query]

        result = subprocess.run(
            command,
            cwd=self.nova_path,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0 and not result.stdout.strip():
            raise RuntimeError(result.stderr.strip() or 'Search command failed')

        rows = []
        for line in result.stdout.splitlines():
            parts = line.split('|')
            if len(parts) < 8:
                continue
            rows.append({
                'link': parts[0],
                'name': parts[1],
                'size': parts[2],
                'seeds': parts[3],
                'leech': parts[4],
                'engine_url': parts[5],
                'desc_link': parts[6],
                'pub_date': parts[7],
            })
        return rows

    def check_for_higher_quality(
        self,
        title: str,
        year: int | None,
        current_quality: str | None,
        preferred_quality: str | None = None,
    ):
        query = f'{title} {year}' if year else title
        rows = self._run_search(query)

        best = None
        for row in rows:
            candidate_quality = detect_quality(row['name'])
            # Must be strictly higher than current.
            if compare_quality(current_quality, candidate_quality) <= 0:
                continue
            # If a preferred target is set, candidate must also meet it.
            if preferred_quality and compare_quality(preferred_quality, candidate_quality) > 0:
                continue

            if best is None:
                best = {**row, 'quality': candidate_quality}
                continue

            # Prefer higher quality, then seeds.
            best_cmp = compare_quality(best.get('quality'), candidate_quality)
            if best_cmp < 0:
                best = {**row, 'quality': candidate_quality}
                continue

            if best_cmp == 0:
                try:
                    if int(row['seeds']) > int(best['seeds']):
                        best = {**row, 'quality': candidate_quality}
                except ValueError:
                    pass

        return {
            'found': best is not None,
            'best': best,
            'result_count': len(rows),
        }
