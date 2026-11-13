import pytest
from fastapi.testclient import TestClient

from dunzo.app import create_app

HX = {"HX-Request": "true"}


@pytest.fixture
def client(settings):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def deck_id_from(html: str) -> str:
    return html.split('hx-delete="/decks/')[1].split('"')[0]


def test_root_redirects_to_decks(client):
    r = client.get("/")
    assert r.status_code == 307
    assert r.headers["location"] == "/decks"


def test_empty_state(client):
    r = client.get("/decks")
    assert r.status_code == 200
    assert "No decks yet" in r.text


def test_create_with_htmx_returns_updated_list(client):
    r = client.post("/decks", data={"name": "Biology"}, headers=HX)
    assert r.status_code == 200
    assert "Biology" in r.text
    assert "<html" not in r.text  # a fragment, not a full page
    assert "Biology" in client.get("/decks").text


def test_create_without_htmx_redirects(client):
    r = client.post("/decks", data={"name": "Chemistry"})
    assert r.status_code == 303
    assert r.headers["location"] == "/decks"
    assert "Chemistry" in client.get("/decks").text


def test_blank_name_htmx_gets_retargeted_error(client):
    r = client.post("/decks", data={"name": "   "}, headers=HX)
    assert r.status_code == 422
    assert r.headers["HX-Retarget"] == "#deck-form-error"
    assert "cannot be empty" in r.text
    assert "No decks yet" in client.get("/decks").text


def test_blank_name_without_htmx_rerenders_page_with_error(client):
    r = client.post("/decks", data={"name": ""})
    assert r.status_code == 422
    assert "cannot be empty" in r.text
    assert "<html" in r.text


def test_missing_name_field_is_a_validation_error_not_a_crash(client):
    r = client.post("/decks", data={}, headers=HX)
    assert r.status_code == 422


def test_names_are_html_escaped(client):
    client.post("/decks", data={"name": '<script>alert("x")</script>'}, headers=HX)
    page = client.get("/decks").text
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_delete_removes_deck_and_is_idempotent(client):
    client.post("/decks", data={"name": "Keep"}, headers=HX)
    r = client.post("/decks", data={"name": "Remove"}, headers=HX)
    # the row's delete button comes after its name
    target = deck_id_from(r.text.split(">Remove<", 1)[1])

    first = client.delete(f"/decks/{target}", headers=HX)
    assert first.status_code == 200
    assert "Remove" not in first.text
    assert "Keep" in first.text

    again = client.delete(f"/decks/{target}", headers=HX)
    assert again.status_code == 200
    assert "Keep" in again.text


def test_delete_last_deck_shows_empty_state(client):
    r = client.post("/decks", data={"name": "Only"}, headers=HX)
    r = client.delete(f"/decks/{deck_id_from(r.text)}", headers=HX)
    assert "No decks yet" in r.text


def test_delete_confirmation_mentions_notes_when_present(client, settings):
    import uuid

    from dunzo.db import connect

    r = client.post("/decks", data={"name": "Full"}, headers=HX)
    deck_id = deck_id_from(r.text)
    c = connect()
    pid, nid = str(uuid.uuid4()), str(uuid.uuid4())
    c.execute("INSERT INTO player (id, name, source, source_hash) VALUES (?, 'p', 'x', 'h')", (pid,))
    c.execute(
        "INSERT INTO note (id, deck_id, player_id, card_count, position, properties,"
        " created_at, updated_at) VALUES (?, ?, ?, 1, 1024, '{}', 0, 0)",
        (nid, deck_id, pid),
    )
    c.commit()
    c.close()
    page = client.get("/decks").text
    assert "1 note" in page
    assert "including any review progress" in page


def test_static_assets_served(client):
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
