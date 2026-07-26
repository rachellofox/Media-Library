import os
import re
import subprocess
import urllib.request

from quality import compare_quality, detect_quality

DEFAULT_MIRROR_URLS = [
    'https://kickasstorrents.bz',
    'https://kickasstorrents.to',
    'https://kickasstorrents.cr',
]


class SearchEngineError(RuntimeError):
    """The search engine could not be reached or produced nothing usable.

    Distinct from an empty result set: nova2 exits 0 even when every mirror
    fails, so without this the caller cannot tell "no matches for this title"
    apart from "the search never actually ran".
    """


# nova2 writes failures to stderr and still exits 0, so the text is the only
# signal available. Ordered most specific first.
# The mirrors sit behind Cloudflare, which refuses a default urllib agent.
_BROWSER_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0'
)

_FAILURE_SIGNATURES = [
    (
        'wrong_version_number',
        'the mirrors refused a secure connection, which usually means an ISP block '
        'or an interception page',
    ),
    (
        'certificate_verify_failed',
        'the mirrors presented an invalid certificate, which usually means an interception page',
    ),
    (
        'name or service not known',
        'the mirror hostnames could not be resolved (DNS failure or block)',
    ),
    ('getaddrinfo failed', 'the mirror hostnames could not be resolved (DNS failure or block)'),
    ('connection error', 'no mirror could be reached'),
    ('timed out', 'the mirrors did not respond in time'),
    ('empty response', 'the mirrors returned nothing'),
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
        replacement = f'MIRROR_URLS = [\n    {urls_literal},\n]'

        with open(engine_file, encoding='utf-8', errors='replace') as fh:
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
            text=False,
            timeout=120,
        )

        stdout = (result.stdout or b'').decode('utf-8', errors='replace')
        stderr = (result.stderr or b'').decode('utf-8', errors='replace')

        if result.returncode != 0 and not stdout.strip():
            raise SearchEngineError(self._describe_failure(stderr) or 'Search command failed')

        rows = []
        for line in stdout.splitlines():
            parts = line.split('|')
            if len(parts) < 8:
                continue
            rows.append(
                {
                    'link': parts[0],
                    'name': parts[1],
                    'size': parts[2],
                    'seeds': parts[3],
                    'leech': parts[4],
                    'engine_url': parts[5],
                    'desc_link': parts[6],
                    'pub_date': parts[7],
                }
            )

        # nova2 exits 0 whether it found nothing or never reached a mirror, and
        # its stderr carries failures only from the mirrors that broke — a mirror
        # that answered with no matches says nothing at all. So a zero-result run
        # with some complaints is ambiguous, and the mirrors are asked directly
        # rather than telling the user their engine is down when the release
        # simply does not exist.
        if not rows:
            described = self._describe_failure(stderr)
            if described and not self._any_mirror_reachable():
                raise SearchEngineError(described)
        return rows

    def _any_mirror_reachable(self) -> bool:
        """Whether any configured mirror answers an HTTP request right now."""
        for url in self.mirror_urls:
            try:
                request = urllib.request.Request(url, headers={'User-Agent': _BROWSER_USER_AGENT})
                with urllib.request.urlopen(request, timeout=8) as response:
                    if 200 <= getattr(response, 'status', 200) < 400:
                        return True
            except Exception:
                continue
        return False

    def _describe_failure(self, stderr: str) -> str | None:
        """Turn nova2's stderr into a short cause, or None if it looks healthy."""
        text = (stderr or '').strip()
        if not text:
            return None

        lowered = text.lower()
        for signature, explanation in _FAILURE_SIGNATURES:
            if signature in lowered:
                return f'{self.engine_name}: {explanation}.'

        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), '')
        return f'{self.engine_name}: {first_line}' if first_line else None

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
