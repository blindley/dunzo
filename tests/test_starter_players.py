import json

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo import starter_players
from dunzo.app import create_app


def test_basic_starter_is_a_valid_player(conn):
    values = starter_players.get_starter("basic")
    p = repo.create_player(conn, values["name"], values["source"], values["note_schema"])
    assert p.name == "Basic front/back"
    assert "dunzo.onInit" in p.source and "dunzo.done" in p.source
    deck = repo.create_deck(conn, "d")
    assert repo.create_note(conn, deck.id, p.id, 2, {"front": "a", "back": "b"})
    with pytest.raises(repo.NoteInvalid):
        repo.create_note(conn, deck.id, p.id, 1, {"front": "a"})  # back is required


def test_basic_source_does_not_use_anything_the_sandbox_forbids():
    source = starter_players.BASIC_SOURCE
    for banned in ("fetch(", "XMLHttpRequest", "eval(", "innerHTML", "localStorage"):
        assert banned not in source


def test_unknown_starter_and_copy_semantics():
    assert starter_players.get_starter("nope") is None
    starter_players.get_starter("basic")["name"] = "changed"
    assert starter_players.get_starter("basic")["name"] == "Basic front/back"
    assert json.loads(starter_players.get_starter("basic")["note_schema"])["required"]


@pytest.fixture
def client(settings):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def test_new_player_page_can_be_prefilled(client):
    page = client.get("/players/new?starter=basic")
    assert page.status_code == 200 and "Basic front/back" in page.text and "dunzo.onInit" in page.text
    blank = client.get("/players/new?starter=nope")
    assert blank.status_code == 200 and "dunzo.onInit" not in blank.text
    assert "dunzo.onInit" not in client.get("/players/new").text


def test_players_page_links_to_the_starter(client):
    assert "/players/new?starter=basic" in client.get("/players").text


def test_prefilled_form_can_be_saved(client):
    values = starter_players.get_starter("basic")
    r = client.post("/players", data=values)
    assert r.status_code == 303
