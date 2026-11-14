"""SQL for progress tables (card_state and review_log). Progress only: no content tables.

Functions take an open connection and do not commit (see content_repo.py).
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProgressCount:
    card_states: int  # reviewed cards (card_state rows)
    reviews: int  # logged reviews (review_log rows)

    def __bool__(self) -> bool:
        return bool(self.card_states or self.reviews)


@dataclass(frozen=True)
class CardState:
    note_id: str
    card_index: int
    due: float  # unix epoch seconds, UTC
    scheduler: str  # name of the scheduler that owns `state`, e.g. 'sm2'
    state: dict[str, Any]


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


def get_card_state(conn: sqlite3.Connection, note_id: str, card_index: int) -> CardState | None:
    """The card's scheduling state, or None if it has never been reviewed (a new card)."""
    row = conn.execute(
        "SELECT note_id, card_index, due, scheduler, state FROM card_state"
        " WHERE note_id = ? AND card_index = ?",
        (note_id, card_index),
    ).fetchone()
    if row is None:
        return None
    return CardState(
        note_id=row["note_id"],
        card_index=row["card_index"],
        due=row["due"],
        scheduler=row["scheduler"],
        state=json.loads(row["state"]),
    )


def record_review(
    conn: sqlite3.Connection,
    *,
    note_id: str,
    card_index: int,
    reviewed_at: float,
    grade: int,
    detail: Any,
    scheduler: str,
    state_before: dict[str, Any] | None,
    state_after: dict[str, Any],
    due: float,
) -> None:
    """Write one review: create or update the card's card_state row and append to review_log.

    The card_state row is created here, when the card is first reviewed, and never earlier.
    `state_before` is None for a card's first review. `detail` (any JSON value, or None) is
    whatever the player reported.
    """
    conn.execute(
        "INSERT INTO card_state (note_id, card_index, due, scheduler, state)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (note_id, card_index) DO UPDATE SET"
        " due = excluded.due, scheduler = excluded.scheduler, state = excluded.state",
        (note_id, card_index, due, scheduler, json.dumps(state_after)),
    )
    conn.execute(
        "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, detail,"
        " state_before, state_after) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            note_id,
            card_index,
            reviewed_at,
            grade,
            None if detail is None else json.dumps(detail, ensure_ascii=False),
            None if state_before is None else json.dumps(state_before),
            json.dumps(state_after),
        ),
    )


@dataclass(frozen=True)
class ReviewEntry:
    id: int
    note_id: str
    card_index: int
    reviewed_at: float  # unix epoch seconds, UTC
    grade: int
    detail: Any  # whatever the player reported (any JSON value), or None
    state_before: dict[str, Any] | None  # None for a card's first review
    state_after: dict[str, Any]


def _load(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def list_reviews(
    conn: sqlite3.Connection, note_id: str, card_index: int | None = None, limit: int = 100
) -> list[ReviewEntry]:
    """A note's logged reviews, newest first; optionally only one card's."""
    sql = (
        "SELECT id, note_id, card_index, reviewed_at, grade, detail, state_before, state_after"
        " FROM review_log WHERE note_id = ?"
    )
    params: list[Any] = [note_id]
    if card_index is not None:
        sql += " AND card_index = ?"
        params.append(card_index)
    sql += " ORDER BY reviewed_at DESC, id DESC LIMIT ?"
    params.append(limit)
    return [
        ReviewEntry(
            id=r["id"],
            note_id=r["note_id"],
            card_index=r["card_index"],
            reviewed_at=r["reviewed_at"],
            grade=r["grade"],
            detail=_load(r["detail"]),
            state_before=_load(r["state_before"]),
            state_after=_load(r["state_after"]),
        )
        for r in conn.execute(sql, params).fetchall()
    ]
