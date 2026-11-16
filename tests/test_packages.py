import ast
import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

import pytest

from dunzo import content_repo as repo
from dunzo import packages
from dunzo.db import connect, init_db

SCHEMA = json.dumps(
    {"type": "object", "required": ["front"], "properties": {"front": {"type": "string"}}}
)
PNG = b"\x89PNG fake image"
MP3 = b"fake audio"
BASIC_FILES = {
    "index.html": b"<!-- basic -->\n",
    "css/main.css": b"body {}\n",
    "player.js": b"// basic\n",
}


def png_id(data=PNG):
    return hashlib.sha256(data).hexdigest()


def add_media(conn, settings, data, mime="image/png"):
    return repo.add_media(conn, settings.media_dir, io.BytesIO(data), mime)


@pytest.fixture
def world(conn, settings):
    """Two decks, two players (one with several files), media on notes; one deck's notes reordered."""
    basic = repo.create_player(conn, "Basic", BASIC_FILES, SCHEMA)
    plain = repo.create_player(conn, "Plain", "<!-- plain -->")
    pic = add_media(conn, settings, PNG)
    sound = add_media(conn, settings, MP3, "audio/mpeg")
    spanish = repo.create_deck(conn, "Spanish")
    other = repo.create_deck(conn, "Other")
    a = repo.create_note(conn, spanish.id, basic.id, 2, {"front": "a", "n": 1})
    b = repo.create_note(conn, spanish.id, basic.id, 1, {"front": "ünï"})
    c = repo.create_note(conn, spanish.id, plain.id, 3, {"x": [1, {"y": None}]})
    repo.link_note_media(conn, a.id, pic.id)
    repo.link_note_media(conn, c.id, pic.id)
    repo.link_note_media(conn, b.id, sound.id)
    repo.move_note(conn, c.id, before_note_id=a.id)  # order is now c, a, b
    repo.create_note(conn, other.id, plain.id, 1, {"only": True})
    conn.commit()
    return {"spanish": spanish, "other": other, "basic": basic, "plain": plain, "pic": pic,
            "sound": sound, "notes": (a, b, c)}


def export(conn, settings, deck_ids) -> bytes:
    buf = io.BytesIO()
    packages.write_package(conn, settings.media_dir, deck_ids, buf)
    return buf.getvalue()


