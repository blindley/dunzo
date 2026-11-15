import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect

HX = {"HX-Request": "true"}


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


def make(db, texts="abc"):
    deck = repo.create_deck(db, "Deck")
    player = repo.create_player(db, "Player", "// js")
    notes = [repo.create_note(db, deck.id, player.id, 1, {"front": t}) for t in texts]
    db.commit()
    return deck, player, notes


def texts(db, deck):
    return [n.properties["front"] for n in repo.list_notes(db, deck.id)]


def reorder(client, deck, ids, **kw):
    return client.post(f"/decks/{deck.id}/notes/reorder", data={"order": ids}, **kw)


# ------------------------------------------------------------------------------ route


def test_reorder_saves_and_returns_the_fresh_list(client, db):
    deck, _, (a, b, c) = make(db)
    r = reorder(client, deck, [c.id, a.id, b.id], headers=HX)
    assert r.status_code == 200 and "<html" not in r.text
    assert texts(db, deck) == ["c", "a", "b"]
    assert r.text.index(c.id) < r.text.index(a.id) < r.text.index(b.id)
    assert [n.position for n in repo.list_notes(db, deck.id)] == [1024, 2048, 3072]


def test_reorder_without_htmx_redirects_to_the_deck(client, db):
    deck, _, (a, b, _c) = make(db)
    r = reorder(client, deck, [b.id, a.id, _c.id])
    assert r.status_code == 303 and r.headers["location"] == f"/decks/{deck.id}"
    assert texts(db, deck) == ["b", "a", "c"]


def test_same_order_is_accepted(client, db):
    deck, _, notes = make(db)
    assert reorder(client, deck, [n.id for n in notes], headers=HX).status_code == 200
    assert texts(db, deck) == ["a", "b", "c"]


@pytest.mark.parametrize("which", ["missing", "duplicate", "extra", "foreign", "none"])
def test_stale_or_wrong_ids_are_a_409_and_change_nothing(client, db, which):
    deck, player, (a, b, c) = make(db)
    other = repo.create_deck(db, "Other")
    foreign = repo.create_note(db, other.id, player.id, 1, {"front": "x"})
    db.commit()
    ids = {
        "missing": [c.id, a.id],
        "duplicate": [c.id, a.id, a.id],
        "extra": [c.id, b.id, a.id, "zzz"],
        "foreign": [c.id, b.id, foreign.id],
        "none": [],
    }[which]
    r = reorder(client, deck, ids, headers=HX)
    assert r.status_code == 409 and "have changed" in r.json()["detail"]
    assert texts(db, deck) == ["a", "b", "c"]
    assert [n.properties["front"] for n in repo.list_notes(db, other.id)] == ["x"]


def test_unknown_deck_is_404(client):
    assert client.post("/decks/nope/notes/reorder", data={"order": ["x"]}, headers=HX).status_code == 404


def test_reorder_never_touches_progress(client, db):
    deck, _, (a, b, _c) = make(db)
    db.execute("INSERT INTO card_state VALUES (?, 0, 1.0, 'sm2', '{}')", (a.id,))
    db.execute(
        "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, state_after)"
        " VALUES (?, 0, 1.0, 3, '{}')",
        (a.id,),
    )
    db.commit()
    reorder(client, deck, [_c.id, b.id, a.id], headers=HX)
    for table in ("card_state", "review_log"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


def test_reorder_route_does_not_shadow_deleting_a_note(client, db):
    deck, _, (a, *_rest) = make(db)
    assert client.delete(f"/decks/{deck.id}/notes/{a.id}", headers=HX).status_code == 200
    assert texts(db, deck) == ["b", "c"]


# ------------------------------------------------------------------------- page markup


def test_deck_page_wires_up_the_reorder_script(client, db):
    deck, _, (a, b, _c) = make(db)
    page = client.get(f"/decks/{deck.id}").text
    assert f'data-reorder-url="/decks/{deck.id}/notes/reorder"' in page
    assert 'id="note-list-error"' in page and "/static/note-reorder.js" in page
    assert f'data-note-id="{a.id}"' in page and page.count('class="drag-handle"') == 3
    assert "Move up" in page  # the buttons stay as the keyboard and touch way to reorder


def test_a_single_note_has_no_drag_handle(client, db):
    deck, _, _ = make(db, texts="a")
    assert "drag-handle" not in client.get(f"/decks/{deck.id}").text


def test_fragment_after_a_change_carries_handles_and_ids(client, db):
    deck, _, (a, b, c) = make(db)
    r = client.post(f"/decks/{deck.id}/notes/{c.id}/move", data={"direction": "up"}, headers=HX)
    assert r.text.count("drag-handle") == 3 and f'data-note-id="{c.id}"' in r.text
    assert a.id and b.id


def test_script_is_served_and_posts_the_whole_order(client):
    r = client.get("/static/note-reorder.js")
    assert r.status_code == 200
    assert 'body.append("order"' in r.text and "reorderUrl" in r.text
