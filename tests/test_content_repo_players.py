import hashlib
import json
import uuid

import pytest

from dunzo import content_repo
from dunzo import player_files as pf

SCHEMA = {"type": "object", "properties": {"front": {"type": "string"}}, "required": ["front"]}
MULTI = {"index.html": b"<link href=css/a.css>", "css/a.css": b"a{}", "img/p.png": b"\x89PNG"}


def add_deck_and_note(conn, player_id):
    deck_id = str(uuid.uuid4())
    conn.execute("INSERT INTO deck (id, name) VALUES (?, 'd')", (deck_id,))
    conn.execute(
        "INSERT INTO note (id, deck_id, player_id, card_count, position, properties,"
        " created_at, updated_at) VALUES (?, ?, ?, 1, 1024, '{}', 0, 0)",
        (str(uuid.uuid4()), deck_id, player_id),
    )


def test_create_from_text_is_a_player_with_just_an_index(conn, settings):
    p = content_repo.create_player(conn, "  Basic   card ", "<p>hi</p>\r\n")
    assert p.name == "Basic card"
    data = b"<p>hi</p>\n"  # line endings normalized
    assert p.files_hash == pf.hash_files({"index.html": data})
    assert p.note_schema is None and p.note_count == 0
    uuid.UUID(p.id)
    assert content_repo.get_player(conn, p.id) == p
    assert content_repo.get_player(conn, "missing") is None
    digest = hashlib.sha256(data).hexdigest()
    assert content_repo.list_player_files(conn, p.id) == [
        content_repo.PlayerFile("index.html", digest, "text/html", len(data))
    ]
    assert (settings.media_dir / digest).read_bytes() == data


def test_create_with_several_files(conn, settings):
    p = content_repo.create_player(conn, "Multi", MULTI)
    files = content_repo.list_player_files(conn, p.id)
    assert [f.path for f in files] == ["css/a.css", "img/p.png", "index.html"]
    assert [f.mime for f in files] == ["text/css", "image/png", "text/html"]
    assert [f.size for f in files] == [3, 4, len(MULTI["index.html"])]
    for f in files:
        assert (settings.media_dir / f.hash).read_bytes() == MULTI[f.path]
    got = content_repo.get_player_file(conn, p.id, "css/a.css")
    assert got == files[0]
    assert content_repo.get_player_file(conn, p.id, "css/missing.css") is None
    assert content_repo.get_player_file(conn, "other", "css/a.css") is None
    assert p.files_hash == content_repo.hash_player_files(MULTI)


def test_same_bytes_are_stored_once(conn, settings):
    content_repo.create_player(conn, "a", {"index.html": b"same", "x.js": b"same"})
    content_repo.create_player(conn, "b", {"index.html": b"same"})
    assert len(list(settings.media_dir.iterdir())) == 1


def test_blank_name_or_files_rejected(conn):
    with pytest.raises(ValueError, match="name cannot be empty"):
        content_repo.create_player(conn, "  ", "x")
    with pytest.raises(ValueError, match="cannot be empty"):
        content_repo.create_player(conn, "p", "  \n ")
    with pytest.raises(ValueError, match="needs an index.html"):
        content_repo.create_player(conn, "p", {"other.html": b"x"})
    with pytest.raises(ValueError, match="not supported"):
        content_repo.create_player(conn, "p", {"index.html": b"x", "a.exe": b"x"})
    assert content_repo.list_players(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM player_file").fetchone()[0] == 0


def test_invalid_files_write_nothing(conn, settings):
    with pytest.raises(ValueError):
        content_repo.create_player(conn, "p", {"index.html": b"x", "dunzo/api.js": b"y"})
    assert not settings.media_dir.exists() or list(settings.media_dir.iterdir()) == []


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


def test_update_changes_name_and_schema_but_not_files(conn):
    p = content_repo.create_player(conn, "p", MULTI, json.dumps(SCHEMA))
    updated = content_repo.update_player(conn, p.id, "p2", "")
    assert updated.name == "p2" and updated.note_schema is None
    assert updated.files_hash == p.files_hash
    assert content_repo.list_player_files(conn, p.id)  # still there


def test_update_invalid_leaves_player_unchanged(conn):
    p = content_repo.create_player(conn, "p", "a")
    with pytest.raises(ValueError):
        content_repo.update_player(conn, p.id, "p", "{bad")
    with pytest.raises(ValueError):
        content_repo.update_player(conn, p.id, " ", None)
    assert content_repo.get_player(conn, p.id) == p


def test_update_missing_returns_none(conn):
    assert content_repo.update_player(conn, "nope", "p", None) is None


def test_replace_files(conn, settings):
    p = content_repo.create_player(conn, "p", MULTI)
    new = {"index.html": b"<b>new</b>", "app.js": b"var x;"}
    r = content_repo.replace_player_files(conn, p.id, new)
    assert r.files_hash == pf.hash_files(new) != p.files_hash
    assert [f.path for f in content_repo.list_player_files(conn, p.id)] == ["app.js", "index.html"]
    assert content_repo.get_player_file(conn, p.id, "css/a.css") is None
    f = content_repo.get_player_file(conn, p.id, "app.js")
    assert (settings.media_dir / f.hash).read_bytes() == b"var x;"
    assert content_repo.get_player(conn, p.id) == r


def test_replace_with_text_shorthand(conn):
    p = content_repo.create_player(conn, "p", MULTI)
    r = content_repo.replace_player_files(conn, p.id, "just html")
    assert [f.path for f in content_repo.list_player_files(conn, p.id)] == ["index.html"]
    assert r.files_hash != p.files_hash


def test_replace_invalid_keeps_the_current_files(conn):
    p = content_repo.create_player(conn, "p", MULTI)
    with pytest.raises(ValueError, match="needs an index.html"):
        content_repo.replace_player_files(conn, p.id, {"a.js": b"x"})
    assert content_repo.get_player(conn, p.id) == p
    assert len(content_repo.list_player_files(conn, p.id)) == 3


def test_replace_missing_returns_none(conn):
    assert content_repo.replace_player_files(conn, "nope", MULTI) is None


def test_delete_unused_player_removes_its_file_rows(conn, settings):
    p = content_repo.create_player(conn, "p", MULTI)
    assert content_repo.delete_player(conn, p.id) is True
    assert content_repo.get_player(conn, p.id) is None
    assert conn.execute("SELECT COUNT(*) FROM player_file").fetchone()[0] == 0
    assert len(list(settings.media_dir.iterdir())) == 3  # bytes stay: garbage collection is deferred


def test_delete_player_in_use_is_refused(conn):
    p = content_repo.create_player(conn, "p", "x")
    add_deck_and_note(conn, p.id)
    with pytest.raises(content_repo.PlayerInUse) as info:
        content_repo.delete_player(conn, p.id)
    assert info.value.note_count == 1
    assert content_repo.get_player(conn, p.id) is not None
    assert content_repo.list_player_files(conn, p.id)


def test_delete_missing_player_returns_false(conn):
    assert content_repo.delete_player(conn, "nope") is False