def manifest_of(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return json.loads(zf.read("manifest.json"))


@pytest.fixture
def second(tmp_path):
    """A second, empty installation: its own database file and media directory."""
    db_path, media_dir = tmp_path / "second.db", tmp_path / "second-media"
    init_db(db_path)
    c = connect(db_path)
    yield c, media_dir
    c.close()


def deck_notes(conn, name):
    deck = next(d for d in repo.list_decks(conn) if d.name == name)
    return deck, repo.list_notes(conn, deck.id)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def file_bytes(conn, media_dir, player_id) -> dict[str, bytes]:
    return {f.path: (media_dir / f.hash).read_bytes() for f in repo.list_player_files(conn, player_id)}


def build(manifest, files=None, raw_manifest=None):
    """A package zip from a manifest dict and extra members."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if raw_manifest is not None:
            zf.writestr("manifest.json", raw_manifest)
        elif manifest is not None:
            zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in (files or {}).items():
            zf.writestr(name, data)
    return io.BytesIO(buf.getvalue())


def base_manifest(**over):
    m = {"format": "dunzo-package", "version": packages.FORMAT_VERSION, "decks": [], "players": [],
         "media": []}
    m.update(over)
    return m


PID = str(uuid.uuid4())
PF = {f"players/{PID}/index.html": "x"}  # the one file of the default test player


def one_player(schema=None, files=("index.html",)):
    return {"id": PID, "name": "P", "note_schema": schema, "files": list(files)}


def one_deck(*notes, name="D"):
    return {"name": name, "notes": list(notes)}


def note(properties=None, count=1, player_id=PID, media=()):
    return {"player_id": player_id, "card_count": count, "properties": properties or {},
            "media": list(media)}


def do_import(conn, media_dir, package):
    return packages.import_package(conn, media_dir, package)


# ---------------------------------------------------------------------- the export


def test_package_layout(conn, settings, world):
    data = export(conn, settings, [world["spanish"].id])
    basic, plain = world["basic"].id, world["plain"].id
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert set(zf.namelist()) == {
            "manifest.json",
            f"players/{basic}/index.html",
            f"players/{basic}/css/main.css",
            f"players/{basic}/player.js",
            f"players/{plain}/index.html",
            f"media/{world['pic'].id}",
            f"media/{world['sound'].id}",
        }
        assert zf.read(f"media/{world['pic'].id}") == PNG
        for path, content in BASIC_FILES.items():
            assert zf.read(f"players/{basic}/{path}") == content
    m = manifest_of(data)
    assert (m["format"], m["version"]) == ("dunzo-package", 2)
    assert [d["name"] for d in m["decks"]] == ["Spanish"]
    players = {p["name"]: p for p in m["players"]}
    assert set(players) == {"Basic", "Plain"}
    assert players["Basic"]["note_schema"] == json.loads(SCHEMA)
    assert players["Basic"]["files"] == ["css/main.css", "index.html", "player.js"]
    assert players["Plain"]["files"] == ["index.html"]
    assert {x["id"] for x in m["media"]} == {world["pic"].id, world["sound"].id}


def test_notes_are_an_ordered_list_without_positions(conn, settings, world):
    m = manifest_of(export(conn, settings, [world["spanish"].id]))
    notes = m["decks"][0]["notes"]
    assert [n["properties"].get("front", "x") for n in notes] == ["x", "a", "ünï"]  # c, a, b
    assert all("position" not in n for n in notes)
    assert notes[1]["media"] == [world["pic"].id]


def test_only_requested_decks_and_what_they_use(conn, settings, world):
    m = manifest_of(export(conn, settings, [world["other"].id]))
    assert [d["name"] for d in m["decks"]] == ["Other"]
    assert [p["name"] for p in m["players"]] == ["Plain"]
    assert m["media"] == []


def test_deck_ids_are_exported_once_in_the_given_order(conn, settings, world):
    ids = [world["spanish"].id, world["other"].id, world["spanish"].id]
    m = manifest_of(export(conn, settings, ids))
    assert [d["name"] for d in m["decks"]] == ["Spanish", "Other"]


def test_export_errors(conn, settings, world):
    with pytest.raises(packages.PackageError, match="Deck not found"):
        export(conn, settings, ["nope"])
    (settings.media_dir / world["pic"].id).unlink()
    with pytest.raises(packages.PackageError, match="media file .* missing on disk"):
        export(conn, settings, [world["spanish"].id])


def test_export_with_a_missing_player_file(conn, settings, world):
    entry = repo.get_player_file(conn, world["basic"].id, "player.js")
    (settings.media_dir / entry.hash).unlink()
    with pytest.raises(packages.PackageError, match="player.js.* missing on disk"):
        export(conn, settings, [world["spanish"].id])


def test_export_never_contains_progress(conn, settings, world):
    a = world["notes"][0]
    marker = "SECRET-PROGRESS-MARKER"
    conn.execute(
        "INSERT INTO card_state VALUES (?, 0, 123456789.5, 'sm2', ?)",
        (a.id, json.dumps({"ease": 2.5, "marker": marker})),
    )
    conn.execute(
        "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, detail, state_after)"
        " VALUES (?, 0, 987654321.5, 3, ?, '{}')",
        (a.id, json.dumps({"typed": marker})),
    )
    conn.commit()
    data = export(conn, settings, [d.id for d in repo.list_decks(conn)])
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            blob = zf.read(name)
            assert marker.encode() not in blob, name
            assert b"987654321" not in blob and b"123456789" not in blob, name
    m = manifest_of(data)
    assert set(m) == {"format", "version", "decks", "players", "media"}
    note_keys = {k for d in m["decks"] for n in d["notes"] for k in n}
    assert note_keys == {"id", "player_id", "card_count", "properties", "media"}


def test_packages_module_uses_only_content_repo():
    tree = ast.parse(Path(packages.__file__).read_text())
    dunzo_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "dunzo":
            dunzo_imports |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dunzo."):
            dunzo_imports.add(node.module)
        elif isinstance(node, ast.Import):
            dunzo_imports |= {a.name for a in node.names if a.name.startswith("dunzo")}
    assert dunzo_imports == {"content_repo"}


# ------------------------------------------------------------------ import: round trip


def test_import_into_a_fresh_installation_preserves_everything(conn, settings, world, second):
    data = export(conn, settings, [d.id for d in repo.list_decks(conn)])
    c2, media2 = second
    result = do_import(c2, media2, io.BytesIO(data))
    c2.commit()
    assert result == packages.ImportResult(
        decks=2, notes=4, players_added=2, players_reused=0, media=2
    )

    deck, notes = deck_notes(c2, "Spanish")
    assert [n.properties for n in notes] == [
        {"x": [1, {"y": None}]}, {"front": "a", "n": 1}, {"front": "ünï"},
    ]  # order preserved
    assert [n.card_count for n in notes] == [3, 2, 1]
    assert [n.position for n in notes] == [1024, 2048, 3072]
    assert [n.player_name for n in notes] == ["Plain", "Basic", "Basic"]
    assert {n.id for n in notes}.isdisjoint({x.id for x in world["notes"]})  # new ids
    assert deck.id != world["spanish"].id

    assert [m.id for m in repo.list_note_media(c2, notes[1].id)] == [world["pic"].id]
    assert [m.id for m in repo.list_note_media(c2, notes[0].id)] == [world["pic"].id]
    assert [m.id for m in repo.list_note_media(c2, notes[2].id)] == [world["sound"].id]
    assert (media2 / world["pic"].id).read_bytes() == PNG

    basic = repo.get_player(c2, world["basic"].id)  # players keep their identity
    assert file_bytes(c2, media2, basic.id) == BASIC_FILES
    assert basic.files_hash == world["basic"].files_hash
    assert basic.note_schema == world["basic"].note_schema
    assert file_bytes(c2, media2, world["plain"].id) == {"index.html": b"<!-- plain -->"}


def test_import_creates_no_progress(conn, settings, world, second):
    a = world["notes"][0]
    conn.execute("INSERT INTO card_state VALUES (?, 0, 1.0, 'sm2', '{}')", (a.id,))
    conn.execute(
        "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, state_after)"
        " VALUES (?, 0, 1.0, 3, '{}')", (a.id,),
    )
    conn.commit()
    c2, media2 = second
    do_import(c2, media2, io.BytesIO(export(conn, settings, [world["spanish"].id])))
    assert count(c2, "note") == 3
    assert count(c2, "card_state") == 0 and count(c2, "review_log") == 0


def test_importing_twice_copies_decks_but_reuses_players_and_media(conn, settings, world, second):
    c2, media2 = second
    data = export(conn, settings, [world["spanish"].id])
    do_import(c2, media2, io.BytesIO(data))
    result = do_import(c2, media2, io.BytesIO(data))
    assert result.players_added == 0 and result.players_reused == 2
    assert count(c2, "deck") == 2 and count(c2, "note") == 6
    assert count(c2, "player") == 2 and count(c2, "media") == 2
    assert count(c2, "player_file") == 4  # three files of Basic, one of Plain
    assert len(list(media2.iterdir())) == 2 + 4  # two media files and four distinct player files


def test_importing_into_the_same_installation(conn, settings, world):
    result = do_import(conn, settings.media_dir, io.BytesIO(export(conn, settings, [world["other"].id])))
    assert result.players_reused == 1 and result.players_added == 0
    assert [d.name for d in repo.list_decks(conn)] == ["Other", "Other", "Spanish"]
    assert count(conn, "player") == 2


def test_same_player_id_with_different_files_is_added_as_a_copy(conn, settings, world, second):
    c2, media2 = second
    data = export(conn, settings, [world["other"].id])
    existing = repo.create_player(
        c2, "Mine", "<!-- my own page -->", None, player_id=world["plain"].id, media_dir=media2
    )
    c2.commit()
    result = do_import(c2, media2, io.BytesIO(data))
    assert result.players_added == 1 and result.players_reused == 0
    assert repo.get_player(c2, existing.id) == existing  # untouched
    assert file_bytes(c2, media2, existing.id) == {"index.html": b"<!-- my own page -->"}
    _, notes = deck_notes(c2, "Other")
    assert notes[0].player_id != existing.id
    assert file_bytes(c2, media2, notes[0].player_id) == {"index.html": b"<!-- plain -->"}
    assert count(c2, "player") == 2


def test_same_player_id_with_one_file_changed_is_also_a_copy(conn, settings, world, second):
    c2, media2 = second
    changed = {**BASIC_FILES, "player.js": b"// edited\n"}
    repo.create_player(c2, "Basic", changed, SCHEMA, player_id=world["basic"].id, media_dir=media2)
    do_import(c2, media2, io.BytesIO(export(conn, settings, [world["spanish"].id])))
    assert count(c2, "player") == 3


def test_same_player_id_with_different_schema_is_also_a_copy(conn, settings, world, second):
    c2, media2 = second
    repo.create_player(c2, "Basic", BASIC_FILES, None, player_id=world["basic"].id, media_dir=media2)  # no schema
    do_import(c2, media2, io.BytesIO(export(conn, settings, [world["spanish"].id])))
    assert count(c2, "player") == 3


def test_empty_package_imports_nothing(conn, settings, second):
    c2, media2 = second
    assert do_import(c2, media2, io.BytesIO(export(conn, settings, []))) == packages.ImportResult(0, 0, 0, 0, 0)


def test_player_files_are_kept_byte_for_byte(second):
    c2, media2 = second
    raw = b"a\r\nb\x00\xff"
    package = build(base_manifest(players=[one_player()], decks=[one_deck(note())]),
                    {f"players/{PID}/index.html": raw})
    do_import(c2, media2, package)
    assert file_bytes(c2, media2, PID) == {"index.html": raw}


def test_create_player_with_explicit_id(conn):
    pid = str(uuid.uuid4())
    assert repo.create_player(conn, "p", "x", None, player_id=pid).id == pid
    assert repo.get_player(conn, pid) is not None


# ------------------------------------------------------------------ import: bad input


def failing(conn, media_dir, package, message):
    with pytest.raises(packages.PackageError, match=message):
        do_import(conn, media_dir, package)
    conn.rollback()
    for table in ("deck", "note", "player", "player_file", "media"):
        assert count(conn, table) == 0, table


def test_not_a_zip(second):
    c2, media2 = second
    failing(c2, media2, io.BytesIO(b"definitely not a zip"), "not a valid zip")


def test_missing_or_bad_manifest(second):
    c2, media2 = second
    failing(c2, media2, build(None, {"other.txt": "x"}), "no manifest.json")
    failing(c2, media2, build(None, raw_manifest="{bad"), "not valid JSON")
    failing(c2, media2, build(None, raw_manifest=b"\xff\xfe"), "not valid JSON")
    failing(c2, media2, build(None, raw_manifest="[]"), "must be an object")


def test_wrong_format_and_versions(second):
    c2, media2 = second
    failing(c2, media2, build({"format": "other", "version": 2}), "not a dunzo package")
    failing(c2, media2, build(base_manifest(version=3)), "version 3")
    failing(c2, media2, build(base_manifest(version=1)), "no longer reads")
    failing(c2, media2, build(base_manifest(version="2")), "no valid version")
    failing(c2, media2, build(base_manifest(version=True)), "no valid version")


def test_oversized_manifest_is_refused(second, monkeypatch):
    c2, media2 = second
    monkeypatch.setattr(packages, "MAX_MANIFEST_BYTES", 10)
    failing(c2, media2, build(base_manifest()), "too large")


def test_package_size_and_member_limits(second, monkeypatch):
    c2, media2 = second
    monkeypatch.setattr(packages, "MAX_PACKAGE_BYTES", 10)
    failing(c2, media2, build(base_manifest()), "too large")
    monkeypatch.setattr(packages, "MAX_PACKAGE_BYTES", 10**9)
    monkeypatch.setattr(packages, "MAX_MEMBERS", 0)
    failing(c2, media2, build(base_manifest()), "too many files")


@pytest.mark.parametrize(
    "manifest, files, message",
    [
        (base_manifest(decks=[{"name": "  ", "notes": []}]), {}, "Deck 1: Deck name cannot be empty"),
        (base_manifest(decks="x"), {}, "decks must be a list"),
        (base_manifest(decks=[5]), {}, "Deck 1 must be an object"),
        (base_manifest(decks=[{"notes": []}]), {}, "“name” must be text"),
        (base_manifest(players=[{"id": "nope", "name": "P"}]), {}, "must be a UUID"),
        (base_manifest(players=[{"id": PID.upper(), "name": "P"}]), {}, "lower-case UUID"),
        (base_manifest(players=[{"id": PID, "name": "P"}]), {}, "needs an index.html"),
        (base_manifest(players=[one_player(), one_player()]), PF, "listed twice"),
        (base_manifest(players=[one_player()]), {f"players/{PID}/index.html": ""}, "is empty"),
        (base_manifest(players=[one_player(files=("index.html", "index.html"))]), PF, "listed twice"),
        (base_manifest(players=[one_player(files=("index.html", "gone.js"))]), PF, "no file “gone.js”"),
        (base_manifest(players=[one_player(files=("index.html", "../x.js"))]), PF, "not a valid file path"),
        (base_manifest(players=[one_player(files=("index.html", "/abs.js"))]), PF, "not a valid file path"),
        (base_manifest(players=[one_player(files=("index.html", "dunzo/api.js"))]),
         {**PF, f"players/{PID}/dunzo/api.js": "evil"}, "reserved"),
        (base_manifest(players=[one_player(files=("index.html", "run.exe"))]),
         {**PF, f"players/{PID}/run.exe": "x"}, "not supported"),
        (base_manifest(players=[one_player(files=("a.js",))]), {f"players/{PID}/a.js": "x"}, "needs an index.html"),
        (base_manifest(players=[{**one_player(), "files": "index.html"}]), PF, "files must be a list"),
        (base_manifest(players=[one_player(files=(5,))]), PF, "must be text"),
        (base_manifest(players=[one_player(schema=[1])]), PF, "object or null"),
        (base_manifest(players=[one_player(schema={"type": "nonsense"})]), PF, "not a valid JSON Schema"),
        (base_manifest(players=[{"id": PID, "name": " ", "files": ["index.html"]}]), PF, "name cannot be empty"),
        (base_manifest(decks=[one_deck(note())]), {}, "its player is not in the package"),
        (base_manifest(decks=[one_deck({"player_id": 5})]), {}, "its player is not in the package"),
        (base_manifest(decks=[one_deck(note(player_id=str(uuid.uuid4())))],
                       players=[one_player()]), PF, "its player is not"),
        (base_manifest(decks=[one_deck(note(count=0))], players=[one_player()]),
         PF, "Deck “D”, note 1: Number of cards"),
        (base_manifest(decks=[one_deck(note(count="2"))], players=[one_player()]), PF, "between 1 and"),
        (base_manifest(decks=[one_deck(note(properties={}))], players=[one_player(schema=json.loads(SCHEMA))]),
         PF, "note 1: .*required"),
        (base_manifest(decks=[one_deck({"player_id": PID, "card_count": 1, "properties": [1]})],
                       players=[one_player()]), PF, "must be a JSON object"),
    ],
)
def test_invalid_manifests_write_nothing(second, manifest, files, message):
    c2, media2 = second
    failing(c2, media2, build(manifest, files), message)


def test_one_bad_note_stops_the_whole_import(second):
    c2, media2 = second
    manifest = base_manifest(
        players=[one_player(schema=json.loads(SCHEMA))],
        decks=[one_deck(note({"front": "ok"}), name="Good"), one_deck(note({}), name="Bad")],
    )
    failing(c2, media2, build(manifest, PF), "Deck “Bad”, note 1")
    assert count(c2, "player") == 0  # validated before anything was written
    assert not media2.exists() or list(media2.iterdir()) == []  # and no bytes were stored


def test_an_oversized_player_file_is_refused(second, monkeypatch):
    c2, media2 = second
    monkeypatch.setattr(repo, "MAX_PLAYER_FILE_BYTES", 3)
    failing(c2, media2, build(base_manifest(players=[one_player()]), {f"players/{PID}/index.html": "1234"}),
            "too large")


MID = png_id()


@pytest.mark.parametrize(
    "manifest, files, message",
    [
        (base_manifest(media=[{"id": "short", "mime": "image/png"}]), {}, "sha256 hash"),
        (base_manifest(media=[{"id": MID, "mime": "text/html"}]), {f"media/{MID}": PNG}, "not supported"),
        (base_manifest(media=[{"id": MID, "mime": "image/png"}]), {}, "is missing"),
        (base_manifest(media=[{"id": MID, "mime": "image/png"}] * 2), {f"media/{MID}": PNG}, "listed twice"),
        (base_manifest(players=[one_player()], decks=[one_deck(note(media=[MID]))]), PF, "package lacks"),
        (base_manifest(players=[one_player()], decks=[one_deck(note(media=["zz"]))]), PF, "sha256 hashes"),
    ],
)
def test_bad_media_declarations(second, manifest, files, message):
    c2, media2 = second
    failing(c2, media2, build(manifest, files), message)


def test_media_that_does_not_match_its_hash_is_refused_and_rolled_back(second):
    c2, media2 = second
    manifest = base_manifest(media=[{"id": MID, "mime": "image/png"}])
    failing(c2, media2, build(manifest, {f"media/{MID}": b"other bytes"}), "does not match its hash")


def test_empty_media_file_is_refused(second):
    c2, media2 = second
    manifest = base_manifest(media=[{"id": MID, "mime": "image/png"}])
    failing(c2, media2, build(manifest, {f"media/{MID}": b""}), "empty")


def test_member_names_are_never_used_as_paths(second, tmp_path):
    c2, media2 = second
    package = build(
        base_manifest(),
        {"../evil.txt": "x", "/abs/evil.txt": "x", "media/../../evil2": "x", "extra/file": "x",
         f"players/{PID}/../../evil3.js": "x"},
    )
    do_import(c2, media2, package)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path.parent / "evil.txt").exists()
    assert not media2.exists() or list(media2.iterdir()) == []


def test_unreferenced_extra_files_are_ignored(second):
    c2, media2 = second
    manifest = base_manifest(players=[one_player()], decks=[one_deck(note())])
    do_import(c2, media2, build(manifest, {**PF, "readme.txt": "hi", f"players/{PID}/unlisted.js": "x"}))
    assert count(c2, "note") == 1
    assert file_bytes(c2, media2, PID) == {"index.html": b"x"}
