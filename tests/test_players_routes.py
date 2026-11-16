import io
import json
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect

HX = {"HX-Request": "true"}
SITE = {"index.html": "<p>hi</p>", "css/main.css": "p{}", "js/app.js": "var a;"}


@pytest.fixture
def client(settings):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def make_zip(members=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in (SITE if members is None else members).items():
            zf.writestr(name, data)
    return buf.getvalue()


def zip_part(members=None, name="p.zip"):
    return {"file": (name, make_zip(members), "application/zip")}


def create(client, name="Basic", members=None, schema=""):
    r = client.post("/players", data={"name": name, "note_schema": schema}, files=zip_part(members))
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


def stored_paths(pid):
    db = connect()
    try:
        return [f.path for f in repo.list_player_files(db, pid)]
    finally:
        db.close()


def test_list_empty_state_and_nav(client):
    r = client.get("/players")
    assert r.status_code == 200
    assert "No players yet" in r.text
    assert 'href="/players"' in r.text


def test_new_page_renders_the_zip_form(client):
    r = client.get("/players/new")
    assert r.status_code == 200
    assert 'name="name"' in r.text and 'name="note_schema"' in r.text
    assert 'name="file"' in r.text and 'enctype="multipart/form-data"' in r.text
    assert "required" in r.text  # a zip is required without a starter
    assert "Replace all files" not in r.text  # files need a saved player


def test_create_from_a_zip_stores_the_files_and_redirects_to_the_edit_page(client):
    pid = create(client, name="Typing")
    assert stored_paths(pid) == ["css/main.css", "index.html", "js/app.js"]
    page = client.get(f"/players/{pid}?saved=1")
    assert page.status_code == 200 and "Saved." in page.text
    for path in ("css/main.css", "index.html", "js/app.js"):
        assert path in page.text
    assert "text/css" in page.text and "Replace all files" in page.text
    assert "Typing" in client.get("/players").text


def test_create_from_a_zip_of_a_folder(client):
    pid = create(client, members={"mysite/index.html": "x", "mysite/a.js": "y"})
    assert stored_paths(pid) == ["a.js", "index.html"]


def test_create_with_a_schema(client):
    schema = json.dumps({"type": "object"})
    pid = create(client, schema=schema)
    assert "&#34;type&#34;: &#34;object&#34;" in client.get(f"/players/{pid}").text


@pytest.mark.parametrize(
    "data, members, message",
    [
        ({"name": ""}, None, "name cannot be empty"),
        ({"name": "p", "note_schema": "{bad"}, None, "not valid JSON"),
        ({"name": "p", "note_schema": '{"type": "nope"}'}, None, "not a valid JSON Schema"),
        ({"name": "p"}, {"readme.txt": "x"}, "needs an index.html"),
        ({"name": "p"}, {"index.html": "x", "a.exe": "x"}, "not supported"),
        ({"name": "p"}, {"index.html": "x", "dunzo/api.js": "x"}, "reserved"),
        ({"name": "p"}, {"index.html": "x", "../up.js": "x"}, "not a valid file path"),
    ],
)
def test_create_validation_errors_rerender_form_with_input(client, data, members, message):
    r = client.post("/players", data=data, files=zip_part(members))
    assert r.status_code == 422
    assert message in r.text
    assert "<html" in r.text
    assert client.get("/players").text.count('class="deck-row"') == 0


def test_create_without_a_zip_or_starter_says_so(client):
    r = client.post("/players", data={"name": "p"})
    assert r.status_code == 422 and "Choose a zip file" in r.text


def test_create_with_something_that_is_not_a_zip(client):
    r = client.post("/players", data={"name": "p"}, files={"file": ("p.zip", b"nope", "application/zip")})
    assert r.status_code == 422 and "not a valid zip" in r.text


def test_error_keeps_what_the_user_typed(client):
    r = client.post("/players", data={"name": "Keepme", "note_schema": "{bad"}, files=zip_part())
    assert "Keepme" in r.text and "{bad" in r.text


def test_error_after_choosing_the_starter_keeps_the_starter(client):
    r = client.post("/players", data={"name": " ", "starter": "basic"})
    assert r.status_code == 422 and 'name="starter" value="basic"' in r.text


def test_a_chosen_zip_wins_over_the_starter(client):
    r = client.post("/players", data={"name": "Mine", "starter": "basic"}, files=zip_part({"index.html": "x"}))
    assert r.status_code == 303
    assert stored_paths(r.headers["location"].split("?")[0].rsplit("/", 1)[1]) == ["index.html"]


def test_update_saves_name_and_schema_and_keeps_the_files(client):
    pid = create(client)
    schema = json.dumps({"type": "object"})
    r = client.post(f"/players/{pid}", data={"name": "Renamed", "note_schema": schema})
    assert r.status_code == 303 and r.headers["location"] == f"/players/{pid}?saved=1"
    page = client.get(f"/players/{pid}").text
    assert "Renamed" in page and "&#34;type&#34;: &#34;object&#34;" in page
    assert stored_paths(pid) == ["css/main.css", "index.html", "js/app.js"]


def test_update_validation_error_changes_nothing(client):
    pid = create(client, name="Orig")
    bad = client.post(f"/players/{pid}", data={"name": "X", "note_schema": "{bad"})
    assert bad.status_code == 422 and "not valid JSON" in bad.text
    assert "Orig" in client.get(f"/players/{pid}").text


def test_replace_files(client):
    pid = create(client)
    r = client.post(f"/players/{pid}/files", files=zip_part({"index.html": "new", "other.js": "x"}))
    assert r.status_code == 303 and r.headers["location"] == f"/players/{pid}?saved=1"
    assert stored_paths(pid) == ["index.html", "other.js"]
    page = client.get(f"/players/{pid}").text
    assert "other.js" in page and "css/main.css" not in page


def test_replace_files_errors_keep_the_old_files(client):
    pid = create(client)
    bad = client.post(f"/players/{pid}/files", files=zip_part({"only.js": "x"}))
    assert bad.status_code == 422 and "needs an index.html" in bad.text and "<html" in bad.text
    none = client.post(f"/players/{pid}/files", data={})
    assert none.status_code == 422 and "Choose a zip file" in none.text
    assert stored_paths(pid) == ["css/main.css", "index.html", "js/app.js"]


def test_unknown_player_is_404(client):
    assert client.get("/players/nope").status_code == 404
    assert client.post("/players/nope", data={"name": "a"}).status_code == 404
    assert client.post("/players/nope/files", files=zip_part()).status_code == 404


def test_names_are_html_escaped(client):
    create(client, name="<b>bold</b>")
    page = client.get("/players").text
    assert "<b>bold</b>" not in page
    pid = page.split('href="/players/')[1].split('"')[0]
    edit = client.get(f"/players/{pid}").text
    assert "<b>bold</b>" not in edit


def test_file_paths_in_errors_are_html_escaped(client):
    # paths are restricted to plain characters; the error message that quotes one is escaped
    r = client.post("/players", data={"name": "p"}, files=zip_part({"index.html": "x", "a<b>.js": "x"}))
    assert r.status_code == 422 and "<b>.js" not in r.text and "&lt;b&gt;.js" in r.text


def test_delete_unused_player(client):
    pid = create(client, name="Gone")
    r = client.delete(f"/players/{pid}", headers=HX)
    assert r.status_code == 200
    assert "No players yet" in r.text
    assert client.delete(f"/players/{pid}", headers=HX).status_code == 200  # idempotent


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
