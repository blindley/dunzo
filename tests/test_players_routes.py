import json
import uuid

import pytest
from fastapi.testclient import TestClient

from dunzo.app import create_app
from dunzo.db import connect

HX = {"HX-Request": "true"}
PNG = ("pic.png", b"\x89PNG\r\n fake", "image/png")


@pytest.fixture
def client(settings):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def create(client, name="Basic", source="// js", schema=""):
    r = client.post("/players", data={"name": name, "source": source, "note_schema": schema})
    assert r.status_code == 303, r.text
    return r.headers["location"].split("?")[0].rsplit("/", 1)[1]


def use_in_note(player_id):
    c = connect()
    deck = str(uuid.uuid4())
    c.execute("INSERT INTO deck (id, name) VALUES (?, 'd')", (deck,))
    c.execute(
        "INSERT INTO note (id, deck_id, player_id, card_count, position, properties,"
        " created_at, updated_at) VALUES (?, ?, ?, 1, 1024, '{}', 0, 0)",
        (str(uuid.uuid4()), deck, player_id),
    )
    c.commit()
    c.close()


def test_list_empty_state_and_nav(client):
    r = client.get("/players")
    assert r.status_code == 200
    assert "No players yet" in r.text
    assert 'href="/players"' in r.text


def test_new_page_renders(client):
    r = client.get("/players/new")
    assert r.status_code == 200
    assert 'name="source"' in r.text
    assert "Media" not in r.text  # media needs a saved player


def test_create_redirects_to_edit_page_and_lists(client):
    pid = create(client, name="Typing", source="// typing player")
    page = client.get(f"/players/{pid}?saved=1")
    assert page.status_code == 200
    assert "Saved." in page.text
    assert "// typing player" in page.text
    assert "Typing" in client.get("/players").text


@pytest.mark.parametrize(
    "data, message",
    [
        ({"name": "", "source": "x"}, "name cannot be empty"),
        ({"name": "p", "source": " "}, "source cannot be empty"),
        ({"name": "p", "source": "x", "note_schema": "{bad"}, "not valid JSON"),
        ({"name": "p", "source": "x", "note_schema": '{"type": "nope"}'}, "not a valid JSON Schema"),
    ],
)
def test_create_validation_errors_rerender_form_with_input(client, data, message):
    r = client.post("/players", data=data)
    assert r.status_code == 422
    assert message in r.text
    assert "<html" in r.text
    assert client.get("/players").text.count('class="deck-row"') == 0


def test_error_keeps_what_the_user_typed(client):
    r = client.post("/players", data={"name": "Keepme", "source": "let a = 1;", "note_schema": "{bad"})
    assert "Keepme" in r.text
    assert "let a = 1;" in r.text
    assert "{bad" in r.text


def test_update_saves_and_validates(client):
    pid = create(client)
    schema = json.dumps({"type": "object"})
    r = client.post(
        f"/players/{pid}", data={"name": "Renamed", "source": "// v2", "note_schema": schema}
    )
    assert r.status_code == 303
    page = client.get(f"/players/{pid}").text
    assert "Renamed" in page and "// v2" in page
    assert "&#34;type&#34;: &#34;object&#34;" in page  # the schema, HTML-escaped in the textarea

    bad = client.post(f"/players/{pid}", data={"name": "X", "source": "", "note_schema": ""})
    assert bad.status_code == 422
    assert "// v2" in client.get(f"/players/{pid}").text  # unchanged


def test_unknown_player_is_404(client):
    assert client.get("/players/nope").status_code == 404
    assert client.post("/players/nope", data={"name": "a", "source": "b"}).status_code == 404
    assert client.post("/players/nope/media", files={"file": PNG}).status_code == 404


def test_names_are_html_escaped(client):
    create(client, name="<b>bold</b>", source="</textarea><script>alert(1)</script>")
    page = client.get("/players").text
    assert "<b>bold</b>" not in page
    pid = page.split('href="/players/')[1].split('"')[0]
    edit = client.get(f"/players/{pid}").text
    assert "<script>alert(1)</script>" not in edit


def test_delete_unused_player(client):
    pid = create(client, name="Gone")
    r = client.delete(f"/players/{pid}", headers=HX)
    assert r.status_code == 200
    assert "No players yet" in r.text
    again = client.delete(f"/players/{pid}", headers=HX)  # idempotent
    assert again.status_code == 200


def test_delete_player_in_use_is_refused_with_retargeted_error(client):
    pid = create(client, name="Busy")
    use_in_note(pid)
    r = client.delete(f"/players/{pid}", headers=HX)
    assert r.status_code == 409
    assert r.headers["HX-Retarget"] == "#player-list-error"
    assert "1 note still use it" in r.text
    page = client.get("/players").text
    assert "Busy" in page
    assert "disabled" in page  # the button is disabled while the player is in use


# ------------------------------------------------------------------ media on a player


def test_upload_with_htmx_returns_media_list_and_serves_file(client):
    pid = create(client)
    r = client.post(f"/players/{pid}/media", files={"file": PNG}, headers=HX)
    assert r.status_code == 200
    assert "image/png" in r.text
    assert "<html" not in r.text
    media_id = r.text.split('href="/media/')[1].split('"')[0]
    served = client.get(f"/media/{media_id}")
    assert served.status_code == 200
    assert served.content == PNG[1]
    assert served.headers["content-type"] == "image/png"
    assert "/media/" + media_id in client.get(f"/players/{pid}").text


def test_upload_without_htmx_redirects(client):
    pid = create(client)
    r = client.post(f"/players/{pid}/media", files={"file": PNG})
    assert r.status_code == 303
    assert r.headers["location"] == f"/players/{pid}"
    assert "image/png" in client.get(f"/players/{pid}").text


def test_upload_rejected_type_htmx(client):
    pid = create(client)
    r = client.post(
        f"/players/{pid}/media", files={"file": ("a.html", b"<script>", "text/html")}, headers=HX
    )
    assert r.status_code == 422
    assert r.headers["HX-Retarget"] == "#media-form-error"
    assert "not supported" in r.text
    assert "No media attached" in client.get(f"/players/{pid}").text


def test_upload_rejected_type_without_htmx_rerenders_page(client):
    pid = create(client)
    r = client.post(f"/players/{pid}/media", files={"file": ("a.js", b"x", "text/javascript")})
    assert r.status_code == 422
    assert "not supported" in r.text
    assert "<html" in r.text


def test_upload_without_choosing_a_file(client):
    pid = create(client)
    r = client.post(f"/players/{pid}/media", data={}, headers=HX)
    assert r.status_code == 422
    assert "Choose a file" in r.text


def test_unlink_media_keeps_it_servable(client):
    pid = create(client)
    r = client.post(f"/players/{pid}/media", files={"file": PNG}, headers=HX)
    media_id = r.text.split('href="/media/')[1].split('"')[0]
    gone = client.delete(f"/players/{pid}/media/{media_id}", headers=HX)
    assert gone.status_code == 200
    assert "No media attached" in gone.text
    assert client.delete(f"/players/{pid}/media/{media_id}", headers=HX).status_code == 200
    assert client.get(f"/media/{media_id}").status_code == 200  # GC is deferred
