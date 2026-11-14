import uuid

from dunzo import content_repo, progress_repo


def make_note(conn, card_count=5):
    deck = content_repo.create_deck(conn, "d")
    player = content_repo.create_player(conn, "p", "x")
    return content_repo.create_note(conn, deck.id, player.id, card_count, {})


def add_progress(conn, note_id, index):
    conn.execute("INSERT INTO card_state VALUES (?, ?, 1.0, 'sm2', '{}')", (note_id, index))
    conn.execute(
        "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, state_after)"
        " VALUES (?, ?, 1.0, 3, '{}')",
        (note_id, index),
    )


def test_count_and_delete_from_index(conn):
    note = make_note(conn)
    for i in range(4):
        add_progress(conn, note.id, i)
    conn.execute(  # a second review of card 3: another log row, same card_state row
        "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, state_after)"
        " VALUES (?, 3, 2.0, 3, '{}')",
        (note.id,),
    )
    count = progress_repo.count_progress_from(conn, note.id, 2)
    assert (count.card_states, count.reviews) == (2, 3)
    assert count
    progress_repo.delete_progress_from(conn, note.id, 2)
    assert progress_repo.count_progress_from(conn, note.id, 2) == progress_repo.ProgressCount(0, 0)
    assert not progress_repo.count_progress_from(conn, note.id, 2)
    assert progress_repo.count_progress_from(conn, note.id, 0).card_states == 2  # 0 and 1 remain


def test_other_notes_are_untouched(conn):
    a, b = make_note(conn), make_note(conn)
    add_progress(conn, a.id, 4)
    add_progress(conn, b.id, 4)
    progress_repo.delete_progress_from(conn, a.id, 0)
    assert progress_repo.count_progress_from(conn, a.id, 0).card_states == 0
    assert progress_repo.count_progress_from(conn, b.id, 0).card_states == 1
    assert uuid.UUID(b.id)
