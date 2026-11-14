import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect

PNG = b"\x89PNG fake image"


@pytest.fixture
def client(settings):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


@pytest.fixture
def db(client):
    c = connect()
    yield c
    c.commit()
    c.close()


def make(db, settings, name="Spanish/Verbs: 1"):
    deck = repo.create_deck(db, name)
    player = repo.create_player(db, "P", "// js")
    n1 = repo.create_note(db, deck.id, player.id, 1, {"front": "one"})
    n2 = repo.create_note(db, deck.id, player.id, 2, {"front": "two"})
    media = repo.add_media(db, settings.media_dir, io.BytesIO(PNG), "image/png")
    repo.link_note_media(db, n2.id, media.id)
    db.commit()
    return deck, (n1, n2), media


def manifest_of(content: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return json.loads(zf.read("manifest.json"))


def upload(client, content, name="p.zip"):
    return client.post("/packages/import", files={"file": (name, content, "application/zip")})


def counts(db, *tables):
    return [db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables]


# ------------------------------------------------------------------------------ page


def test_page_empty_state(client):
    r = client.get("/packages")
    assert r.status_code == 200
    assert "No decks to export yet" in r.text
    assert 'action="/packages/import"' in r.text and 'enctype="multipart/form-data"' in r.text
    assert 'href="/packages"' in r.text  # the nav link
    assert "Imported" not in r.text


def test_page_lists_decks_with_export_links(client, db, settings):
    deck, _, _ = make(db, settings)
    page = client.get("/packages").text
    assert 'href="/export"' in page and f'href="/decks/{deck.id}/export"' in page
    assert "Spanish/Verbs: 1" in page and "2 notes" in page


# ---------------------------------------------------------------------------- export


def test_export_one_deck(client, db, settings):
    deck, _, media = make(db, settings)
    r = client.get(f"/decks/{deck.id}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="dunzo-Spanish-Verbs-1.zip"' in r.headers["content-disposition"]
    m = manifest_of(r.content)
    assert [d["name"] for d in m["decks"]] == ["Spanish/Verbs: 1"]
    assert [n["properties"]["front"] for n in m["decks"][0]["notes"]] == ["one", "two"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.read(f"media/{media.id}") == PNG


def test_export_all_decks(client, db, settings):
    make(db, settings)
    repo.create_deck(db, "Empty")
    db.commit()
    m = manifest_of(client.get("/export").content)
    assert [d["name"] for d in m["decks"]] == ["Empty", "Spanish/Verbs: 1"]
    assert 'filename="dunzo-all-decks.zip"' in client.get("/export").headers["content-disposition"]


def test_export_unknown_deck_is_404(client):
    assert client.get("/decks/nope/export").status_code == 404


def test_export_with_missing_media_file_is_a_409(client, db, settings):
    deck, _, media = make(db, settings)
    (settings.media_dir / media.id).unlink()
    r = client.get(f"/decks/{deck.id}/export")
    assert r.status_code == 409 and "missing on disk" in r.json()["detail"]


def test_export_leaves_no_temporary_files(client, db, settings, monkeypatch, tmp_path):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    deck, _, media = make(db, settings)
    client.get(f"/decks/{deck.id}/export")
    (settings.media_dir / media.id).unlink()
    client.get(f"/decks/{deck.id}/export")  # the failing path cleans up too
    assert list((tmp_path / "tmp").iterdir()) == []


def test_export_never_includes_progress(client, db, settings):
    deck, (n1, _), _ = make(db, settings)
    db.execute("INSERT INTO card_state VALUES (?, 0, 424242.5, 'sm2', '{}')", (n1.id,))
    db.commit()
    r = client.get(f"/decks/{deck.id}/export")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert all(b"424242" not in zf.read(n) for n in zf.namelist())


# ---------------------------------------------------------------------------- import


def test_import_roundtrip_through_the_routes(client, db, settings):
    deck, _, _ = make(db, settings)
    package = client.get(f"/decks/{deck.id}/export").content
    r = upload(client, package)
    assert r.status_code == 303
    assert r.headers["location"] == (
        "/packages?decks=1&notes=2&players_added=0&players_reused=1&media=1"
    )
    page = client.get(r.headers["location"]).text
    assert "Imported 1 deck with 2 notes." in page
    assert "0 added, 1 already here" in page
    assert counts(db, "deck", "note", "player", "media") == [2, 4, 1, 1]
    copy = next(d for d in repo.list_decks(db) if d.id != deck.id)
    assert [n.properties["front"] for n in repo.list_notes(db, copy.id)] == ["one", "two"]


def test_import_results_are_not_shown_on_a_plain_visit(client):
    assert "Imported" not in client.get("/packages").text
    assert "Imported 0 decks" in client.get("/packages?decks=0").text


@pytest.mark.parametrize("content, message", [(b"not a zip", "not a valid zip")])
def test_bad_package_rerenders_with_an_error(client, db, content, message):
    r = upload(client, content)
    assert r.status_code == 422 and message in r.text and "<html" in r.text
    assert counts(db, "deck", "note", "player", "media") == [0, 0, 0, 0]


def test_no_file_chosen(client):
    r = client.post("/packages/import", data={})
    assert r.status_code == 422 and "Choose a package file" in r.text


def test_error_after_partial_writes_is_rolled_back(client, db):
    """A media file that does not match its hash is only noticed after other rows exist."""
    good = b"good bytes"
    import hashlib

    good_id = hashlib.sha256(good).hexdigest()
    bad_id = hashlib.sha256(b"claimed").hexdigest()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "format": "dunzo-package", "version": 1, "decks": [], "players": [],
            "media": [{"id": good_id, "mime": "image/png"}, {"id": bad_id, "mime": "image/png"}],
        }))
        zf.writestr(f"media/{good_id}", good)
        zf.writestr(f"media/{bad_id}", b"different")
    r = upload(client, buf.getvalue())
    assert r.status_code == 422 and "does not match its hash" in r.text
    assert counts(db, "media") == [0]  # the good file's row was rolled back too
