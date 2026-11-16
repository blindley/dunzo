import io
import json
import re

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect
from dunzo.routes import review
from dunzo.templating import STATIC_DIR

NOW = 1_000_000.0
DAY = 86400.0

SITE = {
    "index.html": b'<script src="dunzo/api.js"></script><script src="dunzo/card.js"></script>',
    "css/main.css": b"body{}",
    "js/app.js": b"var x = '</script>';\n",
    "img/pic.png": b"\x89PNG",
}


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


def make(db, files=None):
    deck = repo.create_deck(db, "Deck")
    player = repo.create_player(db, "Player", SITE if files is None else files)
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


def frame(note, index=0, path="index.html"):
    return f"/review/frame/{note.id}/{index}/{path}"


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


def test_next_returns_the_card_its_media_and_the_frame_url(client, db, settings):
    deck, player = make(db)
    n = add_note(db, deck, player, count=2, props={"front": "Hola", "pic": "x"})
    note_pic = repo.add_media(db, settings.media_dir, io.BytesIO(b"a"), "image/png")
    repo.link_note_media(db, n.id, note_pic.id)
    db.commit()
    data = nxt(client, deck)
    assert data["done"] is False and data["is_new"] is True
    assert data["counts"] == {"due": 0, "new": 2}
    assert data["frame_url"] == f"/review/frame/{n.id}/0/index.html"
    card = data["card"]
    assert card["index"] == 0 and card["card_count"] == 2
    assert card["note"]["id"] == n.id and card["note"]["properties"] == {"front": "Hola", "pic": "x"}
    assert card["note"]["media"] == [
        {"id": note_pic.id, "mime": "image/png", "size": 1, "url": f"/cards/{n.id}/media/{note_pic.id}"}
    ]
    assert client.get(card["note"]["media"][0]["url"]).content == b"a"  # the listed URL works
    assert client.get(f"/decks/{deck.id}/review/next").headers["cache-control"] == "no-store"


def test_the_frame_url_names_the_card_to_show(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, count=2)
    report(client, deck, note_id=n.id, card_index=0, grade=3)
    assert nxt(client, deck)["frame_url"] == f"/review/frame/{n.id}/1/index.html"


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


# ------------------------------------------------------------- the player's directory


