"""Read-only queries that join content and progress: which card to review next, and stats.

The only module besides the repos allowed to look at both sides. It never writes.

A deck's review queue has two parts:
  1. due cards: card_state rows with due <= now, earliest due first;
  2. new cards: cards that have no card_state row, in deck order (note.position, then
     card_index). Cards are not stored, so a note's indices 0..card_count-1 are enumerated
     in the query with a recursive CTE.
Due cards always come before new ones. A card_state row whose index is >= the note's
card_count (which should not exist) is ignored.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class QueuedCard:
    note_id: str
    card_index: int
    is_new: bool  # True: never reviewed (no card_state row)


@dataclass(frozen=True)
class QueueCounts:
    due: int
    new: int


_NEXT_DUE = """
    SELECT cs.note_id, cs.card_index
    FROM card_state cs JOIN note n ON n.id = cs.note_id
    WHERE n.deck_id = ? AND cs.due <= ? AND cs.card_index < n.card_count
    ORDER BY cs.due, n.position, cs.card_index
    LIMIT 1
"""

_NEXT_NEW = """
    WITH RECURSIVE cards(note_id, card_index) AS (
        SELECT id, 0 FROM note WHERE deck_id = ?
        UNION ALL
        SELECT c.note_id, c.card_index + 1
        FROM cards c JOIN note n ON n.id = c.note_id
        WHERE c.card_index + 1 < n.card_count
    )
    SELECT c.note_id, c.card_index
    FROM cards c
    JOIN note n ON n.id = c.note_id
    LEFT JOIN card_state cs ON cs.note_id = c.note_id AND cs.card_index = c.card_index
    WHERE cs.note_id IS NULL
    ORDER BY n.position, c.card_index
    LIMIT 1
