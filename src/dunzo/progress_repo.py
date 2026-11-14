"""SQL for progress tables (card_state and review_log). Progress only: no content tables.

Functions take an open connection and do not commit (see content_repo.py).
"""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressCount:
    card_states: int  # reviewed cards (card_state rows)
    reviews: int  # logged reviews (review_log rows)

    def __bool__(self) -> bool:
        return bool(self.card_states or self.reviews)


def count_progress_from(conn: sqlite3.Connection, note_id: str, from_index: int) -> ProgressCount:
    """Progress that exists for the note's cards with card_index >= from_index."""
    states = conn.execute(
        "SELECT COUNT(*) FROM card_state WHERE note_id = ? AND card_index >= ?",
        (note_id, from_index),
    ).fetchone()[0]
    reviews = conn.execute(
        "SELECT COUNT(*) FROM review_log WHERE note_id = ? AND card_index >= ?",
        (note_id, from_index),
    ).fetchone()[0]
    return ProgressCount(card_states=states, reviews=reviews)


def delete_progress_from(conn: sqlite3.Connection, note_id: str, from_index: int) -> None:
    """Delete scheduling state and review history for card_index >= from_index.

    Used when a note's card_count shrinks. Indices below from_index are never renumbered.
    """
    conn.execute(
        "DELETE FROM card_state WHERE note_id = ? AND card_index >= ?", (note_id, from_index)
    )
    conn.execute(
        "DELETE FROM review_log WHERE note_id = ? AND card_index >= ?", (note_id, from_index)
    )
