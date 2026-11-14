import io
import json

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect
from dunzo.routes import review
from dunzo.templating import STATIC_DIR

NOW = 1_000_000.0
DAY = 86400.0


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
    monkeypatch.setattr(review, "_now", lambda: NOW)


def make(db, source="// js"):
    deck = repo.create_deck(db, "Deck")
    player = repo.create_player(db, "Player", source)
    db.commit()
    return deck, player


def add_note(db, deck, player, count=1, props=None):
    n = repo.create_note(db, deck.id, player.id, count, props or {"front": "q"})
    db.commit()
    return n


def nxt(client, deck):
    r = client.get(f"/decks/{deck.id}/review/next")
    assert r.status_code == 200
    return r.json()


def report(client, deck, **body):
    return client.post(f"/decks/{deck.id}/review/report", json=body)


def rows(db, table):
    return db.execute(f"SELECT * FROM {table}").fetchall()


# ---------------------------------------------------------------------------- pages


def test_review_page_and_unknown_deck(client, db):
    deck, _ = make(db)
    page = client.get(f"/decks/{deck.id}/review")
    assert page.status_code == 200
    assert f'data-next-url="/decks/{deck.id}/review/next"' in page.text
    assert f'data-report-url="/decks/{deck.id}/review/report"' in page.text
    assert "/static/player-host.js" in page.text and "/static/review.js" in page.text
    assert client.get("/decks/nope/review").status_code == 404
    assert client.get("/decks/nope/review/next").status_code == 404
    assert client.post("/decks/nope/review/report", json={}).status_code == 404


def test_deck_page_links_to_review_only_when_there_is_something(client, db):
    deck, player = make(db)
    assert "/review" not in client.get(f"/decks/{deck.id}").text
    add_note(db, deck, player, count=3)
    assert "Review (0 due, 3 new)" in client.get(f"/decks/{deck.id}").text


# ----------------------------------------------------------------------------- next


def test_empty_deck_is_done(client, db):
    deck, _ = make(db)
    assert nxt(client, deck) == {"done": True, "counts": {"due": 0, "new": 0}}


def test_next_returns_card_media_and_frame(client, db, settings):
    deck, player = make(db)
    n = add_note(db, deck, player, count=2, props={"front": "Hola", "pic": "x"})
    note_pic = repo.add_media(db, settings.media_dir, io.BytesIO(b"a"), "image/png")
    player_pic = repo.add_media(db, settings.media_dir, io.BytesIO(b"b"), "image/png")
    repo.link_note_media(db, n.id, note_pic.id)
    repo.link_player_media(db, player.id, player_pic.id)
    db.commit()
    data = nxt(client, deck)
    assert data["done"] is False and data["is_new"] is True
    assert data["counts"] == {"due": 0, "new": 2}
    assert data["frame_url"] == f"/review/frame/{player.id}"
    card = data["card"]
    assert card["index"] == 0 and card["card_count"] == 2
    assert card["note"]["id"] == n.id and card["note"]["properties"] == {"front": "Hola", "pic": "x"}
    assert {m["id"] for m in card["note"]["media"]} == {note_pic.id, player_pic.id}
    one = next(m for m in card["note"]["media"] if m["id"] == note_pic.id)
    assert one == {"id": note_pic.id, "mime": "image/png", "size": 1,
                   "url": f"/cards/{n.id}/media/{note_pic.id}"}
    assert client.get(one["url"]).content == b"a"  # the listed URL works
    assert client.get(f"/decks/{deck.id}/review/next").headers["cache-control"] == "no-store"


def test_due_card_is_served_before_new_ones(client, db):
    deck, player = make(db)
    a, b = add_note(db, deck, player, 1), add_note(db, deck, player, 1)
    db.execute("INSERT INTO card_state VALUES (?, 0, ?, 'sm2', '{}')", (b.id, NOW - 1))
    db.commit()
    data = nxt(client, deck)
    assert data["card"]["note"]["id"] == b.id and data["is_new"] is False
    assert data["counts"] == {"due": 1, "new": 1}
    assert a.id


# --------------------------------------------------------------------------- report


def test_first_report_creates_progress_and_returns_next_card(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, count=2)
    assert rows(db, "card_state") == []  # new cards have no progress row
    r = report(client, deck, note_id=n.id, card_index=0, grade=3, detail={"typed": "x"})
    assert r.status_code == 200
    data = r.json()
    assert data["card"]["index"] == 1 and data["is_new"] is True
    assert data["counts"] == {"due": 0, "new": 1}

    (state,) = rows(db, "card_state")
    assert (state["note_id"], state["card_index"], state["scheduler"]) == (n.id, 0, "sm2")
    assert state["due"] == NOW + 1 * DAY
    assert json.loads(state["state"])["interval"] == 1
    (log,) = rows(db, "review_log")
    assert log["grade"] == 3 and log["reviewed_at"] == NOW
    assert json.loads(log["detail"]) == {"typed": "x"}
    assert log["state_before"] is None


