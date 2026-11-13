"""SQLite connection handling and schema creation.

Content and progress tables share one SQLite file in v1, but the rule from PROJECT.md
holds: references go one way. Progress tables point at content tables (ON DELETE CASCADE);
no content table has a foreign key to a progress table.

Conventions:
- ids of decks, notes and players are UUID strings; media ids are sha256 hex digests.
- JSON columns are stored as TEXT and checked with json_valid().
- All timestamps (created_at, updated_at, due, reviewed_at) are REAL unix epoch seconds, UTC.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from dunzo.config import get_settings

SCHEMA_VERSION = 1

SCHEMA = """
-- ---------------------------------------------------------------- content (shareable)

CREATE TABLE IF NOT EXISTS deck (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,                 -- JavaScript text
    source_hash TEXT NOT NULL,                 -- sha256 of source
    note_schema TEXT CHECK (note_schema IS NULL OR json_valid(note_schema))
);

CREATE TABLE IF NOT EXISTS note (
    id         TEXT PRIMARY KEY,
    deck_id    TEXT NOT NULL REFERENCES deck(id) ON DELETE CASCADE,
    player_id  TEXT NOT NULL REFERENCES player(id),
    card_count INTEGER NOT NULL CHECK (card_count >= 1),
    position   INTEGER NOT NULL,               -- order within deck; gaps of 1024
    properties TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(properties)),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (deck_id, position)
);
CREATE INDEX IF NOT EXISTS idx_note_player ON note(player_id);

CREATE TABLE IF NOT EXISTS media (
    id   TEXT PRIMARY KEY,                     -- sha256 of the bytes
    mime TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0)    -- bytes live in data/media/<id>
);

CREATE TABLE IF NOT EXISTS note_media (
    note_id  TEXT NOT NULL REFERENCES note(id) ON DELETE CASCADE,
    media_id TEXT NOT NULL REFERENCES media(id),
    PRIMARY KEY (note_id, media_id)
);
CREATE INDEX IF NOT EXISTS idx_note_media_media ON note_media(media_id);

CREATE TABLE IF NOT EXISTS player_media (
    player_id TEXT NOT NULL REFERENCES player(id) ON DELETE CASCADE,
    media_id  TEXT NOT NULL REFERENCES media(id),
    PRIMARY KEY (player_id, media_id)
);
CREATE INDEX IF NOT EXISTS idx_player_media_media ON player_media(media_id);

-- ---------------------------------------------------------------- progress (private)

-- A row exists only once the card has been reviewed. New cards have no row.
CREATE TABLE IF NOT EXISTS card_state (
    note_id    TEXT NOT NULL REFERENCES note(id) ON DELETE CASCADE,
    card_index INTEGER NOT NULL CHECK (card_index >= 0),
    due        REAL NOT NULL,
    scheduler  TEXT NOT NULL,                  -- e.g. 'sm2'
    state      TEXT NOT NULL CHECK (json_valid(state)),
    PRIMARY KEY (note_id, card_index)
);
CREATE INDEX IF NOT EXISTS idx_card_state_due ON card_state(due);

CREATE TABLE IF NOT EXISTS review_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id      TEXT NOT NULL REFERENCES note(id) ON DELETE CASCADE,
    card_index   INTEGER NOT NULL CHECK (card_index >= 0),
    reviewed_at  REAL NOT NULL,
    grade        INTEGER NOT NULL CHECK (grade BETWEEN 1 AND 4),
    detail       TEXT CHECK (detail IS NULL OR json_valid(detail)),
    state_before TEXT CHECK (state_before IS NULL OR json_valid(state_before)),
    state_after  TEXT NOT NULL CHECK (json_valid(state_after))
);
CREATE INDEX IF NOT EXISTS idx_review_log_card ON review_log(note_id, card_index, reviewed_at);
CREATE INDEX IF NOT EXISTS idx_review_log_time ON review_log(reviewed_at);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with foreign keys enforced and rows addressable by column name."""
    db_path = path if path is not None else get_settings().db_path
    # check_same_thread=False: FastAPI may set up and tear down a dependency in different
    # worker threads. A connection is still only used by one request at a time.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path: Path | None = None) -> None:
    """Create the schema if needed. Safe to call on every startup."""
    conn = connect(path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {version} is newer than this code "
                f"understands ({SCHEMA_VERSION}). Upgrade the app or use another DATA_DIR."
            )
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, committed on success."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
