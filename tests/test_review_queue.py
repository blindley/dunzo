import pytest

from dunzo import content_repo as repo
from dunzo import review_queue as rq

NOW = 1_000_000.0


@pytest.fixture
def deck(conn):
    return repo.create_deck(conn, "d")


@pytest.fixture
def player(conn):
    return repo.create_player(conn, "p", "x")


def note(conn, deck, player, count=1, **kw):
    return repo.create_note(conn, deck.id, player.id, count, {}, **kw)


def reviewed(conn, n, index, due):
    conn.execute("INSERT INTO card_state VALUES (?, ?, ?, 'sm2', '{}')", (n.id, index, due))


def pair(card):
    return (card.note_id, card.card_index) if card else None


def test_empty_deck_has_nothing(conn, deck):
    assert rq.next_card(conn, deck.id, NOW) is None
    assert rq.counts(conn, deck.id, NOW) == rq.QueueCounts(due=0, new=0)


def test_new_cards_come_in_deck_order(conn, deck, player):
    a, b, c = note(conn, deck, player, 2), note(conn, deck, player, 1), note(conn, deck, player, 3)
    seen = []
    while card := rq.next_card(conn, deck.id, NOW):
        assert card.is_new
        seen.append(pair(card))
        reviewed(conn, conn_note(conn, card), card.card_index, NOW + 1000)  # reviewed, not due
    assert seen == [
        (a.id, 0), (a.id, 1), (b.id, 0), (c.id, 0), (c.id, 1), (c.id, 2),
    ]


def conn_note(conn, card):
    return repo.get_note(conn, card.note_id)


def test_new_order_follows_note_position(conn, deck, player):
    x, y = note(conn, deck, player), note(conn, deck, player)
    assert pair(rq.next_card(conn, deck.id, NOW)) == (x.id, 0)
    repo.move_note(conn, y.id, before_note_id=x.id)
    assert pair(rq.next_card(conn, deck.id, NOW)) == (y.id, 0)


def test_due_cards_come_before_new_and_earliest_first(conn, deck, player):
    n1, n2 = note(conn, deck, player, 1), note(conn, deck, player, 2)
    reviewed(conn, n2, 1, NOW - 10)
    reviewed(conn, n1, 0, NOW - 100)
    first = rq.next_card(conn, deck.id, NOW)
    assert pair(first) == (n1.id, 0) and not first.is_new
    conn.execute("UPDATE card_state SET due = ? WHERE note_id = ? AND card_index = 0", (NOW + 5, n1.id))
    assert pair(rq.next_card(conn, deck.id, NOW)) == (n2.id, 1)
    conn.execute("UPDATE card_state SET due = ? WHERE note_id = ?", (NOW + 5, n2.id))
    nxt = rq.next_card(conn, deck.id, NOW)
    assert pair(nxt) == (n2.id, 0) and nxt.is_new


def test_due_exactly_now_counts_and_future_does_not(conn, deck, player):
    n = note(conn, deck, player, 2)
    reviewed(conn, n, 0, NOW)
    reviewed(conn, n, 1, NOW + 0.5)
    assert rq.counts(conn, deck.id, NOW) == rq.QueueCounts(due=1, new=0)
    assert pair(rq.next_card(conn, deck.id, NOW)) == (n.id, 0)


def test_other_decks_are_ignored(conn, deck, player):
    other = repo.create_deck(conn, "other")
    foreign = note(conn, other, player, 2)
    reviewed(conn, foreign, 0, NOW - 1)
    assert rq.next_card(conn, deck.id, NOW) is None
    assert rq.counts(conn, deck.id, NOW) == rq.QueueCounts(0, 0)


def test_state_beyond_card_count_is_ignored(conn, deck, player):
    n = note(conn, deck, player, 2)
    reviewed(conn, n, 5, NOW - 1)  # should not exist; must not show up or skew the counts
    assert rq.counts(conn, deck.id, NOW) == rq.QueueCounts(due=0, new=2)
    assert pair(rq.next_card(conn, deck.id, NOW)) == (n.id, 0)


def test_counts(conn, deck, player):
    a, b = note(conn, deck, player, 3), note(conn, deck, player, 2)
    reviewed(conn, a, 0, NOW - 1)
    reviewed(conn, a, 1, NOW + 100)
    reviewed(conn, b, 1, NOW - 50)
    assert rq.counts(conn, deck.id, NOW) == rq.QueueCounts(due=2, new=2)


def test_queue_never_writes(conn, deck, player):
    note(conn, deck, player, 2)
    rq.next_card(conn, deck.id, NOW)
    rq.counts(conn, deck.id, NOW)
    for table in ("card_state", "review_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
