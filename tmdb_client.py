import json
import urllib.parse
import urllib.request

TMDB_API_BASE = 'https://api.themoviedb.org/3'
IMAGE_BASE_THUMB = 'https://image.tmdb.org/t/p/w342'
IMAGE_BASE_POSTER = 'https://image.tmdb.org/t/p/w500'


class TmdbClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError('TMDB_API_KEY is not set')
        self._api_key = api_key

    def _get(self, path: str, params: dict | None = None) -> dict:
        query = {'api_key': self._api_key}
        if params:
            query.update(params)
        url = f'{TMDB_API_BASE}{path}?{urllib.parse.urlencode(query)}'
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def ping(self) -> dict:
        return self._get('/configuration')

    def _media_summary(self, item: dict, media_type: str | None = None) -> dict | None:
        resolved_type = media_type or item.get('media_type')
        if resolved_type not in ('movie', 'tv'):
            return None

        tmdb_id = item.get('id')
        if not tmdb_id:
            return None

        if resolved_type == 'movie':
            title = item.get('title') or item.get('original_title')
            raw_date = item.get('release_date', '')
        else:
            title = item.get('name') or item.get('original_name')
            raw_date = item.get('first_air_date', '')

        if not title:
            return None

        year = None
        if isinstance(raw_date, str) and len(raw_date) >= 4:
            try:
                year = int(raw_date[:4])
            except ValueError:
                pass

        poster_path = item.get('poster_path')
        thumbnail = f'{IMAGE_BASE_THUMB}{poster_path}' if poster_path else None
        poster_url = f'{IMAGE_BASE_POSTER}{poster_path}' if poster_path else None

        return {
            'tmdb_id': tmdb_id,
            'title': title,
            'year': year,
            'release_date': raw_date or None,
            'media_type': resolved_type,
            'thumbnail': thumbnail,
            'poster_url': poster_url,
        }

    def search(self, query: str, max_results: int = 8) -> list[dict]:
        """Search movies and TV shows. Returns list of result dicts with tmdb_id."""
        safe = query.strip()
        if not safe:
            return []
        try:
            data = self._get('/search/multi', {'query': safe, 'include_adult': 'false'})
        except Exception:
            return []

        out = []
        for item in data.get('results', []):
            parsed = self._media_summary(item)
            if not parsed:
                continue
            out.append(parsed)
            if len(out) >= max_results:
                break

        return out

    def search_precise(self, query: str, media_type: str, year: int | None = None, max_results: int = 8) -> list[dict]:
        """Search a specific media type with optional year filtering."""
        safe = query.strip()
        if not safe or media_type not in ('movie', 'tv'):
            return []

        params = {'query': safe, 'include_adult': 'false'}
        if year:
            if media_type == 'movie':
                params['year'] = str(year)
            else:
                params['first_air_date_year'] = str(year)

        path = '/search/movie' if media_type == 'movie' else '/search/tv'
        try:
            data = self._get(path, params)
        except Exception:
            return []

        out = []
        for item in data.get('results', []):
            parsed = self._media_summary(item, media_type=media_type)
            if not parsed:
                continue
            out.append(parsed)
            if len(out) >= max_results:
                break

        return out

    def trending(self, max_results: int = 12) -> list[dict]:
        try:
            data = self._get('/trending/all/week', {'language': 'en-US'})
        except Exception:
            return []

        out = []
        for item in data.get('results', []):
            parsed = self._media_summary(item)
            if not parsed:
                continue
            out.append(parsed)
            if len(out) >= max_results:
                break

        return out

    def collection_parts(self, collection_id: int | str) -> list[dict]:
        try:
            data = self._get(f'/collection/{collection_id}', {'language': 'en-US'})
        except Exception:
            return []

        out = []
        for item in data.get('parts', []):
            parsed = self._media_summary(item, media_type='movie')
            if parsed:
                out.append(parsed)
        return out

    def metadata_by_tmdb_id(self, tmdb_id: int | str, media_type: str) -> dict:
        """Fetch full metadata for a known TMDB ID. Returns dict matching IMDbClient.metadata() contract."""
        if media_type == 'tv':
            path = f'/tv/{tmdb_id}'
        else:
            path = f'/movie/{tmdb_id}'

        try:
            data = self._get(path, {'append_to_response': 'credits,external_ids', 'language': 'en-US'})
        except Exception:
            return {
                'imdb_id': None, 'tmdb_id': int(tmdb_id) if str(tmdb_id).isdigit() else None,
                'title': str(tmdb_id), 'year': None,
                'media_type': media_type, 'poster_url': None,
                'synopsis': None, 'actors': None,
                'collection_id': None, 'collection_name': None,
            }

        external_ids = data.get('external_ids', {})
        imdb_id = external_ids.get('imdb_id') or None

        if media_type == 'movie':
            title = data.get('title') or data.get('original_title') or str(tmdb_id)
            raw_date = data.get('release_date', '')
        else:
            title = data.get('name') or data.get('original_name') or str(tmdb_id)
            raw_date = data.get('first_air_date', '')

        year = None
        if isinstance(raw_date, str) and len(raw_date) >= 4:
            try:
                year = int(raw_date[:4])
            except ValueError:
                pass

        poster_path = data.get('poster_path')
        poster_url = f'{IMAGE_BASE_POSTER}{poster_path}' if poster_path else None

        synopsis = data.get('overview') or None

        cast = data.get('credits', {}).get('cast', [])
        actors = ', '.join(m.get('name', '') for m in cast[:5] if m.get('name')) or None

        raw_rating = data.get('vote_average')
        rating = round(float(raw_rating), 1) if raw_rating else None
        collection = data.get('belongs_to_collection') or {}

        return {
            'imdb_id': imdb_id,
            'tmdb_id': data.get('id'),
            'title': title,
            'year': year,
            'media_type': media_type,
            'poster_url': poster_url,
            'synopsis': synopsis,
            'actors': actors,
            'rating': rating,
            'collection_id': collection.get('id'),
            'collection_name': collection.get('name'),
        }

    def metadata_by_imdb_id(self, imdb_id: str) -> dict:
        """Look up a title by IMDb ID and return full metadata. Used for Refresh flow."""
        stub = {
            'imdb_id': imdb_id, 'tmdb_id': None, 'title': imdb_id, 'year': None,
            'media_type': 'movie', 'poster_url': None,
            'synopsis': None, 'actors': None,
            'collection_id': None, 'collection_name': None,
        }
        try:
            data = self._get(f'/find/{imdb_id}', {'external_source': 'imdb_id', 'language': 'en-US'})
        except Exception:
            return stub

        movie_results = data.get('movie_results', [])
        tv_results = data.get('tv_results', [])
        episode_results = data.get('tv_episode_results', [])

        if movie_results:
            result = movie_results[0]
            media_type = 'movie'
            tmdb_id = result.get('id')
        elif tv_results:
            result = tv_results[0]
            media_type = 'tv'
            tmdb_id = result.get('id')
        elif episode_results:
            # IMDb ID is for an individual episode — promote to the parent series
            result = episode_results[0]
            media_type = 'tv'
            tmdb_id = result.get('show_id')
        else:
            return stub

        if not tmdb_id:
            return stub

        return self.metadata_by_tmdb_id(tmdb_id, media_type)
