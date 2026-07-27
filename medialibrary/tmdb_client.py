import json
import urllib.parse
import urllib.request
from typing import ClassVar

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

    def search_precise(
        self, query: str, media_type: str, year: int | None = None, max_results: int = 8
    ) -> list[dict]:
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

    def tv_seasons(self, tv_id: int | str) -> list[dict]:
        """Seasons TMDB lists for a show. Season 0 is the specials season."""
        try:
            data = self._get(f'/tv/{tv_id}', {'language': 'en-US'})
        except Exception:
            return []

        seasons = []
        for entry in data.get('seasons') or []:
            number = entry.get('season_number')
            if number is None:
                continue
            poster_path = entry.get('poster_path')
            seasons.append(
                {
                    'season_number': number,
                    'name': entry.get('name')
                    or ('Specials' if number == 0 else f'Season {number}'),
                    'episode_count': entry.get('episode_count') or 0,
                    'overview': entry.get('overview') or None,
                    'air_date': entry.get('air_date') or None,
                    'poster_url': f'{IMAGE_BASE_POSTER}{poster_path}' if poster_path else None,
                }
            )
        return seasons

    def poster_options(self, tmdb_id: int | str, media_type: str) -> list[dict]:
        """Every poster TMDB holds for a title, best-rated first.

        `include_image_language=null` keeps the language-neutral artwork, which is
        often the cleanest, alongside the localised versions.
        """
        path = f'/tv/{tmdb_id}/images' if media_type == 'tv' else f'/movie/{tmdb_id}/images'
        try:
            data = self._get(path, {'include_image_language': 'en,null'})
        except Exception:
            return []

        posters = []
        for entry in data.get('posters') or []:
            file_path = entry.get('file_path')
            if not file_path:
                continue
            posters.append(
                {
                    'language': entry.get('iso_639_1'),
                    'width': entry.get('width'),
                    'height': entry.get('height'),
                    'vote_average': entry.get('vote_average') or 0,
                    'vote_count': entry.get('vote_count') or 0,
                    'thumb_url': f'{IMAGE_BASE_THUMB}{file_path}',
                    'poster_url': f'{IMAGE_BASE_POSTER}{file_path}',
                }
            )
        posters.sort(key=lambda p: (-p['vote_average'], -p['vote_count']))
        return posters

    def tv_status(self, tv_id: int | str) -> dict:
        """Airing state for a show, and its seasons in one request.

        `status` is TMDB's own wording ("Returning Series", "Ended", "Canceled").
        Seasons come from the same payload so callers do not need a second call.
        """
        try:
            data = self._get(f'/tv/{tv_id}', {'language': 'en-US'})
        except Exception:
            return {'status': None, 'in_production': None, 'next_air_date': None, 'seasons': []}

        upcoming = data.get('next_episode_to_air') or {}
        seasons = []
        for entry in data.get('seasons') or []:
            number = entry.get('season_number')
            if number is None:
                continue
            seasons.append(
                {
                    'season_number': number,
                    'name': entry.get('name')
                    or ('Specials' if number == 0 else f'Season {number}'),
                    'episode_count': entry.get('episode_count') or 0,
                }
            )
        return {
            'status': data.get('status') or None,
            'in_production': bool(data.get('in_production')),
            'next_air_date': upcoming.get('air_date') or None,
            'seasons': seasons,
        }

    def season_episodes(self, tv_id: int | str, season_number: int) -> list[dict]:
        """Episodes for one season, including the synopsis for each."""
        try:
            data = self._get(f'/tv/{tv_id}/season/{season_number}', {'language': 'en-US'})
        except Exception:
            return []

        episodes = []
        for entry in data.get('episodes') or []:
            number = entry.get('episode_number')
            if number is None:
                continue
            still_path = entry.get('still_path')
            raw_rating = entry.get('vote_average')
            episodes.append(
                {
                    'season_number': entry.get('season_number', season_number),
                    'episode_number': number,
                    'title': entry.get('name') or f'Episode {number}',
                    'synopsis': entry.get('overview') or None,
                    'air_date': entry.get('air_date') or None,
                    'runtime': entry.get('runtime') or None,
                    'still_url': f'{IMAGE_BASE_THUMB}{still_path}' if still_path else None,
                    'rating': round(float(raw_rating), 1) if raw_rating else None,
                }
            )
        return episodes

    # TMDB's episode group types. 1 is the default air order, which is already
    # what `season_episodes` returns, so it is not offered as an alternative.
    ORDERING_TYPES: ClassVar[dict[int, str]] = {
        2: 'Absolute',
        3: 'DVD',
        4: 'Digital',
        5: 'Story arc',
        6: 'Production',
        7: 'TV',
    }

    def episode_orderings(self, tv_id: int | str) -> list[dict]:
        """Alternative episode numberings a show has been released in.

        A release is not obliged to number episodes the way TMDB does by
        default. Firefly is the clearest case: shipped on DVD with the
        double-length pilot first, while TMDB lists it in broadcast order with
        that pilot last, so the two disagree about what "S01E01" means.

        Each ordering is returned in the same shape as `season_episodes`, with
        season and episode numbers as *that ordering* counts them, so the two can
        be compared directly.
        """
        try:
            data = self._get(f'/tv/{tv_id}/episode_groups')
        except Exception:
            return []

        orderings = []
        for group in data.get('results') or []:
            kind = self.ORDERING_TYPES.get(group.get('type'))
            if not kind or not group.get('id'):
                continue
            episodes = self._episode_group(group['id'])
            if episodes:
                orderings.append(
                    {
                        'id': group['id'],
                        'name': group.get('name') or kind,
                        'kind': kind,
                        'episodes': episodes,
                    }
                )
        return orderings

    def _episode_group(self, group_id: str) -> list[dict]:
        """Episodes of one ordering, numbered as that ordering numbers them."""
        try:
            data = self._get(f'/tv/episode_group/{group_id}')
        except Exception:
            return []

        episodes = []
        for group in data.get('groups') or []:
            season_number = group.get('order')
            if season_number is None:
                continue
            for position, entry in enumerate(group.get('episodes') or []):
                # `order` is the episode's zero-based place in this ordering;
                # the season/episode numbers on the entry are its air-order
                # identity, which is not what this ordering means by them.
                order = entry.get('order')
                number = (order + 1) if order is not None else (position + 1)
                still_path = entry.get('still_path')
                raw_rating = entry.get('vote_average')
                episodes.append(
                    {
                        'season_number': int(season_number),
                        'episode_number': number,
                        'title': entry.get('name') or f'Episode {number}',
                        'synopsis': entry.get('overview') or None,
                        'air_date': entry.get('air_date') or None,
                        'runtime': entry.get('runtime') or None,
                        'still_url': f'{IMAGE_BASE_THUMB}{still_path}' if still_path else None,
                        'rating': round(float(raw_rating), 1) if raw_rating else None,
                    }
                )
        return episodes

    def metadata_by_tmdb_id(self, tmdb_id: int | str, media_type: str) -> dict:
        """Full metadata for a known TMDB ID.

        Returns the same dict shape as the other metadata lookups.
        """
        path = f'/tv/{tmdb_id}' if media_type == 'tv' else f'/movie/{tmdb_id}'

        try:
            data = self._get(
                path, {'append_to_response': 'credits,external_ids', 'language': 'en-US'}
            )
        except Exception:
            return {
                'imdb_id': None,
                'tmdb_id': int(tmdb_id) if str(tmdb_id).isdigit() else None,
                'title': str(tmdb_id),
                'year': None,
                'media_type': media_type,
                'poster_url': None,
                'synopsis': None,
                'actors': None,
                'genre_1': None,
                'genre_2': None,
                'collection_id': None,
                'collection_name': None,
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

        genres = data.get('genres') or []
        genre_names = [g.get('name') for g in genres if isinstance(g, dict) and g.get('name')]
        genre_1 = genre_names[0] if len(genre_names) >= 1 else None
        genre_2 = genre_names[1] if len(genre_names) >= 2 else None

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
            'genre_1': genre_1,
            'genre_2': genre_2,
            'rating': rating,
            'collection_id': collection.get('id'),
            'collection_name': collection.get('name'),
        }

    def metadata_by_imdb_id(self, imdb_id: str) -> dict:
        """Look up a title by IMDb ID and return full metadata. Used for Refresh flow."""
        stub = {
            'imdb_id': imdb_id,
            'tmdb_id': None,
            'title': imdb_id,
            'year': None,
            'media_type': 'movie',
            'poster_url': None,
            'synopsis': None,
            'actors': None,
            'genre_1': None,
            'genre_2': None,
            'collection_id': None,
            'collection_name': None,
        }
        try:
            data = self._get(
                f'/find/{imdb_id}', {'external_source': 'imdb_id', 'language': 'en-US'}
            )
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
