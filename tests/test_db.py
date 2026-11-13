import sqlite3
import uuid

import pytest

from dunzo.db import SCHEMA_VERSION, connect, init_db

CONTENT_TABLES = {"deck", "player", "note", "media", "note_media", "player_media"}
PROGRESS_TABLES = {"card_state", "review_log"}


def uid() -> str:
    return str(uuid.uuid4())


def add_deck(conn) -> str:
    deck_id = uid()
    conn.execute("INSERT INTO deck (id, name) VALUES (?, ?)", (deck_id, "d"))
    return deck_id


def add_player(conn) -> str:
    player_id = uid()
    conn.execute(
        "INSERT INTO player (id, name, source, source_hash) VALUES (?, 'p', 'x', 'h')",
        (player_id,),
    )
    return player_id


def add_note(conn, deck_id, player_id, position=1024, card_count=2) -> str:
    note_id = uid()
    conn.execute(
        "INSERT INTO note (id, deck_id, player_id, card_count, position, properties,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, '{}', 0, 0)",
        (note_id, deck_id, player_id, card_count, position),
    )
    return note_id


def tables(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows if not r["name"].startswith("sqlite_")}


def test_schema_has_all_tables(conn):
    assert tables(conn) == CONTENT_TABLES | PROGRESS_TABLES
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_init_db_is_idempotent(conn, settings):
    deck_id = add_deck(conn)
    conn.commit()
    init_db()
    init_db()
    assert conn.execute("SELECT id FROM deck").fetchone()["id"] == deck_id


def test_newer_schema_version_is_refused(settings):
    init_db()
    c = sqlite3.connect(settings.db_path)
    c.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    c.close()
    with pytest.raises(RuntimeError, match="newer"):
        init_db()


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add_note(conn, uid(), uid())


def test_content_never_references_progress(conn):
    """The core rule: no content table has a foreign key to a progress table."""
    for table in CONTENT_TABLES:
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
            assert fk["table"] not in PROGRESS_TABLES, f"{table} -> {fk['table']}"


def test_progress_references_only_content_with_cascade(conn):
    for table in PROGRESS_TABLES:
        fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        assert fks, f"{table} has no foreign keys"
        for fk in fks:
            assert fk["table"] in CONTENT_TABLES
            assert fk["on_delete"] == "CASCADE"


def test_note_position_unique_within_deck_only(conn):
    d1, d2, p = add_deck(conn), add_deck(conn), add_player(conn)
    add_note(conn, d1, p, position=1024)
    add_note(conn, d2, p, position=1024)  # same position, other deck: fine
    with pytest.raises(sqlite3.IntegrityError):
        add_note(conn, d1, p, position=1024)


def test_card_count_must_be_positive(conn):
    d, p = add_deck(conn), add_player(conn)
    with pytest.raises(sqlite3.IntegrityError):
        add_note(conn, d, p, card_count=0)


def test_invalid_json_is_rejected(conn):
    d, p = add_deck(conn), add_player(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO note (id, deck_id, player_id, card_count, position, properties,"
            " created_at, updated_at) VALUES (?, ?, ?, 1, 1024, 'not json', 0, 0)",
            (uid(), d, p),
        )


def test_deleting_deck_cascades_to_notes_and_progress(conn):
    d, p = add_deck(conn), add_player(conn)
    n = add_note(conn, d, p)
    conn.execute(
        "INSERT INTO card_state VALUES (?, 0, 100.0, 'sm2', '{}')",
        (n,),
    )
    conn.execute(
        "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, state_before,"
        " state_after) VALUES (?, 0, 50.0, 3, NULL, '{}')",
        (n,),
    )
    conn.execute("DELETE FROM deck WHERE id = ?", (d,))
    for table in ("note", "card_state", "review_log"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    # content that is not owned by the deck survives
    assert conn.execute("SELECT COUNT(*) FROM player").fetchone()[0] == 1


def test_player_in_use_cannot_be_deleted(conn):
    d, p = add_deck(conn), add_player(conn)
    add_note(conn, d, p)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM player WHERE id = ?", (p,))


def test_grade_must_be_1_to_4(conn):
    d, p = add_deck(conn), add_player(conn)
    n = add_note(conn, d, p)
    for bad in (0, 5):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, state_after)"
                " VALUES (?, 0, 1.0, ?, '{}')",
                (n, bad),
            )


def test_card_state_primary_key(conn):
    d, p = add_deck(conn), add_player(conn)
    n = add_note(conn, d, p)
    conn.execute("INSERT INTO card_state VALUES (?, 0, 1.0, 'sm2', '{}')", (n,))
    conn.execute("INSERT INTO card_state VALUES (?, 1, 1.0, 'sm2', '{}')", (n,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO card_state VALUES (?, 0, 2.0, 'sm2', '{}')", (n,))


def test_media_links_cascade_but_media_survives(conn):
    d, p = add_deck(conn), add_player(conn)
    n = add_note(conn, d, p)
    conn.execute("INSERT INTO media VALUES ('abc', 'image/png', 3)")
    conn.execute("INSERT INTO note_media VALUES (?, 'abc')", (n,))
    conn.execute("INSERT INTO player_media VALUES (?, 'abc')", (p,))
    conn.execute("DELETE FROM note WHERE id = ?", (n,))
    assert conn.execute("SELECT COUNT(*) FROM note_media").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1


def test_connect_enables_foreign_keys(settings):
    init_db()
    c = connect()
    try:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        c.close()
