import time

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect

STARTER = "/players/new?starter=basic"


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


def make_player(db):
    player = repo.create_player(db, "P", "// js")
    db.commit()
    return player


def make_deck_with_note(db):
    deck = repo.create_deck(db, "Deck")
    note = repo.create_note(db, deck.id, make_player(db).id, 1, {"front": "q"})
    db.commit()
    return deck, note


# ------------------------------------------------------------------------ decks page


def test_decks_page_points_to_the_starter_player_until_a_player_exists(client, db):
    assert STARTER in client.get("/decks").text
    make_player(db)
    assert STARTER not in client.get("/decks").text


def test_decks_page_hint_also_shows_when_a_create_error_rerenders_the_page(client):
    r = client.post("/decks", data={"name": ""})
    assert r.status_code == 422 and "cannot be empty" in r.text and STARTER in r.text


# ------------------------------------------------------------------------- deck page


def test_empty_deck_offers_the_first_note_and_no_review_hint(client, db):
    deck = repo.create_deck(db, "Empty")
    db.commit()
    page = client.get(f"/decks/{deck.id}").text
    assert f'href="/decks/{deck.id}/notes/new">Add the first note' in page
    assert "Nothing to review" not in page


def test_deck_with_nothing_due_says_so_and_has_no_review_button(client, db):
    deck, note = make_deck_with_note(db)
    db.execute(
        "INSERT INTO card_state VALUES (?, 0, ?, 'sm2', '{}')", (note.id, time.time() + 10**9)
    )
    db.commit()
    page = client.get(f"/decks/{deck.id}").text
    assert "Nothing to review right now" in page
    assert f"/decks/{deck.id}/review" not in page


def test_deck_with_new_cards_has_a_review_button_and_no_hint(client, db):
    deck, _ = make_deck_with_note(db)
    page = client.get(f"/decks/{deck.id}").text
    assert "Review (0 due, 1 new)" in page
    assert "Nothing to review" not in page


def test_emptied_note_list_fragment_links_to_adding_a_note(client, db):
    deck, note = make_deck_with_note(db)
    r = client.delete(f"/decks/{deck.id}/notes/{note.id}", headers={"HX-Request": "true"})
    assert "No notes in this deck yet" in r.text
    assert f'href="/decks/{deck.id}/notes/new"' in r.text


# --------------------------------------------------------------- choose-a-player page


def test_choose_player_without_players_links_the_starter(client, db):
    deck = repo.create_deck(db, "D")
    db.commit()
    page = client.get(f"/decks/{deck.id}/notes/new").text
    assert "no players yet" in page and STARTER in page and 'href="/players/new"' in page


# ----------------------------------------------------------------------------- stats


def test_stats_say_so_when_there_are_no_cards(client, db):
    assert "No cards yet" in client.get("/stats").text
    deck = repo.create_deck(db, "Empty")
    db.commit()
    assert "No cards yet" in client.get(f"/decks/{deck.id}/stats").text


def test_stats_hide_the_empty_message_once_there_are_cards(client, db):
    deck, _ = make_deck_with_note(db)
    assert "No cards yet" not in client.get("/stats").text
    assert "No cards yet" not in client.get(f"/decks/{deck.id}/stats").text
