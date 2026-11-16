import json

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo import starter_players
from dunzo.app import create_app


def test_basic_starter_is_a_valid_player(conn):
    values = starter_players.get_starter("basic")
    p = repo.create_player(conn, values["name"], values["files"], values["note_schema"])
    assert p.name == "Basic front/back"
    assert [f.path for f in repo.list_player_files(conn, p.id)] == ["index.html", "player.js", "style.css"]
    deck = repo.create_deck(conn, "d")
    assert repo.create_note(conn, deck.id, p.id, 2, {"front": "a", "back": "b"})
    with pytest.raises(repo.NoteInvalid):
        repo.create_note(conn, deck.id, p.id, 1, {"front": "a"})  # back is required


def test_index_loads_the_dunzo_files_then_the_player():
    html = starter_players.BASIC_INDEX
    api = html.index('src="dunzo/api.js"')
    card = html.index('src="dunzo/card.js"')
    player = html.index('src="player.js"')
    assert api < card < player
    assert 'href="style.css"' in html


def test_player_script_uses_the_api_and_nothing_the_sandbox_forbids():
    source = starter_players.BASIC_SOURCE
    assert "dunzo.onInit" in source and "dunzo.done" in source
    for banned in ("fetch(", "XMLHttpRequest", "eval(", "innerHTML", "localStorage"):
        assert banned not in source


def test_unknown_starter_and_copy_semantics():
    assert starter_players.get_starter("nope") is None
    starter = starter_players.get_starter("basic")
    starter["name"] = "changed"
    starter["files"]["extra.js"] = b"x"
    again = starter_players.get_starter("basic")
    assert again["name"] == "Basic front/back" and "extra.js" not in again["files"]
    assert json.loads(again["note_schema"])["required"]


@pytest.fixture
def client(settings):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def test_new_player_page_can_be_prefilled(client):
    page = client.get("/players/new?starter=basic")
    assert page.status_code == 200 and "Basic front/back" in page.text
    assert 'name="starter" value="basic"' in page.text
    zip_input = page.text.split('id="player-zip"')[1].split(">")[0]
    assert "required" not in zip_input  # the starter's files are used when no zip is chosen
    blank = client.get("/players/new?starter=nope")
    assert blank.status_code == 200 and "Basic front/back" not in blank.text
    assert 'name="starter"' not in blank.text
    assert 'name="starter"' not in client.get("/players/new").text


def test_players_page_links_to_the_starter(client):
    assert "/players/new?starter=basic" in client.get("/players").text


def test_prefilled_form_can_be_saved_without_a_zip(client, settings):
    values = starter_players.get_starter("basic")
    r = client.post(
        "/players",
        data={"name": values["name"], "note_schema": values["note_schema"], "starter": "basic"},
    )
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    for path in ("index.html", "style.css", "player.js"):
        assert path in page
