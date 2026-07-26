import json
import re
import urllib.parse
import urllib.request

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/123 Safari/537.36')


class IMDbClient:
    def _fetch_text(self, url: str) -> str:
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode('utf-8', errors='replace')

    def search(self, query: str, max_results: int = 8):
        safe = query.strip()
        if not safe:
            return []

        encoded = urllib.parse.quote(safe)
        first = safe[0].lower()
        url = f'https://v2.sg.media-imdb.com/suggestion/{first}/{encoded}.json'

        try:
            data = self._fetch_text(url)
            parsed = json.loads(data)
        except Exception:
            return []

        out = []
        for item in parsed.get('d', [])[:max_results]:
            imdb_id = item.get('id')
            title = item.get('l')
            year = item.get('y')
            kind = item.get('qid')
            if not imdb_id or not title:
                continue
            media_type = 'tv' if str(kind).lower().startswith('tv') else 'movie'
            i_field = item.get('i', {})
            thumbnail = i_field.get('imageUrl') if isinstance(i_field, dict) else None
            out.append({
                'imdb_id': imdb_id,
                'title': title,
                'year': year,
                'media_type': media_type,
                'thumbnail': thumbnail,
            })
        return out

    def metadata(self, imdb_id: str):
        url = f'https://www.imdb.com/title/{imdb_id}/'
        html = self._fetch_text(url)

        # Pull JSON-LD block for stable metadata extraction.
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        if not m:
            return {
                'imdb_id': imdb_id, 'title': imdb_id, 'year': None, 'media_type': 'movie',
                'poster_url': None, 'synopsis': None, 'actors': None,
            }

        payload = json.loads(m.group(1))
        raw_type = str(payload.get('@type', '')).lower()

        year = None
        date_published = payload.get('datePublished')
        if isinstance(date_published, str) and len(date_published) >= 4:
            try:
                year = int(date_published[:4])
            except ValueError:
                year = None

        # Actors from JSON-LD actor array
        actor_list = payload.get('actor', [])
        if isinstance(actor_list, dict):
            actor_list = [actor_list]
        actors = ', '.join(a.get('name', '') for a in actor_list if a.get('name')) or None

        return {
            'imdb_id': imdb_id,
            'title': payload.get('name', imdb_id),
            'year': year,
            'media_type': 'tv' if 'tv' in raw_type else 'movie',
            'poster_url': payload.get('image') or None,
            'synopsis': payload.get('description') or None,
            'actors': actors,
        }
