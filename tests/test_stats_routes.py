from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect
from dunzo.routes import review

NOON = datetime(2026, 11, 14, 12, 0).timestamp()


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


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(review, "_now", lambda: NOON)


def world(db):
    deck = repo.create_deck(db, "Spanish")
    player = repo.create_player(db, "P", "x")
    n = repo.create_note(db, deck.id, player.id, 2, {"front": "hola"})
    db.execute("INSERT INTO card_state VALUES (?, 0, ?, 'sm2', '{}')", (n.id, NOON - 1))
    for at, grade, before in ((NOON - 60, 3, "NULL"), (NOON - 30, 1, "'{}'")):
        db.execute(
            "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, detail, state_before,"
            f" state_after) VALUES (?, 0, ?, ?, '{{\"typed\": \"<b>x</b>\"}}', {before}, '{{}}')",
            (n.id, at, grade),
        )
    db.commit()
    return deck, n


def test_global_stats_page(client, db):
    world(db)
    r = client.get("/stats")
    assert r.status_code == 200
    assert "All decks together" in r.text and "Reviews today" in r.text
    assert "Retention" in r.text and "0%" in r.text  # one re-review, answered Again
    assert "Overdue cards are counted under Today" in r.text


def test_deck_stats_page(client, db):
    deck, _ = world(db)
    r = client.get(f"/decks/{deck.id}/stats")
    assert r.status_code == 200 and "Stats: Spanish" in r.text
    assert f'href="/decks/{deck.id}"' in r.text
    assert client.get("/decks/nope/stats").status_code == 404


def test_empty_stats_render(client, db):
    r = client.get("/stats")
    assert r.status_code == 200 and "–" in r.text  # no retention yet


@pytest.mark.parametrize("days, expected", [(7, 7), (0, 1), (-5, 1), (9999, 365)])
def test_days_parameter_is_clamped(client, db, days, expected):
    assert f"last {expected} days" in client.get(f"/stats?days={days}").text


def test_days_must_be_a_number(client, db):
    assert client.get("/stats?days=abc").status_code == 422


def test_note_history_page_escapes_detail(client, db):
    _, n = world(db)
    r = client.get(f"/notes/{n.id}/history")
    assert r.status_code == 200
    assert "Again" in r.text and "Good" in r.text and "(first review)" in r.text
    assert "<b>x</b>" not in r.text  # the player's detail is escaped
    assert r.text.index("Again") < r.text.index("Good")  # newest first
    assert "2026-11-14 11:59" in r.text


def test_note_history_empty_and_missing(client, db):
    deck = repo.create_deck(db, "D")
    player = repo.create_player(db, "P", "x")
    n = repo.create_note(db, deck.id, player.id, 1, {"front": "a"})
    db.commit()
    assert "has not been reviewed yet" in client.get(f"/notes/{n.id}/history").text
    assert client.get("/notes/nope/history").status_code == 404


def test_links_to_stats_and_history(client, db):
    deck, n = world(db)
    assert 'href="/stats"' in client.get("/decks").text
    page = client.get(f"/decks/{deck.id}").text
    assert f"/decks/{deck.id}/stats" in page and f"/notes/{n.id}/history" in page
