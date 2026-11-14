from datetime import datetime, timedelta

import pytest

from dunzo import content_repo as repo
from dunzo import review_queue as rq

NOON = datetime(2026, 11, 14, 12, 0).timestamp()  # "now": noon, local time
DAY = 86400.0
TODAY = datetime(2026, 11, 14).date()


@pytest.fixture
def deck(conn):
    return repo.create_deck(conn, "d")


@pytest.fixture
def player(conn):
    return repo.create_player(conn, "p", "x")


def note(conn, deck, player, count=1):
    return repo.create_note(conn, deck.id, player.id, count, {})


def log(conn, n, index, at, grade=3, seen=True):
    before = "'{}'" if seen else "NULL"
    conn.execute(
        "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, state_before,"
        f" state_after) VALUES (?, ?, ?, ?, {before}, '{{}}')",
        (n.id, index, at, grade),
    )


def state(conn, n, index, due):
    conn.execute("INSERT INTO card_state VALUES (?, ?, ?, 'sm2', '{}')", (n.id, index, due))


def test_empty_collection(conn):
    s = rq.stats(conn, NOON)
    assert (s.cards, s.new, s.reviewed, s.due_now, s.total_reviews, s.reviews_today) == (0,) * 6
    assert len(s.history) == 30 and all(d.count == 0 for d in s.history)
    assert len(s.forecast) == rq.FORECAST_DAYS and all(d.count == 0 for d in s.forecast)
    assert s.grades == {1: 0, 2: 0, 3: 0, 4: 0} and s.retention is None


def test_card_counts(conn, deck, player):
    a, b = note(conn, deck, player, 3), note(conn, deck, player, 2)
    state(conn, a, 0, NOON - 10)
    state(conn, a, 1, NOON + DAY)
    state(conn, b, 1, NOON - 5)
    state(conn, b, 9, NOON - 5)  # stale row beyond card_count: ignored everywhere
    s = rq.stats(conn, NOON, deck.id)
    assert (s.cards, s.reviewed, s.new, s.due_now) == (5, 3, 2, 2)


def test_history_is_zero_filled_and_ends_today(conn, deck, player):
    n = note(conn, deck, player)
    log(conn, n, 0, NOON - 3600)
    log(conn, n, 0, NOON - 7200)
    log(conn, n, 0, NOON - 2 * DAY)
    s = rq.stats(conn, NOON, days=7)
    assert [d.day for d in s.history] == [TODAY - timedelta(days=i) for i in range(6, -1, -1)]
    assert [d.count for d in s.history] == [0, 0, 0, 0, 1, 0, 2]
    assert s.reviews_today == 2 and s.total_reviews == 3


def test_reviews_outside_the_window_count_only_in_the_total(conn, deck, player):
    n = note(conn, deck, player)
    log(conn, n, 0, NOON - 40 * DAY, grade=1)
    s = rq.stats(conn, NOON, days=30)
    assert s.total_reviews == 1 and sum(d.count for d in s.history) == 0
    assert s.grades == {1: 0, 2: 0, 3: 0, 4: 0}


def test_grades_and_retention_only_count_re_reviews(conn, deck, player):
    n = note(conn, deck, player)
    log(conn, n, 0, NOON - 100, grade=4, seen=False)  # first review: in grades, not retention
    log(conn, n, 0, NOON - 90, grade=1)
    log(conn, n, 0, NOON - 80, grade=2)
    log(conn, n, 0, NOON - 70, grade=3)
    log(conn, n, 0, NOON - 60, grade=3)
    s = rq.stats(conn, NOON)
    assert s.grades == {1: 1, 2: 1, 3: 2, 4: 1}
    assert s.retention == pytest.approx(3 / 4)


def test_first_reviews_alone_give_no_retention(conn, deck, player):
    n = note(conn, deck, player)
    log(conn, n, 0, NOON - 100, grade=3, seen=False)
    assert rq.stats(conn, NOON).retention is None


def test_forecast_folds_overdue_into_today(conn, deck, player):
    n = note(conn, deck, player, 5)
    state(conn, n, 0, NOON - 5 * DAY)  # overdue
    state(conn, n, 1, NOON + 3600)  # later today
    state(conn, n, 2, NOON + DAY)  # tomorrow
    state(conn, n, 3, NOON + 6 * DAY)  # last forecast day
    state(conn, n, 4, NOON + 7 * DAY)  # beyond the horizon
    s = rq.stats(conn, NOON)
    assert [d.count for d in s.forecast] == [2, 1, 0, 0, 0, 0, 1]
    assert s.forecast[0].day == TODAY and s.forecast[-1].day == TODAY + timedelta(days=6)


def test_deck_scope_versus_everything(conn, deck, player):
    other = repo.create_deck(conn, "other")
    a, b = note(conn, deck, player, 2), note(conn, other, player, 3)
    log(conn, a, 0, NOON - 10)
    log(conn, b, 0, NOON - 10)
    log(conn, b, 1, NOON - 10)
    state(conn, b, 0, NOON - 1)
    mine, theirs, both = (rq.stats(conn, NOON, d) for d in (deck.id, other.id, None))
    assert (mine.cards, mine.total_reviews, mine.due_now) == (2, 1, 0)
    assert (theirs.cards, theirs.total_reviews, theirs.due_now) == (3, 2, 1)
    assert (both.cards, both.total_reviews, both.reviews_today) == (5, 3, 3)


def test_stats_never_write(conn, deck, player):
    note(conn, deck, player)
    rq.stats(conn, NOON)
    for table in ("card_state", "review_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