def test_second_report_uses_stored_state(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    report(client, deck, note_id=n.id, card_index=0, grade=3)
    db.execute("UPDATE card_state SET due = ?", (NOW - 1,))
    db.commit()
    assert nxt(client, deck)["is_new"] is False
    data = report(client, deck, note_id=n.id, card_index=0, grade=3).json()
    assert data == {"done": True, "counts": {"due": 0, "new": 0}}
    (state,) = rows(db, "card_state")
    assert json.loads(state["state"])["interval"] == 6 and state["due"] == NOW + 6 * DAY
    log = rows(db, "review_log")
    assert len(log) == 2 and json.loads(log[1]["state_before"])["interval"] == 1
    assert log[0]["detail"] is None


def test_again_comes_back_when_due_not_before(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    done = report(client, deck, note_id=n.id, card_index=0, grade=1).json()
    assert done["done"] is True  # due tomorrow, not now
    (state,) = rows(db, "card_state")
    assert json.loads(state["state"])["lapses"] == 1


@pytest.mark.parametrize("grade", [0, 5, "3", True, None, 3.0, [3]])
def test_bad_grade_is_rejected_and_records_nothing(client, db, grade):
    deck, player = make(db)
    n = add_note(db, deck, player)
    r = report(client, deck, note_id=n.id, card_index=0, grade=grade)
    assert r.status_code == 422 and "Grade" in r.json()["detail"]
    assert rows(db, "card_state") == [] and rows(db, "review_log") == []


def test_missing_grade_is_rejected(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    assert report(client, deck, note_id=n.id, card_index=0).status_code == 422


@pytest.mark.parametrize("body", [{"card_index": 0}, {"note_id": "x"}, {"note_id": 5, "card_index": 0},
                                  {"note_id": "x", "card_index": True}, {"note_id": "x", "card_index": "0"}])
def test_malformed_card_reference_is_422(client, db, body):
    deck, _ = make(db)
    assert report(client, deck, grade=3, **body).status_code == 422


def test_unknown_note_wrong_deck_or_index_is_404(client, db):
    deck, player = make(db)
    other = repo.create_deck(db, "Other")
    n = add_note(db, deck, player, count=2)
    db.commit()
    assert report(client, deck, note_id="nope", card_index=0, grade=3).status_code == 404
    assert report(client, other, note_id=n.id, card_index=0, grade=3).status_code == 404
    assert report(client, deck, note_id=n.id, card_index=2, grade=3).status_code == 404
    assert report(client, deck, note_id=n.id, card_index=-1, grade=3).status_code == 404
    assert rows(db, "card_state") == []


def test_oversized_detail_is_rejected(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    big = "x" * (review.MAX_DETAIL_BYTES + 1)
    r = report(client, deck, note_id=n.id, card_index=0, grade=3, detail=big)
    assert r.status_code == 422 and rows(db, "review_log") == []


def test_body_must_be_an_object(client, db):
    deck, _ = make(db)
    assert client.post(f"/decks/{deck.id}/review/report", json=[1]).status_code == 422


def test_corrupt_stored_state_is_a_conflict_not_a_crash(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    db.execute("INSERT INTO card_state VALUES (?, 0, ?, 'sm2', '{}')", (n.id, NOW - 1))
    db.commit()
    assert report(client, deck, note_id=n.id, card_index=0, grade=3).status_code == 409
    assert rows(db, "review_log") == []


def test_unknown_scheduler_in_stored_state_is_a_conflict(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    db.execute("INSERT INTO card_state VALUES (?, 0, ?, 'fsrs', '{}')", (n.id, NOW - 1))
    db.commit()
    assert report(client, deck, note_id=n.id, card_index=0, grade=3).status_code == 409


# ---------------------------------------------------------------- the player's frame


def test_frame_is_locked_down_and_loads_api_then_player(client, db):
    deck, player = make(db)
    r = client.get(f"/review/frame/{player.id}")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    for part in ("default-src 'none'", "script-src 'self'", "connect-src 'none'",
                 "form-action 'none'", "sandbox allow-scripts"):
        assert part in csp
    assert "unsafe-eval" not in csp and "allow-same-origin" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    body = r.text
    api = body.index("/static/player-api.js")
    src = body.index(f"/review/player/{player.id}/source.js?v={player.source_hash}")
    assert api < src
    assert "<script>" not in body  # no inline script: the player's code is never inlined
    assert client.get("/review/frame/nope").status_code == 404


def test_player_source_is_served_verbatim_as_javascript(client, db):
    source = "var x = '</script><b>';\n// ok\n"
    _, player = make(db, source=source)
    r = client.get(f"/review/player/{player.id}/source.js")
    assert r.status_code == 200 and r.text == source
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/review/player/nope/source.js").status_code == 404


def test_edited_player_source_is_what_the_frame_gets(client, db):
    _, player = make(db)
    repo.update_player(db, player.id, "Player", "// v2", None)
    db.commit()
    assert "// v2" in client.get(f"/review/player/{player.id}/source.js").text


# --------------------------------------------------------------------- static assets


@pytest.mark.parametrize("name", ["player-api.js", "player-host.js", "review.js"])
def test_static_scripts_are_served(client, name):
    assert client.get(f"/static/{name}").status_code == 200


def test_host_sandboxes_with_scripts_only():
    host = (STATIC_DIR / "player-host.js").read_text()
    assert 'setAttribute("sandbox", "allow-scripts")' in host
    for path in STATIC_DIR.glob("*.js"):
        if path.name != "htmx.min.js":
            assert "allow-same-origin" not in path.read_text(), path.name


def test_host_only_trusts_its_own_frame():
    host = (STATIC_DIR / "player-host.js").read_text()
    assert "event.source !== frame.contentWindow" in host
    api = (STATIC_DIR / "player-api.js").read_text()
    assert "event.source !== parent" in api
