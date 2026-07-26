import json
import urllib.error
import urllib.parse
import urllib.request

TRAKT_API_BASE = 'https://api.trakt.tv'


class TraktRequestError(Exception):
    def __init__(self, code: str, status_code: int | None = None,
                 payload: dict | list | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.payload = payload


class TraktClient:
    def __init__(
        self,
        client_id: str,
        username: str = '',
        client_secret: str = '',
        access_token: str = '',
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.username = urllib.parse.quote(username, safe='')

    def _headers(self, include_auth: bool = True) -> dict[str, str]:
        headers = {
            'Content-Type': 'application/json',
            'trakt-api-version': '2',
            'trakt-api-key': self.client_id,
            'User-Agent': 'MediaLibrary/1.0',
        }
        if include_auth and self.access_token:
            headers['Authorization'] = f'Bearer {self.access_token}'
        return headers

    def _request(self, method: str, path: str, payload: dict | None = None,
                 include_auth: bool = True):
        url = TRAKT_API_BASE + path
        body = None
        if payload is not None:
            body = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=body, method=method,
                                     headers=self._headers(include_auth=include_auth))
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode('utf-8')
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            payload = None
            try:
                raw = exc.read().decode('utf-8')
                payload = json.loads(raw) if raw else None
            except Exception:
                payload = None
            if exc.code == 401:
                raise TraktRequestError('unauthorized', status_code=exc.code,
                                        payload=payload) from exc
            if exc.code == 403:
                raise TraktRequestError('forbidden', status_code=exc.code, payload=payload) from exc
            if exc.code == 404:
                raise TraktRequestError('not_found', status_code=exc.code, payload=payload) from exc
            if exc.code == 410:
                raise TraktRequestError('deactivated', status_code=exc.code,
                                        payload=payload) from exc
            if exc.code == 423:
                raise TraktRequestError('locked', status_code=exc.code, payload=payload) from exc
            if exc.code == 429:
                raise TraktRequestError('rate_limited', status_code=exc.code,
                                        payload=payload) from exc
            raise TraktRequestError('http_error', status_code=exc.code, payload=payload) from exc
        except urllib.error.URLError as exc:
            raise TraktRequestError('network_error') from exc

    def _get(self, path: str):
        return self._request('GET', path)

    def _post(self, path: str, payload: dict, include_auth: bool = True):
        return self._request('POST', path, payload=payload, include_auth=include_auth)

    def _user_slug(self) -> str:
        if self.access_token:
            return 'me'
        return self.username

    def current_user(self) -> dict:
        data = self._get('/users/me')
        return data or {}

    def device_code(self) -> dict:
        return self._post('/oauth/device/code', {'client_id': self.client_id},
                          include_auth=False) or {}

    def poll_device_token(self, device_code: str) -> dict:
        try:
            return self._post(
                '/oauth/device/token',
                {
                    'code': device_code,
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                },
                include_auth=False,
            ) or {}
        except TraktRequestError as exc:
            code_map = {
                400: 'pending',
                404: 'not_found',
                409: 'already_used',
                410: 'expired',
                418: 'denied',
                429: 'slow_down',
            }
            if exc.status_code in code_map:
                raise TraktRequestError(code_map[exc.status_code],
                                        status_code=exc.status_code,
                                        payload=exc.payload) from exc
            raise

    def exchange_refresh_token(self, refresh_token: str) -> dict:
        return self._post(
            '/oauth/token',
            {
                'refresh_token': refresh_token,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'refresh_token',
            },
            include_auth=False,
        ) or {}

    def revoke_token(self, token: str) -> None:
        self._post(
            '/oauth/revoke',
            {
                'token': token,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
            },
            include_auth=False,
        )

    def _movie_item(self, movie: dict) -> dict | None:
        imdb_id = movie.get('ids', {}).get('imdb')
        if not imdb_id:
            return None
        return {
            'imdb_id': imdb_id,
            'title': movie.get('title', ''),
            'year': movie.get('year'),
            'media_type': 'movie',
        }

    def _show_item(self, show: dict) -> dict | None:
        imdb_id = show.get('ids', {}).get('imdb')
        if not imdb_id:
            return None
        return {
            'imdb_id': imdb_id,
            'title': show.get('title', ''),
            'year': show.get('year'),
            'media_type': 'tv',
        }

    def collection_movies(self) -> list[dict]:
        path = ('/sync/collection/movies' if self.access_token
                else f'/users/{self._user_slug()}/collection/movies')
        data = self._get(path)
        out = []
        for item in data:
            parsed = self._movie_item(item.get('movie', {}))
            if parsed:
                out.append(parsed)
        return out

    def collection_shows(self) -> list[dict]:
        path = ('/sync/collection/shows' if self.access_token
                else f'/users/{self._user_slug()}/collection/shows')
        data = self._get(path)
        out = []
        for item in data:
            parsed = self._show_item(item.get('show', {}))
            if parsed:
                out.append(parsed)
        return out

    def watchlist_movies(self) -> list[dict]:
        path = ('/sync/watchlist/movies' if self.access_token
                else f'/users/{self._user_slug()}/watchlist/movies')
        data = self._get(path)
        out = []
        for item in data:
            parsed = self._movie_item(item.get('movie', {}))
            if parsed:
                out.append(parsed)
        return out

    def watchlist_shows(self) -> list[dict]:
        path = ('/sync/watchlist/shows' if self.access_token
                else f'/users/{self._user_slug()}/watchlist/shows')
        data = self._get(path)
        out = []
        for item in data:
            parsed = self._show_item(item.get('show', {}))
            if parsed:
                out.append(parsed)
        return out
