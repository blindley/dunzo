import json

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect

HX = {"HX-Request": "true"}
PNG = ("pic.png", b"\x89PNG fake", "image/png")
SCHEMA = json.dumps(
    {
        "type": "object",
        "required": ["front"],
        "properties": {
            "front": {"type": "string", "title": "Front", "maxLength": 20},
            "back": {"type": "string"},
            "level": {"enum": ["easy", "hard"]},
            "hard": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
)


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


def make(db, schema=SCHEMA):
    deck = repo.create_deck(db, "Deck")
    player = repo.create_player(db, "Player", "// js", schema)
    db.commit()
    return deck, player


def add_note(db, deck, player, text, count=1):
    n = repo.create_note(db, deck.id, player.id, count, {"front": text})
    db.commit()
    return n


def texts(db, deck):
    return [n.properties.get("front") for n in repo.list_notes(db, deck.id)]


# -------------------------------------------------------------------------- deck page


def test_deck_page_empty_and_missing(client, db):
    deck, _ = make(db)
    r = client.get(f"/decks/{deck.id}")
    assert r.status_code == 200 and "No notes in this deck yet" in r.text
    assert "Deck" in r.text and f"/decks/{deck.id}/notes/new" in r.text
    assert client.get("/decks/nope").status_code == 404
    assert f'href="/decks/{deck.id}"' in client.get("/decks").text  # deck list links to it


def test_deck_page_lists_notes_in_order_with_preview(client, db):
    deck, player = make(db)
    add_note(db, deck, player, "first <b>")
    add_note(db, deck, player, "second", count=3)
    page = client.get(f"/decks/{deck.id}").text
    assert page.index("first") < page.index("second")
    assert "&lt;b&gt;" in page and "<b>" not in page.split("<main>")[1]
    assert "3 cards" in page and "1 card" in page


# ----------------------------------------------------------------------- creating notes


def test_choose_player_page(client, db):
    deck = repo.create_deck(db, "D")
    db.commit()
    r = client.get(f"/decks/{deck.id}/notes/new")
    assert r.status_code == 200 and "no players yet" in r.text
    player = repo.create_player(db, "Pl", "x")
    db.commit()
    r = client.get(f"/decks/{deck.id}/notes/new?before=abc")
    assert f"player_id={player.id}&amp;before=abc" in r.text


def test_new_form_is_generated_from_schema(client, db):
    deck, player = make(db)
    r = client.get(f"/decks/{deck.id}/notes/new?player_id={player.id}")
    assert r.status_code == 200
    for name in ("prop__front", "prop__back", "prop__level", "prop__hard", "prop__tags"):
        assert f'name="{name}"' in r.text
    assert "Front *" in r.text  # title, marked required
    assert 'name="properties_json"' not in r.text
    assert f'name="player_id" value="{player.id}"' in r.text


def test_new_form_without_schema_is_raw_json(client, db):
    deck, player = make(db, schema=None)
    r = client.get(f"/decks/{deck.id}/notes/new?player_id={player.id}")
    assert 'name="properties_json"' in r.text and "prop__" not in r.text


def test_unknown_player_or_deck_404(client, db):
    deck, _ = make(db)
    assert client.get(f"/decks/{deck.id}/notes/new?player_id=nope").status_code == 404
    assert client.get("/decks/nope/notes/new").status_code == 404
    assert client.post(f"/decks/{deck.id}/notes", data={"player_id": "nope"}).status_code == 404


def test_create_from_generated_form(client, db):
    deck, player = make(db)
    r = client.post(
        f"/decks/{deck.id}/notes",
        data={
            "player_id": player.id,
            "card_count": "2",
            "prop__front": "Hola",
            "prop__level": '"easy"',
            "prop__tags": "a\nb",
            "prop__hard": "on",
        },
    )
    assert r.status_code == 303 and r.headers["location"] == f"/decks/{deck.id}"
    (note,) = repo.list_notes(db, deck.id)
    assert note.card_count == 2
    assert note.properties == {
        "front": "Hola", "level": "easy", "tags": ["a", "b"], "hard": True,
    }


def test_create_from_raw_json(client, db):
    deck, player = make(db, schema=None)
    r = client.post(
        f"/decks/{deck.id}/notes",
        data={"player_id": player.id, "card_count": "1", "properties_json": '{"q": "x"}'},
    )
    assert r.status_code == 303
    assert repo.list_notes(db, deck.id)[0].properties == {"q": "x"}


@pytest.mark.parametrize(
    "data, message",
    [
        ({"card_count": "1"}, "required"),  # front missing
        ({"card_count": "1", "prop__front": "x" * 21}, "too long"),
        ({"card_count": "0", "prop__front": "x"}, "between 1 and"),
        ({"card_count": "abc", "prop__front": "x"}, "between 1 and"),
    ],
)
def test_create_errors_rerender_with_input(client, db, data, message):
    deck, player = make(db)
    r = client.post(f"/decks/{deck.id}/notes", data={"player_id": player.id, "prop__back": "keep", **data})
    assert r.status_code == 422
    assert message in r.text and "keep" in r.text and "<html" in r.text
    assert repo.list_notes(db, deck.id) == []


def test_create_raw_json_errors(client, db):
    deck, player = make(db, schema=None)
    r = client.post(
        f"/decks/{deck.id}/notes",
        data={"player_id": player.id, "card_count": "1", "properties_json": "{bad"},
    )
    assert r.status_code == 422 and "not valid JSON" in r.text and "{bad" in r.text


def test_insert_before(client, db):
    deck, player = make(db)
    a, b = add_note(db, deck, player, "a"), add_note(db, deck, player, "b")
    r = client.post(
        f"/decks/{deck.id}/notes",
        data={"player_id": player.id, "card_count": "1", "prop__front": "x", "before": b.id},
    )
    assert r.status_code == 303
    assert texts(db, deck) == ["a", "x", "b"]
    bad = client.post(
        f"/decks/{deck.id}/notes",
        data={"player_id": player.id, "card_count": "1", "prop__front": "y", "before": "nope"},
    )
    assert bad.status_code == 422 and "not in this deck" in bad.text
    assert a.id


# ----------------------------------------------------------------- delete and reorder


def test_delete_idempotent_and_scoped_to_deck(client, db):
    deck, player = make(db)
    other = repo.create_deck(db, "Other")
    n = add_note(db, deck, player, "keep me")
    db.commit()
    wrong = client.delete(f"/decks/{other.id}/notes/{n.id}", headers=HX)  # wrong deck: no-op
    assert wrong.status_code == 200 and repo.get_note(db, n.id) is not None
    ok = client.delete(f"/decks/{deck.id}/notes/{n.id}", headers=HX)
    assert ok.status_code == 200 and "No notes in this deck yet" in ok.text
    assert client.delete(f"/decks/{deck.id}/notes/{n.id}", headers=HX).status_code == 200
    assert client.delete(f"/decks/nope/notes/{n.id}", headers=HX).status_code == 404


def test_move_up_and_down(client, db):
    deck, player = make(db)
    a, b, c = (add_note(db, deck, player, t) for t in "abc")
    url = lambda n: f"/decks/{deck.id}/notes/{n.id}/move"
    assert client.post(url(c), data={"direction": "up"}, headers=HX).status_code == 200
    assert texts(db, deck) == ["a", "c", "b"]
    client.post(url(a), data={"direction": "down"}, headers=HX)
    assert texts(db, deck) == ["c", "a", "b"]
    client.post(url(a), data={"direction": "down"}, headers=HX)  # a is now second: goes last
    assert texts(db, deck) == ["c", "b", "a"]
    client.post(url(a), data={"direction": "down"}, headers=HX)  # already last: no-op
    client.post(url(c), data={"direction": "up"}, headers=HX)  # already first: no-op
    assert texts(db, deck) == ["c", "b", "a"]
    assert c.id and b.id


def test_move_without_htmx_redirects_and_bad_direction(client, db):
    deck, player = make(db)
    a, _b = add_note(db, deck, player, "a"), add_note(db, deck, player, "b")
    r = client.post(f"/decks/{deck.id}/notes/{a.id}/move", data={"direction": "down"})
    assert r.status_code == 303 and r.headers["location"] == f"/decks/{deck.id}"
    assert texts(db, deck) == ["b", "a"]
    assert client.post(f"/decks/{deck.id}/notes/{a.id}/move", data={"direction": "sideways"}).status_code == 422
    assert client.post(f"/decks/{deck.id}/notes/nope/move", data={"direction": "up"}, headers=HX).status_code == 200


# ---------------------------------------------------------------------------- editing


def test_edit_page_prefills_values(client, db):
    deck, player = make(db)
    n = repo.create_note(db, deck.id, player.id, 3, {"front": "Hi", "tags": ["x", "y"], "hard": True})
    db.commit()
    page = client.get(f"/notes/{n.id}").text
    assert ">Hi</textarea>" in page and "x\ny" in page
    assert 'value="3"' in page and "checked" in page
    assert "Moving a note to another deck" in page
    assert "Saved." not in page and "Saved." in client.get(f"/notes/{n.id}?saved=1").text
    assert client.get("/notes/nope").status_code == 404


def test_edit_save_keeps_unknown_properties_and_updates(client, db):
    deck, player = make(db)
    n = repo.create_note(db, deck.id, player.id, 1, {"front": "old", "legacy": 42})
    db.commit()
    r = client.post(f"/notes/{n.id}", data={"card_count": "2", "prop__front": "new", "prop__back": ""})
    assert r.status_code == 303 and r.headers["location"] == f"/notes/{n.id}?saved=1"
    saved = repo.get_note(db, n.id)
    assert saved.properties == {"front": "new", "legacy": 42, "hard": False}
    assert saved.card_count == 2


def test_edit_validation_error_keeps_note_unchanged(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, "orig")
    r = client.post(f"/notes/{n.id}", data={"card_count": "1", "prop__front": ""})
    assert r.status_code == 422 and "required" in r.text
    assert repo.get_note(db, n.id).properties == {"front": "orig"}


def test_edit_raw_json_note(client, db):
    deck, player = make(db, schema=None)
    n = repo.create_note(db, deck.id, player.id, 1, {"a": 1})
    db.commit()
    assert "&#34;a&#34;: 1" in client.get(f"/notes/{n.id}").text  # escaped inside the textarea
    client.post(f"/notes/{n.id}", data={"card_count": "1", "properties_json": '{"b": 2}'})
    assert repo.get_note(db, n.id).properties == {"b": 2}


def test_edit_moves_note_to_end_of_other_deck(client, db):
    deck, player = make(db)
    other = repo.create_deck(db, "Other")
    add_note(db, other, player, "o1")
    n = add_note(db, deck, player, "mover")
    add_note(db, deck, player, "stay")
    r = client.post(f"/notes/{n.id}", data={"card_count": "1", "prop__front": "mover", "deck_id": other.id})
    assert r.status_code == 303
    assert texts(db, other) == ["o1", "mover"] and texts(db, deck) == ["stay"]
    bad = client.post(f"/notes/{n.id}", data={"card_count": "1", "prop__front": "mover", "deck_id": "nope"})
    assert bad.status_code == 422 and "Deck not found" in bad.text


# ------------------------------------------------------ shrinking card_count and progress


def add_progress(db, note_id, index):
    db.execute("INSERT INTO card_state VALUES (?, ?, 1.0, 'sm2', '{}')", (note_id, index))
    db.execute(
        "INSERT INTO review_log (note_id, card_index, reviewed_at, grade, state_after)"
        " VALUES (?, ?, 1.0, 3, '{}')",
        (note_id, index),
    )
    db.commit()


def progress_counts(db, note_id):
    return tuple(
        db.execute(f"SELECT COUNT(*) FROM {t} WHERE note_id = ?", (note_id,)).fetchone()[0]
        for t in ("card_state", "review_log")
    )


def test_shrink_without_progress_needs_no_confirmation(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, "x", count=4)
    add_progress(db, n.id, 0)  # only card 0 reviewed; cards 2.. have none
    r = client.post(f"/notes/{n.id}", data={"card_count": "2", "prop__front": "x"})
    assert r.status_code == 303
    assert repo.get_note(db, n.id).card_count == 2
    assert progress_counts(db, n.id) == (1, 1)


def test_shrink_with_progress_requires_confirmation(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, "x", count=4)
    for i in range(4):
        add_progress(db, n.id, i)
    data = {"card_count": "2", "prop__front": "changed"}
    r = client.post(f"/notes/{n.id}", data=data)
    assert r.status_code == 409
    assert "permanently deletes" in r.text and "2 reviewed cards" in r.text
    assert 'name="confirm_shrink"' in r.text and ">changed</textarea>" in r.text
    assert repo.get_note(db, n.id).card_count == 4  # nothing was saved
    assert repo.get_note(db, n.id).properties == {"front": "x"}
    assert progress_counts(db, n.id) == (4, 4)

    r = client.post(f"/notes/{n.id}", data={**data, "confirm_shrink": "1"})
    assert r.status_code == 303
    assert repo.get_note(db, n.id).card_count == 2
    assert progress_counts(db, n.id) == (2, 2)  # cards 0 and 1 keep their progress


def test_invalid_input_with_confirmation_deletes_no_progress(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, "x", count=3)
    add_progress(db, n.id, 2)
    r = client.post(f"/notes/{n.id}", data={"card_count": "1", "prop__front": "", "confirm_shrink": "1"})
    assert r.status_code == 422
    assert progress_counts(db, n.id) == (1, 1)


def test_growing_card_count_never_touches_progress(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, "x", count=2)
    add_progress(db, n.id, 1)
    assert client.post(f"/notes/{n.id}", data={"card_count": "5", "prop__front": "x"}).status_code == 303
    assert progress_counts(db, n.id) == (1, 1)


# --------------------------------------------------------------------------- note media


def test_note_media_upload_list_unlink(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, "x")
    r = client.post(f"/notes/{n.id}/media", files={"file": PNG}, headers=HX)
    assert r.status_code == 200 and "<html" not in r.text
    media_id = r.text.split('href="/media/')[1].split('"')[0]
    assert media_id in r.text.split("<code")[1]  # the full hash is shown for use in properties
    assert client.get(f"/media/{media_id}").content == PNG[1]
    assert media_id in client.get(f"/notes/{n.id}").text
    gone = client.delete(f"/notes/{n.id}/media/{media_id}", headers=HX)
    assert gone.status_code == 200 and "No media attached" in gone.text
    assert client.delete(f"/notes/{n.id}/media/{media_id}", headers=HX).status_code == 200


def test_note_media_upload_errors_and_redirect(client, db):
    deck, player = make(db)
    n = add_note(db, deck, player, "x")
    bad = client.post(f"/notes/{n.id}/media", files={"file": ("a.html", b"<x>", "text/html")}, headers=HX)
    assert bad.status_code == 422 and bad.headers["HX-Retarget"] == "#media-form-error"
    page = client.post(f"/notes/{n.id}/media", files={"file": ("a.js", b"x", "text/javascript")})
    assert page.status_code == 422 and "not supported" in page.text and "<html" in page.text
    none = client.post(f"/notes/{n.id}/media", data={}, headers=HX)
    assert none.status_code == 422 and "Choose a file" in none.text
    ok = client.post(f"/notes/{n.id}/media", files={"file": PNG})
    assert ok.status_code == 303 and ok.headers["location"] == f"/notes/{n.id}"
    assert client.post("/notes/nope/media", files={"file": PNG}).status_code == 404
