import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS media_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imdb_id TEXT UNIQUE NOT NULL,
    tmdb_id INTEGER,
    title TEXT NOT NULL,
    year INTEGER,
    media_type TEXT NOT NULL,
    collection_id INTEGER,
    collection_name TEXT,
    current_quality TEXT,
    path TEXT,
    poster_url TEXT,
    synopsis TEXT,
    actors TEXT,
    genre_1 TEXT,
    genre_2 TEXT,
    rating REAL,
    subtitles TEXT,
    favourite INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS quality_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_item_id INTEGER NOT NULL,
    checked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    best_found_quality TEXT,
    best_found_name TEXT,
    best_found_desc_link TEXT,
    found INTEGER NOT NULL DEFAULT 0,
    raw_result_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(media_item_id) REFERENCES media_items(id)
);

CREATE TABLE IF NOT EXISTS download_states (
    media_item_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    source TEXT,
    message TEXT,
    torrent_hash TEXT,
    mode TEXT,
    previous_path TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(media_item_id) REFERENCES media_items(id)
);

CREATE TABLE IF NOT EXISTS discover_ignored_titles (
    tmdb_id INTEGER PRIMARY KEY,
    collection_id INTEGER,
    title TEXT,
    collection_name TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discover_ignored_collections (
    collection_id INTEGER PRIMARY KEY,
    collection_name TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tmdb_season_cache (
    tmdb_id INTEGER NOT NULL,
    season_number INTEGER NOT NULL,
    episodes_json TEXT NOT NULL,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(tmdb_id, season_number)
);

CREATE TABLE IF NOT EXISTS tmdb_tv_status_cache (
    tmdb_id INTEGER PRIMARY KEY,
    status_json TEXT NOT NULL,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discover_ignored_tv (
    tmdb_id INTEGER NOT NULL,
    season_number INTEGER NOT NULL,
    title TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(tmdb_id, season_number)
);

CREATE TABLE IF NOT EXISTS tmdb_collection_parts_cache (
    collection_id INTEGER NOT NULL,
    tmdb_id INTEGER NOT NULL,
    title TEXT,
    year INTEGER,
    poster_url TEXT,
    release_date TEXT,
    media_type TEXT,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(collection_id, tmdb_id)
);

CREATE TABLE IF NOT EXISTS discover_watchlist_cache (
    imdb_id TEXT PRIMARY KEY,
    tmdb_id INTEGER,
    title TEXT,
    year INTEGER,
    media_type TEXT,
    poster_url TEXT,
    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- F-0208.02: a first-party "want to watch" list, not synced from anywhere —
-- added and removed directly, unlike discover_watchlist_cache above which is
-- only ever a cache of what Trakt already says. Keyed on (tmdb_id,
-- media_type) rather than imdb_id since that is what the Discover hero
-- already identifies a title by, before an imdb_id is necessarily known.
CREATE TABLE IF NOT EXISTS watchlist_items (
    tmdb_id INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    imdb_id TEXT,
    title TEXT,
    year INTEGER,
    poster_url TEXT,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tmdb_id, media_type)
);

CREATE TABLE IF NOT EXISTS playback_positions (
    media_item_id INTEGER NOT NULL,
    episode_key TEXT NOT NULL DEFAULT '',
    position_seconds REAL NOT NULL,
    duration_seconds REAL,
    watched INTEGER NOT NULL DEFAULT 0,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (media_item_id, episode_key),
    FOREIGN KEY(media_item_id) REFERENCES media_items(id)
);

-- One row per show once its files have been checked. ordering_id is NULL when
-- checking confirmed TMDB's default numbering was already right — that still
-- has to be recorded, or the next startup probes every file again. It is also
-- NULL when nothing published fits (untitled_seasons then names which seasons
-- to withhold titles for); it is set only when a specific published ordering
-- was adopted.
CREATE TABLE IF NOT EXISTS tv_episode_orderings (
    media_item_id INTEGER PRIMARY KEY,
    ordering_id TEXT,
    ordering_name TEXT,
    ordering_kind TEXT,
    untitled_seasons TEXT NOT NULL DEFAULT '',
    detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(media_item_id) REFERENCES media_items(id)
);
"""


class Storage:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @contextmanager
    def conn(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _migrate_playback_positions_to_per_episode(self) -> None:
        """Widen playback_positions from one row per item to one row per episode.

        The primary key changes from (media_item_id) to (media_item_id,
        episode_key), which SQLite cannot ALTER — the table has to be rebuilt.
        A movie's existing position carries over as episode_key='', which is
        also what a movie writes going forward, so nothing is lost for movies.
        A show's saved position cannot be attributed to a specific episode after
        the fact, so it is dropped rather than guessed onto one at random.
        """
        with self.conn() as c:
            columns = {row['name'] for row in c.execute('PRAGMA table_info(playback_positions)')}
            if 'episode_key' in columns:
                return  # already migrated
            c.executescript(
                """
                ALTER TABLE playback_positions RENAME TO playback_positions_old;
                CREATE TABLE playback_positions (
                    media_item_id INTEGER NOT NULL,
                    episode_key TEXT NOT NULL DEFAULT '',
                    position_seconds REAL NOT NULL,
                    duration_seconds REAL,
                    watched INTEGER NOT NULL DEFAULT 0,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (media_item_id, episode_key),
                    FOREIGN KEY(media_item_id) REFERENCES media_items(id)
                );
                INSERT INTO playback_positions
                    (media_item_id, episode_key, position_seconds, duration_seconds, last_updated)
                SELECT media_item_id, '', position_seconds, duration_seconds, last_updated
                FROM playback_positions_old;
                DROP TABLE playback_positions_old;
                """
            )

    def initialize(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA_SQL)
        self._migrate_playback_positions_to_per_episode()
        # Migrate columns added after initial release.
        with self.conn() as c:
            for col, col_type in [
                ('tmdb_id', 'INTEGER'),
                ('poster_url', 'TEXT'),
                ('synopsis', 'TEXT'),
                ('actors', 'TEXT'),
                ('genre_1', 'TEXT'),
                ('genre_2', 'TEXT'),
                ('rating', 'REAL'),
                ('subtitles', 'TEXT'),
                ('favourite', 'INTEGER NOT NULL DEFAULT 0'),
                ('collection_id', 'INTEGER'),
                ('collection_name', 'TEXT'),
            ]:
                try:
                    c.execute(f'ALTER TABLE media_items ADD COLUMN {col} {col_type}')
                except Exception:
                    pass
            for table_name, col, col_type in [
                ('discover_ignored_titles', 'title', 'TEXT'),
                ('discover_ignored_titles', 'collection_name', 'TEXT'),
                ('discover_ignored_collections', 'collection_name', 'TEXT'),
                ('download_states', 'torrent_hash', 'TEXT'),
                ('download_states', 'mode', 'TEXT'),
                ('download_states', 'previous_path', 'TEXT'),
            ]:
                try:
                    c.execute(f'ALTER TABLE {table_name} ADD COLUMN {col} {col_type}')
                except Exception:
                    pass

    def add_media_item(
        self,
        imdb_id: str,
        tmdb_id: int | None,
        title: str,
        year: int | None,
        media_type: str,
        collection_id: int | None,
        collection_name: str | None,
        current_quality: str | None,
        path: str | None,
        poster_url: str | None = None,
        synopsis: str | None = None,
        actors: str | None = None,
        genre_1: str | None = None,
        genre_2: str | None = None,
        rating: float | None = None,
        subtitles: str | None = None,
    ) -> None:
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO media_items
                    (
                        imdb_id, tmdb_id, title, year, media_type, collection_id,
                        collection_name, current_quality, path, poster_url,
                        synopsis, actors, genre_1, genre_2, rating, subtitles
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(imdb_id) DO UPDATE SET
                    tmdb_id=COALESCE(excluded.tmdb_id, media_items.tmdb_id),
                    title=excluded.title,
                    year=excluded.year,
                    media_type=excluded.media_type,
                    collection_id=COALESCE(excluded.collection_id, media_items.collection_id),
                    collection_name=COALESCE(excluded.collection_name, media_items.collection_name),
                    current_quality=COALESCE(excluded.current_quality, media_items.current_quality),
                    path=COALESCE(excluded.path, media_items.path),
                    poster_url=COALESCE(excluded.poster_url, media_items.poster_url),
                    synopsis=COALESCE(excluded.synopsis, media_items.synopsis),
                    actors=COALESCE(excluded.actors, media_items.actors),
                    genre_1=COALESCE(excluded.genre_1, media_items.genre_1),
                    genre_2=COALESCE(excluded.genre_2, media_items.genre_2),
                    rating=COALESCE(excluded.rating, media_items.rating),
                    subtitles=COALESCE(excluded.subtitles, media_items.subtitles)
                """,
                (
                    imdb_id,
                    tmdb_id,
                    title,
                    year,
                    media_type,
                    collection_id,
                    collection_name,
                    current_quality,
                    path,
                    poster_url,
                    synopsis,
                    actors,
                    genre_1,
                    genre_2,
                    rating,
                    subtitles,
                ),
            )

    def update_metadata(
        self,
        media_id: int,
        imdb_id: str | None,
        tmdb_id: int | None,
        poster_url: str | None,
        synopsis: str | None,
        actors: str | None,
        genre_1: str | None = None,
        genre_2: str | None = None,
        rating: float | None = None,
        title: str | None = None,
        media_type: str | None = None,
        year: int | None = None,
        collection_id: int | None = None,
        collection_name: str | None = None,
    ) -> None:
        with self.conn() as c:
            effective_imdb_id = imdb_id
            if effective_imdb_id:
                existing = c.execute(
                    'SELECT id FROM media_items WHERE imdb_id = ?',
                    (effective_imdb_id,),
                ).fetchone()
                if existing and int(existing['id']) != int(media_id):
                    effective_imdb_id = None
            c.execute(
                """UPDATE media_items SET
                    imdb_id=COALESCE(?, imdb_id),
                    tmdb_id=COALESCE(?, tmdb_id),
                    poster_url=?,
                    synopsis=?,
                    actors=?,
                    genre_1=COALESCE(?, genre_1),
                    genre_2=COALESCE(?, genre_2),
                    rating=COALESCE(?, rating),
                    title=COALESCE(?, title),
                    media_type=COALESCE(?, media_type),
                    year=COALESCE(?, year),
                    collection_id=COALESCE(?, collection_id),
                    collection_name=COALESCE(?, collection_name)
                WHERE id=?""",
                (
                    effective_imdb_id,
                    tmdb_id,
                    poster_url,
                    synopsis,
                    actors,
                    genre_1,
                    genre_2,
                    rating,
                    title,
                    media_type,
                    year,
                    collection_id,
                    collection_name,
                    media_id,
                ),
            )

    def update_path(self, media_id: int, path: str) -> None:
        with self.conn() as c:
            c.execute('UPDATE media_items SET path=? WHERE id=?', (path, media_id))

    def update_poster(self, media_id: int, poster_url: str | None) -> None:
        with self.conn() as c:
            c.execute('UPDATE media_items SET poster_url=? WHERE id=?', (poster_url, media_id))

    def update_subtitles(self, media_id: int, subtitles: str | None) -> None:
        with self.conn() as c:
            c.execute('UPDATE media_items SET subtitles=? WHERE id=?', (subtitles, media_id))

    def update_quality(self, media_id: int, quality: str | None) -> None:
        with self.conn() as c:
            c.execute('UPDATE media_items SET current_quality=? WHERE id=?', (quality, media_id))

    def toggle_favourite(self, media_id: int) -> int:
        with self.conn() as c:
            c.execute(
                'UPDATE media_items SET favourite = 1 - favourite WHERE id=?',
                (media_id,),
            )
            row = c.execute('SELECT favourite FROM media_items WHERE id=?', (media_id,)).fetchone()
        return row['favourite'] if row else 0

    def list_media_items(self):
        with self.conn() as c:
            return c.execute(
                """
                  SELECT m.*, q.checked_at, q.best_found_quality, q.best_found_name,
                      q.best_found_desc_link, q.found,
                      d.status AS download_status,
                      d.source AS download_source,
                      d.message AS download_message,
                      d.torrent_hash AS download_torrent_hash,
                      d.mode AS download_mode,
                      d.previous_path AS download_previous_path,
                      d.updated_at AS download_updated_at
                FROM media_items m
                LEFT JOIN quality_checks q ON q.id = (
                    SELECT id FROM quality_checks
                    WHERE media_item_id = m.id
                    ORDER BY checked_at DESC
                    LIMIT 1
                )
                LEFT JOIN download_states d ON d.media_item_id = m.id
                ORDER BY
                    LOWER(
                        CASE
                            WHEN m.title LIKE 'The %' THEN SUBSTR(m.title, 5)
                            WHEN m.title LIKE 'An %' THEN SUBSTR(m.title, 4)
                            WHEN m.title LIKE 'A %' THEN SUBSTR(m.title, 3)
                            ELSE COALESCE(m.title, '')
                        END
                    ) COLLATE NOCASE,
                    COALESCE(m.year, 0),
                    m.id
                """
            ).fetchall()

    def set_download_state(
        self,
        media_item_id: int,
        status: str,
        source: str | None = None,
        message: str | None = None,
        torrent_hash: str | None = None,
        mode: str | None = None,
        previous_path: str | None = None,
    ) -> None:
        """Record download progress.

        `mode` is 'upgrade' when the item already has a playable file that the
        download is meant to replace, otherwise 'fill'. `previous_path` is the
        path captured at submit time so finalisation knows what to retire even
        if the library entry moves in the meantime. Both are preserved across
        status updates so only the initial submit needs to supply them.
        """
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO download_states
                    (media_item_id, status, source, message, torrent_hash, mode, previous_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(media_item_id) DO UPDATE SET
                    status=excluded.status,
                    source=excluded.source,
                    message=excluded.message,
                    torrent_hash=COALESCE(excluded.torrent_hash, download_states.torrent_hash),
                    mode=COALESCE(excluded.mode, download_states.mode),
                    previous_path=COALESCE(excluded.previous_path, download_states.previous_path),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (media_item_id, status, source, message, torrent_hash, mode, previous_path),
            )

    def clear_download_state(self, media_item_id: int) -> None:
        with self.conn() as c:
            c.execute('DELETE FROM download_states WHERE media_item_id = ?', (media_item_id,))

    def get_media_item(self, media_id: int):
        with self.conn() as c:
            return c.execute('SELECT * FROM media_items WHERE id = ?', (media_id,)).fetchone()

    def get_media_item_by_imdb_id(self, imdb_id: str):
        with self.conn() as c:
            return c.execute('SELECT * FROM media_items WHERE imdb_id = ?', (imdb_id,)).fetchone()

    def list_collection_items(self):
        with self.conn() as c:
            return c.execute(
                """
                SELECT m.id, m.imdb_id, m.tmdb_id, m.title, m.year, m.poster_url,
                       m.collection_id, m.collection_name
                FROM media_items m
                WHERE m.media_type = 'movie'
                  AND m.collection_id IS NOT NULL
                  AND (
                    (m.path IS NOT NULL AND TRIM(m.path) <> '')
                    OR EXISTS (
                        SELECT 1 FROM download_states d
                        WHERE d.media_item_id = m.id
                          AND d.status IN ('starting', 'handed_off', 'downloading')
                    )
                  )
                ORDER BY m.collection_name, m.year, m.title
                """
            ).fetchall()

    def list_discover_ignored_title_ids(self) -> set[int]:
        with self.conn() as c:
            rows = c.execute('SELECT tmdb_id FROM discover_ignored_titles').fetchall()
        return {int(row['tmdb_id']) for row in rows if row['tmdb_id'] is not None}

    def list_discover_ignored_collection_ids(self) -> set[int]:
        with self.conn() as c:
            rows = c.execute('SELECT collection_id FROM discover_ignored_collections').fetchall()
        return {int(row['collection_id']) for row in rows if row['collection_id'] is not None}

    def list_discover_ignored_titles(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """
                SELECT tmdb_id, collection_id, title, collection_name, created_at
                FROM discover_ignored_titles
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_discover_ignored_collections(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """
                SELECT collection_id, collection_name, created_at
                FROM discover_ignored_collections
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def ignore_discover_title(
        self,
        tmdb_id: int,
        collection_id: int | None = None,
        title: str | None = None,
        collection_name: str | None = None,
    ) -> None:
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO discover_ignored_titles (tmdb_id, collection_id, title, collection_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tmdb_id) DO UPDATE SET
                    collection_id=excluded.collection_id,
                    title=COALESCE(excluded.title, discover_ignored_titles.title),
                    collection_name=COALESCE(excluded.collection_name,
                                             discover_ignored_titles.collection_name)
                """,
                (tmdb_id, collection_id, title, collection_name),
            )

    def unignore_discover_title(self, tmdb_id: int) -> None:
        with self.conn() as c:
            c.execute('DELETE FROM discover_ignored_titles WHERE tmdb_id = ?', (tmdb_id,))

    def unignore_all_discover_titles(self) -> None:
        with self.conn() as c:
            c.execute('DELETE FROM discover_ignored_titles')

    def ignore_discover_collection(
        self, collection_id: int, collection_name: str | None = None
    ) -> None:
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO discover_ignored_collections (collection_id, collection_name)
                VALUES (?, ?)
                ON CONFLICT(collection_id) DO UPDATE SET
                    collection_name=COALESCE(excluded.collection_name,
                                             discover_ignored_collections.collection_name)
                """,
                (collection_id, collection_name),
            )

    def unignore_discover_collection(self, collection_id: int) -> None:
        with self.conn() as c:
            c.execute(
                'DELETE FROM discover_ignored_collections WHERE collection_id = ?', (collection_id,)
            )

    def unignore_all_discover_collections(self) -> None:
        with self.conn() as c:
            c.execute('DELETE FROM discover_ignored_collections')

    # A season's episode list is stable once aired, so it is cached in the
    # database rather than in process: checking every show for new episodes would
    # otherwise mean hundreds of TMDB requests after each restart.
    IGNORE_WHOLE_SHOW = -1

    def get_cached_season(
        self, tmdb_id: int, season_number: int, max_age_hours: int
    ) -> list[dict] | None:
        with self.conn() as c:
            row = c.execute(
                'SELECT episodes_json, fetched_at FROM tmdb_season_cache '
                'WHERE tmdb_id = ? AND season_number = ?',
                (tmdb_id, season_number),
            ).fetchone()
        if not row or not row['fetched_at']:
            return None
        try:
            fetched_at = datetime.strptime(row['fetched_at'], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
        if fetched_at < datetime.now() - timedelta(hours=max_age_hours):
            return None
        try:
            return json.loads(row['episodes_json'])
        except (TypeError, ValueError):
            return None

    def set_cached_season(self, tmdb_id: int, season_number: int, episodes: list[dict]) -> None:
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO tmdb_season_cache (tmdb_id, season_number, episodes_json, fetched_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(tmdb_id, season_number) DO UPDATE SET
                    episodes_json=excluded.episodes_json,
                    fetched_at=CURRENT_TIMESTAMP
                """,
                (tmdb_id, season_number, json.dumps(episodes)),
            )

    def get_cached_tv_status(self, tmdb_id: int, max_age_hours: int) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                'SELECT status_json, fetched_at FROM tmdb_tv_status_cache WHERE tmdb_id = ?',
                (tmdb_id,),
            ).fetchone()
        if not row or not row['fetched_at']:
            return None
        try:
            fetched_at = datetime.strptime(row['fetched_at'], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
        if fetched_at < datetime.now() - timedelta(hours=max_age_hours):
            return None
        try:
            return json.loads(row['status_json'])
        except (TypeError, ValueError):
            return None

    def set_cached_tv_status(self, tmdb_id: int, status: dict) -> None:
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO tmdb_tv_status_cache (tmdb_id, status_json, fetched_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(tmdb_id) DO UPDATE SET
                    status_json=excluded.status_json,
                    fetched_at=CURRENT_TIMESTAMP
                """,
                (tmdb_id, json.dumps(status)),
            )

    def ignore_tv_season(self, tmdb_id: int, season_number: int, title: str | None = None) -> None:
        """Hide a season's missing episodes. Season -1 hides the whole show."""
        with self.conn() as c:
            c.execute(
                'INSERT OR REPLACE INTO discover_ignored_tv (tmdb_id, season_number, title) '
                'VALUES (?, ?, ?)',
                (tmdb_id, season_number, title),
            )

    def unignore_tv(self, tmdb_id: int, season_number: int | None = None) -> None:
        with self.conn() as c:
            if season_number is None:
                c.execute('DELETE FROM discover_ignored_tv WHERE tmdb_id = ?', (tmdb_id,))
            else:
                c.execute(
                    'DELETE FROM discover_ignored_tv WHERE tmdb_id = ? AND season_number = ?',
                    (tmdb_id, season_number),
                )

    def list_ignored_tv(self) -> dict[int, set[int]]:
        with self.conn() as c:
            rows = c.execute('SELECT tmdb_id, season_number FROM discover_ignored_tv').fetchall()
        ignored: dict[int, set[int]] = {}
        for row in rows:
            ignored.setdefault(int(row['tmdb_id']), set()).add(int(row['season_number']))
        return ignored

    def get_cached_collection_parts(
        self, collection_id: int, max_age_hours: int
    ) -> list[dict] | None:
        with self.conn() as c:
            summary = c.execute(
                """
                SELECT MAX(fetched_at) AS fetched_at, COUNT(*) AS part_count
                FROM tmdb_collection_parts_cache
                WHERE collection_id = ?
                """,
                (collection_id,),
            ).fetchone()

            if not summary or (summary['part_count'] or 0) == 0:
                return None

            fetched_at_raw = summary['fetched_at']
            if not fetched_at_raw:
                return None
            try:
                fetched_at = datetime.strptime(fetched_at_raw, '%Y-%m-%d %H:%M:%S')
            except Exception:
                return None

            if fetched_at < datetime.now() - timedelta(hours=max_age_hours):
                return None

            rows = c.execute(
                """
                SELECT tmdb_id, title, year, poster_url, release_date, media_type
                FROM tmdb_collection_parts_cache
                WHERE collection_id = ?
                ORDER BY year, title
                """,
                (collection_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def set_cached_collection_parts(self, collection_id: int, parts: list[dict]) -> None:
        with self.conn() as c:
            c.execute(
                'DELETE FROM tmdb_collection_parts_cache WHERE collection_id = ?', (collection_id,)
            )
            if not parts:
                return
            c.executemany(
                """
                INSERT INTO tmdb_collection_parts_cache (
                    collection_id, tmdb_id, title, year, poster_url, release_date, media_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        collection_id,
                        part.get('tmdb_id'),
                        part.get('title'),
                        part.get('year'),
                        part.get('poster_url'),
                        part.get('release_date'),
                        part.get('media_type'),
                    )
                    for part in parts
                    if part.get('tmdb_id') is not None
                ],
            )

    def get_cached_watchlist_entries(self, max_age_hours: int) -> dict[str, dict]:
        with self.conn() as c:
            rows = c.execute(
                """
                SELECT imdb_id, tmdb_id, title, year, media_type, poster_url, fetched_at
                FROM discover_watchlist_cache
                """
            ).fetchall()

        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        out: dict[str, dict] = {}
        for row in rows:
            imdb_id = row['imdb_id']
            if not imdb_id:
                continue
            fetched_at_raw = row['fetched_at']
            if not fetched_at_raw:
                continue
            try:
                fetched_at = datetime.strptime(fetched_at_raw, '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            if fetched_at < cutoff:
                continue
            out[str(imdb_id)] = dict(row)
        return out

    def prune_watchlist_cache(self, imdb_ids: set[str]) -> None:
        with self.conn() as c:
            if not imdb_ids:
                c.execute('DELETE FROM discover_watchlist_cache')
                return
            placeholders = ','.join(['?'] * len(imdb_ids))
            c.execute(
                f'DELETE FROM discover_watchlist_cache WHERE imdb_id NOT IN ({placeholders})',
                tuple(sorted(imdb_ids)),
            )

    def upsert_watchlist_cache_entries(self, entries: list[dict]) -> None:
        if not entries:
            return
        with self.conn() as c:
            c.executemany(
                """
                INSERT INTO discover_watchlist_cache (
                    imdb_id, tmdb_id, title, year, media_type, poster_url, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(imdb_id) DO UPDATE SET
                    tmdb_id=excluded.tmdb_id,
                    title=excluded.title,
                    year=excluded.year,
                    media_type=excluded.media_type,
                    poster_url=excluded.poster_url,
                    fetched_at=CURRENT_TIMESTAMP
                """,
                [
                    (
                        entry.get('imdb_id'),
                        entry.get('tmdb_id'),
                        entry.get('title'),
                        entry.get('year'),
                        entry.get('media_type'),
                        entry.get('poster_url'),
                    )
                    for entry in entries
                    if entry.get('imdb_id')
                ],
            )

    def add_to_watchlist(
        self,
        *,
        tmdb_id: int,
        media_type: str,
        imdb_id: str | None = None,
        title: str | None = None,
        year: int | None = None,
        poster_url: str | None = None,
    ) -> None:
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO watchlist_items (tmdb_id, media_type, imdb_id, title, year, poster_url)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tmdb_id, media_type) DO UPDATE SET
                    imdb_id=excluded.imdb_id,
                    title=excluded.title,
                    year=excluded.year,
                    poster_url=excluded.poster_url
                """,
                (tmdb_id, media_type, imdb_id, title, year, poster_url),
            )

    def remove_from_watchlist(self, tmdb_id: int, media_type: str) -> None:
        with self.conn() as c:
            c.execute(
                'DELETE FROM watchlist_items WHERE tmdb_id = ? AND media_type = ?',
                (tmdb_id, media_type),
            )

    def list_watchlist(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """
                SELECT tmdb_id, media_type, imdb_id, title, year, poster_url, added_at
                FROM watchlist_items
                ORDER BY added_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def is_in_watchlist(self, tmdb_id: int, media_type: str) -> bool:
        with self.conn() as c:
            row = c.execute(
                'SELECT 1 FROM watchlist_items WHERE tmdb_id = ? AND media_type = ?',
                (tmdb_id, media_type),
            ).fetchone()
        return row is not None

    def get_setting(self, key: str) -> str | None:
        with self.conn() as c:
            row = c.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.conn() as c:
            c.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?)'
                ' ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, value),
            )

    def delete_setting(self, key: str) -> None:
        with self.conn() as c:
            c.execute('DELETE FROM settings WHERE key = ?', (key,))

    def clear_quality_checks(self, media_item_id: int) -> None:
        """Drop stored upgrade results for an item.

        Called once the local file changes, since a recorded "better release
        available" refers to the quality the item had at search time.
        """
        with self.conn() as c:
            c.execute('DELETE FROM quality_checks WHERE media_item_id = ?', (media_item_id,))

    def add_quality_check(
        self,
        media_item_id: int,
        best_found_quality: str | None,
        best_found_name: str | None,
        best_found_desc_link: str | None,
        found: bool,
        raw_result_count: int,
    ) -> None:
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO quality_checks (
                    media_item_id, best_found_quality, best_found_name,
                    best_found_desc_link, found, raw_result_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    media_item_id,
                    best_found_quality,
                    best_found_name,
                    best_found_desc_link,
                    1 if found else 0,
                    raw_result_count,
                ),
            )

    # A position within the last 2% of the duration counts as watched rather
    # than requiring the exact end, since players rarely fire a timeupdate at
    # precisely 100% and credits do not need to be sat through.
    _WATCHED_THRESHOLD = 0.98

    def get_playback_position(self, media_item_id: int, episode_key: str = '') -> dict | None:
        """Saved position for one item, or one episode of a show.

        `episode_key` is the episode's file path relative to the show folder —
        the same string the player already receives as `?episode=` — so a movie
        (which passes none) and an episode both fall out of one schema. Empty
        string is the movie/whole-item case, never a wildcard.
        """
        with self.conn() as c:
            row = c.execute(
                'SELECT position_seconds, duration_seconds, watched FROM playback_positions '
                'WHERE media_item_id = ? AND episode_key = ?',
                (media_item_id, episode_key or ''),
            ).fetchone()
        return dict(row) if row else None

    def list_playback_positions(self, media_item_id: int) -> dict[str, dict]:
        """Every saved position for an item, keyed by episode_key.

        Lets a season list show a resume point and a watched mark per episode
        in one query rather than one round trip each.
        """
        with self.conn() as c:
            rows = c.execute(
                'SELECT episode_key, position_seconds, duration_seconds, watched '
                'FROM playback_positions WHERE media_item_id = ?',
                (media_item_id,),
            ).fetchall()
        return {row['episode_key']: dict(row) for row in rows}

    def set_playback_position(
        self,
        media_item_id: int,
        position_seconds: float,
        duration_seconds: float | None = None,
        episode_key: str = '',
    ) -> None:
        """Save the position for one item, or one episode of a show.

        Watched is derived here rather than left to the caller, so "resume"
        and "watched" cannot silently disagree about the same position.
        """
        watched = bool(
            duration_seconds and position_seconds >= duration_seconds * self._WATCHED_THRESHOLD
        )
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO playback_positions
                    (media_item_id, episode_key, position_seconds, duration_seconds, watched)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(media_item_id, episode_key) DO UPDATE SET
                    position_seconds=excluded.position_seconds,
                    duration_seconds=COALESCE(excluded.duration_seconds,
                                              playback_positions.duration_seconds),
                    -- Once watched, a rewind to review a scene should not un-mark it;
                    -- only reaching the threshold again, or a later episode, does.
                    watched=(playback_positions.watched OR excluded.watched),
                    last_updated=CURRENT_TIMESTAMP
                """,
                (media_item_id, episode_key or '', position_seconds, duration_seconds, watched),
            )

    def get_episode_ordering(self, media_item_id: int) -> dict | None:
        """The ordering decided for a show, or None when it has never been checked.

        None is not the same as "use the default" — that is a row with
        ordering_id NULL. The distinction is what stops every startup from
        re-probing every file on a show that was already checked and confirmed.
        """
        with self.conn() as c:
            row = c.execute(
                'SELECT ordering_id, ordering_name, ordering_kind, untitled_seasons '
                'FROM tv_episode_orderings WHERE media_item_id = ?',
                (media_item_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result['untitled_seasons'] = {
            int(n) for n in (result['untitled_seasons'] or '').split(',') if n
        }
        return result

    def set_episode_ordering(
        self,
        media_item_id: int,
        ordering_id: str | None,
        ordering_name: str | None = None,
        ordering_kind: str | None = None,
        untitled_seasons: set[int] | None = None,
    ) -> None:
        """Record what detection found for a show, including "the default is fine"."""
        seasons_text = ','.join(str(n) for n in sorted(untitled_seasons or set()))
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO tv_episode_orderings
                    (media_item_id, ordering_id, ordering_name, ordering_kind, untitled_seasons)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(media_item_id) DO UPDATE SET
                    ordering_id=excluded.ordering_id,
                    ordering_name=excluded.ordering_name,
                    ordering_kind=excluded.ordering_kind,
                    untitled_seasons=excluded.untitled_seasons,
                    detected_at=CURRENT_TIMESTAMP
                """,
                (media_item_id, ordering_id, ordering_name, ordering_kind, seasons_text),
            )

    def clear_episode_ordering(self, media_item_id: int) -> None:
        """Forget a show's detected ordering, so the next startup checks again.

        Not called automatically — for a future "redetect" action, or by hand
        when a show's files have been replaced and the old answer no longer
        applies.
        """
        with self.conn() as c:
            c.execute('DELETE FROM tv_episode_orderings WHERE media_item_id = ?', (media_item_id,))