def test_index_is_served_with_the_frame_headers(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    r = client.get(frame(n))
    assert r.status_code == 200 and r.content == SITE["index.html"]
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    for part in ("default-src 'none'", "script-src 'self'", "connect-src 'none'",
                 "form-action 'none'", "sandbox allow-scripts"):
        assert part in csp
    assert "unsafe-eval" not in csp and "allow-same-origin" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["access-control-allow-origin"] == "*"
    assert r.headers["cache-control"] == "no-cache"


def test_an_empty_path_is_the_index(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    assert client.get(f"/review/frame/{n.id}/0/").content == SITE["index.html"]


def test_other_files_are_served_verbatim_with_their_type(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    css = client.get(frame(n, 0, "css/main.css"))
    assert css.status_code == 200 and css.content == b"body{}"
    assert css.headers["content-type"].startswith("text/css")
    js = client.get(frame(n, 0, "js/app.js"))
    assert js.content == SITE["js/app.js"] and js.headers["content-type"].startswith("text/javascript")
    png = client.get(frame(n, 0, "img/pic.png"))
    assert png.content == SITE["img/pic.png"] and png.headers["content-type"] == "image/png"
    for r in (css, js, png):
        assert "sandbox allow-scripts" in r.headers["content-security-policy"]


def test_relative_references_from_the_index_resolve_inside_the_card_directory(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    base = nxt(client, deck)["frame_url"]
    assert base == f"/review/frame/{n.id}/0/index.html"
    # what a browser makes of src="dunzo/card.js" and href="css/main.css" on that page
    directory = base.rsplit("/", 1)[0]
    assert client.get(directory + "/dunzo/card.js").status_code == 200
    assert client.get(directory + "/css/main.css").status_code == 200


def test_missing_files_unknown_cards_and_traversal_are_404(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, count=2)
    assert client.get(frame(n, 0, "nope.js")).status_code == 404
    assert client.get(frame(n, 0, "css/")).status_code == 404
    assert client.get(frame(n, 0, "css")).status_code == 404
    assert client.get(frame(n, 2)).status_code == 404  # no such card
    assert client.get("/review/frame/nope/0/index.html").status_code == 404
    # encoded traversal never reaches anything: files are looked up by exact path in the
    # database, not on disk by name
    for path in ("%2e%2e/index.html", "..%2f..%2fetc/passwd", "css/%2e%2e/index.html", "%2fetc/passwd"):
        assert client.get(frame(n, 0, path)).status_code == 404, path


def test_another_players_files_are_not_reachable(client, db):
    deck, player = make(db)
    other = repo.create_player(db, "Other", {"index.html": b"o", "secret.js": b"s"})
    n = add_note(db, deck, player)
    db.commit()
    assert client.get(frame(n, 0, "secret.js")).status_code == 404
    assert other.id


def test_a_row_without_its_file_is_404(client, db, settings):
    deck, player = make(db)
    n = add_note(db, deck, player)
    entry = repo.get_player_file(db, player.id, "css/main.css")
    (settings.media_dir / entry.hash).unlink()
    assert client.get(frame(n, 0, "css/main.css")).status_code == 404


def test_edited_files_are_what_the_frame_gets(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    repo.replace_player_files(db, player.id, {"index.html": b"<p>v2</p>"})
    db.commit()
    assert client.get(frame(n)).content == b"<p>v2</p>"
    assert client.get(frame(n, 0, "css/main.css")).status_code == 404


# ------------------------------------------------------------------ the dunzo/ files


def test_card_js_carries_the_card(client, db, settings):
    deck, player = make(db)
    n = add_note(db, deck, player, count=3, props={"front": "日本語", "back": "x"})
    pic = repo.add_media(db, settings.media_dir, io.BytesIO(b"a"), "image/png")
    repo.link_note_media(db, n.id, pic.id)
    db.commit()
    r = client.get(frame(n, 2, "dunzo/card.js"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "no-store"
    assert "sandbox allow-scripts" in r.headers["content-security-policy"]
    match = re.fullmatch(r"window\.dunzoCard = (.*);\n", r.text, re.DOTALL)
    assert match
    card = json.loads(match.group(1))
    assert card == {
        "note": {
            "id": n.id,
            "properties": {"front": "日本語", "back": "x"},
            "media": [{"id": pic.id, "mime": "image/png", "size": 1, "url": f"/cards/{n.id}/media/{pic.id}"}],
        },
        "index": 2,
        "card_count": 3,
    }
    assert card == client.get(f"/decks/{deck.id}/review/next").json()["card"] | {"index": 2}


def test_card_js_differs_per_card(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, count=2)
    assert client.get(frame(n, 0, "dunzo/card.js")).text != client.get(frame(n, 1, "dunzo/card.js")).text


def test_card_js_for_a_missing_card_is_404(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    assert client.get(frame(n, 1, "dunzo/card.js")).status_code == 404
    assert client.get("/review/frame/nope/0/dunzo/card.js").status_code == 404


def test_api_js_is_the_static_player_api(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    r = client.get(frame(n, 0, "dunzo/api.js"))
    assert r.status_code == 200 and r.text == (STATIC_DIR / "player-api.js").read_text()
    assert r.headers["content-type"].startswith("text/javascript")
    assert "sandbox allow-scripts" in r.headers["content-security-policy"]


def test_nothing_else_exists_under_dunzo(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player)
    for path in ("dunzo/other.js", "dunzo/", "dunzo", "dunzo/api.js/x", "dunzo/sub/card.js"):
        assert client.get(frame(n, 0, path)).status_code == 404, path


def test_players_cannot_shadow_the_dunzo_files(db):
    with pytest.raises(ValueError, match="reserved"):
        repo.create_player(db, "p", {"index.html": b"x", "dunzo/card.js": b"evil"})


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


def test_player_api_reports_to_the_host_and_needs_no_messages_in():
    api = (STATIC_DIR / "player-api.js").read_text()
    assert "parent.postMessage" in api and "dunzoCard" in api
    assert 'addEventListener("message"' not in api  # the card arrives as a script, not a message
