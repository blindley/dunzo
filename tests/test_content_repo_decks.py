import uuid

import pytest

from dunzo import content_repo


def add_player_and_note(conn, deck_id, position=1024):
    player_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO player (id, name, source, source_hash) VALUES (?, 'p', 'x', 'h')",
        (player_id,),
    )
    note_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO note (id, deck_id, player_id, card_count, position, properties,"
        " created_at, updated_at) VALUES (?, ?, ?, 2, ?, '{}', 0, 0)",
        (note_id, deck_id, player_id, position),
    )
    return note_id


def test_create_and_get(conn):
    deck = content_repo.create_deck(conn, "  Spanish   verbs ")
    assert deck.name == "Spanish verbs"
    assert deck.note_count == 0
    uuid.UUID(deck.id)
    assert content_repo.get_deck(conn, deck.id) == deck
    assert content_repo.get_deck(conn, "missing") is None


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_blank_name_rejected(conn, bad):
    with pytest.raises(ValueError, match="empty"):
        content_repo.create_deck(conn, bad)
    assert content_repo.list_decks(conn) == []


def test_too_long_name_rejected(conn):
    with pytest.raises(ValueError, match="longer"):
        content_repo.create_deck(conn, "x" * (content_repo.MAX_DECK_NAME_LENGTH + 1))


def test_list_sorted_case_insensitive_with_counts(conn):
    b = content_repo.create_deck(conn, "banana")
    a = content_repo.create_deck(conn, "Apple")
    c = content_repo.create_deck(conn, "Cherry")
    add_player_and_note(conn, b.id, 1024)
    add_player_and_note(conn, b.id, 2048)
    decks = content_repo.list_decks(conn)
    assert [d.name for d in decks] == ["Apple", "banana", "Cherry"]
    assert {d.id: d.note_count for d in decks} == {a.id: 0, b.id: 2, c.id: 0}


def test_duplicate_names_allowed(conn):
    content_repo.create_deck(conn, "Same")
    content_repo.create_deck(conn, "Same")
    assert len(content_repo.list_decks(conn)) == 2


def test_delete_cascades_to_notes_and_progress(conn):
    deck = content_repo.create_deck(conn, "Doomed")
    other = content_repo.create_deck(conn, "Safe")
    note_id = add_player_and_note(conn, deck.id)
    conn.execute("INSERT INTO card_state VALUES (?, 0, 1.0, 'sm2', '{}')", (note_id,))
    assert content_repo.delete_deck(conn, deck.id) is True
    assert content_repo.get_deck(conn, deck.id) is None
    assert conn.execute("SELECT COUNT(*) FROM note").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM card_state").fetchone()[0] == 0
    assert content_repo.get_deck(conn, other.id) is not None


def test_delete_missing_returns_false(conn):
    assert content_repo.delete_deck(conn, "nope") is False
