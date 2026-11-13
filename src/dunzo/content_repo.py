"""SQL for content tables (deck, player, note, media and their link tables).

Content only: nothing here may touch card_state or review_log (see progress_repo.py).
Functions take an open connection and do not commit; the request-scoped connection from
`dunzo.db.get_db` commits on success.
"""

import sqlite3
import uuid
from dataclasses import dataclass

MAX_DECK_NAME_LENGTH = 200


@dataclass(frozen=True)
class Deck:
    id: str
    name: str
    note_count: int


def normalize_deck_name(name: str) -> str:
    """Trim the name and check it. Raises ValueError with a message fit to show the user."""
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValueError("Deck name cannot be empty.")
    if len(cleaned) > MAX_DECK_NAME_LENGTH:
        raise ValueError(f"Deck name cannot be longer than {MAX_DECK_NAME_LENGTH} characters.")
    return cleaned


_DECK_SELECT = """
    SELECT d.id, d.name, COUNT(n.id) AS note_count
    FROM deck d
    LEFT JOIN note n ON n.deck_id = d.id
"""


def _deck_from_row(row: sqlite3.Row) -> Deck:
    return Deck(id=row["id"], name=row["name"], note_count=row["note_count"])


def list_decks(conn: sqlite3.Connection) -> list[Deck]:
    """All decks, sorted by name (case-insensitive), with their note counts."""
    rows = conn.execute(
        _DECK_SELECT + " GROUP BY d.id ORDER BY d.name COLLATE NOCASE, d.id"
    ).fetchall()
    return [_deck_from_row(r) for r in rows]


def get_deck(conn: sqlite3.Connection, deck_id: str) -> Deck | None:
    row = conn.execute(_DECK_SELECT + " WHERE d.id = ? GROUP BY d.id", (deck_id,)).fetchone()
    return _deck_from_row(row) if row else None


def create_deck(conn: sqlite3.Connection, name: str) -> Deck:
    """Create a deck. Raises ValueError if the name is invalid."""
    cleaned = normalize_deck_name(name)
    deck_id = str(uuid.uuid4())
    conn.execute("INSERT INTO deck (id, name) VALUES (?, ?)", (deck_id, cleaned))
    return Deck(id=deck_id, name=cleaned, note_count=0)


def delete_deck(conn: sqlite3.Connection, deck_id: str) -> bool:
    """Delete a deck. Foreign keys cascade to its notes (and any progress for them).

    Returns False if there was no such deck.
    """
    cursor = conn.execute("DELETE FROM deck WHERE id = ?", (deck_id,))
    return cursor.rowcount > 0
