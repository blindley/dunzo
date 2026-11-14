import hashlib
import json
import uuid

import pytest

from dunzo import content_repo

SCHEMA = {"type": "object", "properties": {"front": {"type": "string"}}, "required": ["front"]}


def add_deck_and_note(conn, player_id):
    deck_id = str(uuid.uuid4())
    conn.execute("INSERT INTO deck (id, name) VALUES (?, 'd')", (deck_id,))
    conn.execute(
        "INSERT INTO note (id, deck_id, player_id, card_count, position, properties,"
        " created_at, updated_at) VALUES (?, ?, ?, 1, 1024, '{}', 0, 0)",
        (str(uuid.uuid4()), deck_id, player_id),
    )


def test_create_and_get(conn):
    p = content_repo.create_player(conn, "  Basic   card ", "console.log(1)\r\n")
    assert p.name == "Basic card"
    assert p.source == "console.log(1)\n"  # line endings normalized
    assert p.source_hash == hashlib.sha256(b"console.log(1)\n").hexdigest()
    assert p.note_schema is None
    assert p.note_count == 0
    uuid.UUID(p.id)
    assert content_repo.get_player(conn, p.id) == p
    assert content_repo.get_player(conn, "missing") is None


def test_blank_name_or_source_rejected(conn):
    with pytest.raises(ValueError, match="name cannot be empty"):
        content_repo.create_player(conn, "  ", "x")
    with pytest.raises(ValueError, match="source cannot be empty"):
        content_repo.create_player(conn, "p", "  \n ")
    assert content_repo.list_players(conn) == []


def test_oversized_source_rejected(conn):
    with pytest.raises(ValueError, match="larger"):
        content_repo.create_player(conn, "p", "x" * (content_repo.MAX_PLAYER_SOURCE_BYTES + 1))


def test_schema_is_validated_and_pretty_printed(conn):
    p = content_repo.create_player(conn, "p", "x", json.dumps(SCHEMA))
    assert json.loads(p.note_schema) == SCHEMA
    assert "\n" in p.note_schema


@pytest.mark.parametrize("blank", [None, "", "  \n"])
def test_blank_schema_means_none(conn, blank):
    assert content_repo.create_player(conn, "p", "x", blank).note_schema is None


@pytest.mark.parametrize(
    "bad, message",
    [
        ("{not json", "not valid JSON"),
        ("[1, 2]", "must be a JSON object"),
        ('"text"', "must be a JSON object"),
        ('{"type": "nonsense"}', "not a valid JSON Schema"),
    ],
)
def test_bad_schema_rejected(conn, bad, message):
    with pytest.raises(ValueError, match=message):
        content_repo.create_player(conn, "p", "x", bad)
    assert content_repo.list_players(conn) == []


def test_list_sorted_with_note_counts(conn):
    b = content_repo.create_player(conn, "banana", "x")
    content_repo.create_player(conn, "Apple", "x")
    add_deck_and_note(conn, b.id)
    players = content_repo.list_players(conn)
    assert [p.name for p in players] == ["Apple", "banana"]
    assert [p.note_count for p in players] == [0, 1]


def test_update_changes_hash_and_schema(conn):
    p = content_repo.create_player(conn, "p", "a", json.dumps(SCHEMA))
    updated = content_repo.update_player(conn, p.id, "p2", "b", "")
    assert updated.name == "p2"
    assert updated.source == "b"
    assert updated.source_hash == hashlib.sha256(b"b").hexdigest()
    assert updated.source_hash != p.source_hash
    assert updated.note_schema is None


def test_update_invalid_leaves_player_unchanged(conn):
    p = content_repo.create_player(conn, "p", "a")
    with pytest.raises(ValueError):
        content_repo.update_player(conn, p.id, "p", "a", "{bad")
    assert content_repo.get_player(conn, p.id) == p


def test_update_missing_returns_none(conn):
    assert content_repo.update_player(conn, "nope", "p", "a", None) is None


def test_delete_unused_player_and_its_links_but_not_media(conn, tmp_path):
    p = content_repo.create_player(conn, "p", "x")
    media = content_repo.add_media(conn, tmp_path / "m", _bytes(b"abc"), "image/png")
    content_repo.link_player_media(conn, p.id, media.id)
    assert content_repo.delete_player(conn, p.id) is True
    assert content_repo.get_player(conn, p.id) is None
    assert conn.execute("SELECT COUNT(*) FROM player_media").fetchone()[0] == 0
    assert content_repo.get_media(conn, media.id) is not None


def test_delete_player_in_use_is_refused(conn):
    p = content_repo.create_player(conn, "p", "x")
    add_deck_and_note(conn, p.id)
    with pytest.raises(content_repo.PlayerInUse) as info:
        content_repo.delete_player(conn, p.id)
    assert info.value.note_count == 1
    assert content_repo.get_player(conn, p.id) is not None


def test_delete_missing_player_returns_false(conn):
    assert content_repo.delete_player(conn, "nope") is False


def _bytes(data: bytes):
    import io

    return io.BytesIO(data)