"""


def next_card(conn: sqlite3.Connection, deck_id: str, now: float) -> QueuedCard | None:
    """The next card to review in a deck, or None when nothing is due and nothing is new."""
    row = conn.execute(_NEXT_DUE, (deck_id, now)).fetchone()
    if row is not None:
        return QueuedCard(row["note_id"], row["card_index"], is_new=False)
    row = conn.execute(_NEXT_NEW, (deck_id,)).fetchone()
    if row is not None:
        return QueuedCard(row["note_id"], row["card_index"], is_new=True)
    return None


def counts(conn: sqlite3.Connection, deck_id: str, now: float) -> QueueCounts:
    """How many cards of the deck are due now, and how many have never been reviewed."""
    due = conn.execute(
        "SELECT COUNT(*) FROM card_state cs JOIN note n ON n.id = cs.note_id"
        " WHERE n.deck_id = ? AND cs.due <= ? AND cs.card_index < n.card_count",
        (deck_id, now),
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COALESCE(SUM(card_count), 0) FROM note WHERE deck_id = ?", (deck_id,)
    ).fetchone()[0]
    reviewed = conn.execute(
        "SELECT COUNT(*) FROM card_state cs JOIN note n ON n.id = cs.note_id"
        " WHERE n.deck_id = ? AND cs.card_index < n.card_count",
        (deck_id,),
    ).fetchone()[0]
    return QueueCounts(due=due, new=total - reviewed)


# ------------------------------------------------------------------------------ stats

FORECAST_DAYS = 7


@dataclass(frozen=True)
class DayCount:
    day: date  # a calendar day in the server's local time zone
    count: int


@dataclass(frozen=True)
class Stats:
    cards: int  # all cards (sum of card_count)
    reviewed: int  # cards that have a card_state row
    new: int  # cards never reviewed
    due_now: int
    total_reviews: int  # every review ever logged
    reviews_today: int
    history: list[DayCount]  # reviews per day, oldest first, ending today, no gaps
    forecast: list[DayCount]  # cards coming due per day, today first (overdue count as today)
    grades: dict[int, int]  # reviews per grade (1-4) within the history window
    retention: float | None  # share of re-reviews graded 2-4 in the window; None if there are none


def _scope(deck_id: str | None) -> tuple[str, tuple[str, ...]]:
    """SQL (to append to a WHERE on a query that joins `note n`) limiting it to one deck."""
    return ("", ()) if deck_id is None else (" AND n.deck_id = ?", (deck_id,))


def _start_of_day(day: date) -> float:
    return datetime.combine(day, time.min).timestamp()


def stats(
    conn: sqlite3.Connection, now: float, deck_id: str | None = None, days: int = 30
) -> Stats:
    """Collection stats for one deck, or for every deck when `deck_id` is None.

    Days are calendar days in the server's local time zone. `days` is the length of the
    history window (including today). Review history lives in review_log, so it disappears
    with the note it belongs to.
    """
    scope, params = _scope(deck_id)
    today = datetime.fromtimestamp(now).date()

    cards = conn.execute(
        "SELECT COALESCE(SUM(n.card_count), 0) FROM note n WHERE 1 = 1" + scope, params
    ).fetchone()[0]
    reviewed = conn.execute(
        "SELECT COUNT(*) FROM card_state cs JOIN note n ON n.id = cs.note_id"
        " WHERE cs.card_index < n.card_count" + scope,
        params,
    ).fetchone()[0]
    due_now = conn.execute(
        "SELECT COUNT(*) FROM card_state cs JOIN note n ON n.id = cs.note_id"
        " WHERE cs.card_index < n.card_count AND cs.due <= ?" + scope,
        (now, *params),
    ).fetchone()[0]
    total_reviews = conn.execute(
        "SELECT COUNT(*) FROM review_log rl JOIN note n ON n.id = rl.note_id WHERE 1 = 1" + scope,
        params,
    ).fetchone()[0]

    first_day = today - timedelta(days=days - 1)
    per_day = {
        row["day"]: row["c"]
        for row in conn.execute(
            "SELECT date(rl.reviewed_at, 'unixepoch', 'localtime') AS day, COUNT(*) AS c"
            " FROM review_log rl JOIN note n ON n.id = rl.note_id"
            " WHERE rl.reviewed_at >= ?" + scope + " GROUP BY day",
            (_start_of_day(first_day), *params),
        )
    }
    history = [
        DayCount(d, per_day.get(d.isoformat(), 0))
        for d in (first_day + timedelta(days=i) for i in range(days))
    ]

    grades = {1: 0, 2: 0, 3: 0, 4: 0}
    seen_total = seen_passed = 0
    for row in conn.execute(
        "SELECT rl.grade AS grade, rl.state_before IS NOT NULL AS seen, COUNT(*) AS c"
        " FROM review_log rl JOIN note n ON n.id = rl.note_id"
        " WHERE rl.reviewed_at >= ?" + scope + " GROUP BY rl.grade, seen",
        (_start_of_day(first_day), *params),
    ):
        grades[row["grade"]] += row["c"]
        if row["seen"]:
            seen_total += row["c"]
            if row["grade"] >= 2:
                seen_passed += row["c"]

    horizon = _start_of_day(today + timedelta(days=FORECAST_DAYS))
    due_per_day = [0] * FORECAST_DAYS
    for row in conn.execute(
        "SELECT date(cs.due, 'unixepoch', 'localtime') AS day, COUNT(*) AS c"
        " FROM card_state cs JOIN note n ON n.id = cs.note_id"
        " WHERE cs.card_index < n.card_count AND cs.due < ?" + scope + " GROUP BY day",
        (horizon, *params),
    ):
        offset = max(0, (date.fromisoformat(row["day"]) - today).days)  # overdue: today
        due_per_day[offset] += row["c"]
    forecast = [DayCount(today + timedelta(days=i), c) for i, c in enumerate(due_per_day)]

    return Stats(
        cards=cards,
        reviewed=reviewed,
        new=cards - reviewed,
        due_now=due_now,
        total_reviews=total_reviews,
        reviews_today=history[-1].count,
        history=history,
        forecast=forecast,
        grades=grades,
        retention=(seen_passed / seen_total) if seen_total else None,
    )
