"""Storage for imported watch history — a separate database on purpose.

F-0208.01: Trakt's API moved behind a paywall this app's registered client
isn't on (see Common/DevLog.md), so watch history has to come from somewhere
that doesn't depend on Trakt staying reachable — a file the user already has,
exported from wherever they were tracking it before.

This is its own SQLite file, not a table in library.db: an import is a record
of what happened on another platform, not part of this library, and a bad or
partial import should never be able to touch media_items. Nothing here reads
or writes the main store.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS watch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    year INTEGER,
    media_type TEXT NOT NULL,
    season_number INTEGER,
    episode_number INTEGER,
    imdb_id TEXT,
    tmdb_id INTEGER,
    watched_at TEXT,
    source TEXT NOT NULL,
    source_file TEXT,
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    raw_row TEXT,
    dedupe_key TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_watch_history_title_year
    ON watch_history(title, year);
CREATE INDEX IF NOT EXISTS idx_watch_history_source
    ON watch_history(source);
"""


def _dedupe_key(record: dict, source: str) -> str:
    """Identify a record by its original content, not its normalised form.

    Keyed on the *raw* source row rather than title/year/media_type: two
    genuinely different watches (a rewatch, an episode with no date recorded)
    can normalise to the same title/year, and collapsing them would lose real
    history. What must never duplicate is importing the same export twice —
    hashing the untouched row catches exactly that, and nothing else.
    """
    raw = json.dumps(record, sort_keys=True, default=str)
    return hashlib.sha256(f'{source}|{raw}'.encode()).hexdigest()


class HistoryStore:
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

    def initialize(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA_SQL)

    def import_records(
        self, raw_records: list[dict], normalized: list[dict], source: str, source_file: str
    ) -> dict:
        """Insert normalised records, skipping any already imported.

        `raw_records` and `normalized` are parallel lists — the dedupe key is
        computed from the raw row (see `_dedupe_key`), but what is queried and
        displayed later is the normalised shape.
        """
        imported = 0
        skipped_duplicate = 0
        with self.conn() as c:
            for raw, record in zip(raw_records, normalized, strict=True):
                key = _dedupe_key(raw, source)
                cursor = c.execute(
                    """
                    INSERT OR IGNORE INTO watch_history
                        (title, year, media_type, season_number, episode_number,
                         imdb_id, tmdb_id, watched_at, source, source_file, raw_row, dedupe_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record['title'],
                        record.get('year'),
                        record['media_type'],
                        record.get('season_number'),
                        record.get('episode_number'),
                        record.get('imdb_id'),
                        record.get('tmdb_id'),
                        record.get('watched_at'),
                        source,
                        source_file,
                        json.dumps(raw, default=str),
                        key,
                    ),
                )
                if cursor.rowcount:
                    imported += 1
                else:
                    skipped_duplicate += 1
        return {
            'imported': imported,
            'skipped_duplicate': skipped_duplicate,
            'total_in_file': len(normalized),
        }

    def count(self) -> int:
        with self.conn() as c:
            return c.execute('SELECT COUNT(*) FROM watch_history').fetchone()[0]

    def summary(self) -> dict:
        """Total rows and a per-source breakdown, for the settings page."""
        with self.conn() as c:
            total = c.execute('SELECT COUNT(*) FROM watch_history').fetchone()[0]
            by_source = [
                dict(row)
                for row in c.execute(
                    'SELECT source, COUNT(*) AS count, MAX(imported_at) AS last_imported_at '
                    'FROM watch_history GROUP BY source ORDER BY last_imported_at DESC'
                )
            ]
        return {'total': total, 'by_source': by_source}
