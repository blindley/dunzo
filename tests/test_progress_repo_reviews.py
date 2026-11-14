import json
import sqlite3

import pytest

from dunzo import content_repo, progress_repo


def make_note(conn, card_count=3):
    deck = content_repo.create_deck(conn, "d")
    player = content_repo.create_player(conn, "p", "x")
    return content_repo.create_note(conn, deck.id, player.id, card_count, {})


def record(conn, note_id, **kw):
    args = dict(
        note_id=note_id, card_index=0, reviewed_at=10.0, grade=3, detail=None, scheduler="sm2",
        state_before=None, state_after={"interval": 1}, due=100.0,
    )
    args.update(kw)
    progress_repo.record_review(conn, **args)


def test_new_card_has_no_state(conn):
    n = make_note(conn)
    assert progress_repo.get_card_state(conn, n.id, 0) is None


def test_first_review_creates_state_and_log(conn):
    n = make_note(conn)
    record(conn, n.id, detail={"typed": "hola"})
    assert progress_repo.get_card_state(conn, n.id, 0) == progress_repo.CardState(
        n.id, 0, 100.0, "sm2", {"interval": 1}
    )
    assert progress_repo.get_card_state(conn, n.id, 1) is None  # other cards stay new
    log = conn.execute("SELECT * FROM review_log").fetchone()
    assert log["grade"] == 3 and log["reviewed_at"] == 10.0
    assert json.loads(log["detail"]) == {"typed": "hola"}
    assert log["state_before"] is None
    assert json.loads(log["state_after"]) == {"interval": 1}


def test_later_review_updates_the_row_and_appends_to_the_log(conn):
    n = make_note(conn)
    record(conn, n.id)
    record(
        conn, n.id, reviewed_at=20.0, grade=4, state_before={"interval": 1},
        state_after={"interval": 6}, due=500.0,
    )
    assert conn.execute("SELECT COUNT(*) FROM card_state").fetchone()[0] == 1
    assert progress_repo.get_card_state(conn, n.id, 0).due == 500.0
    rows = conn.execute("SELECT * FROM review_log ORDER BY id").fetchall()
    assert len(rows) == 2
    assert json.loads(rows[1]["state_before"]) == {"interval": 1}


def test_no_detail_is_stored_as_null(conn):
    n = make_note(conn)
    record(conn, n.id)
    assert conn.execute("SELECT detail FROM review_log").fetchone()[0] is None


def test_falsy_detail_is_kept(conn):
    n = make_note(conn)
    record(conn, n.id, detail=0)
    assert conn.execute("SELECT detail FROM review_log").fetchone()[0] == "0"


def test_invalid_grade_is_refused_by_the_database(conn):
    n = make_note(conn)
    with pytest.raises(sqlite3.IntegrityError):
        record(conn, n.id, grade=5)
