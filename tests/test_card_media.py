import io

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect


@pytest.fixture
def world(conn, settings):
    deck = repo.create_deck(conn, "d")
    player = repo.create_player(conn, "p", "x")
    other_player = repo.create_player(conn, "q", "x")
    note = repo.create_note(conn, deck.id, player.id, 1, {})
    sibling = repo.create_note(conn, deck.id, player.id, 1, {})
    stranger = repo.create_note(conn, deck.id, other_player.id, 1, {})
    add = lambda data: repo.add_media(conn, settings.media_dir, io.BytesIO(data), "image/png")  # noqa: E731
    on_note, on_player, unlinked = add(b"note-pic"), add(b"player-pic"), add(b"nobody")
    repo.link_note_media(conn, note.id, on_note.id)
    repo.link_player_media(conn, player.id, on_player.id)
    conn.commit()
    return dict(note=note, sibling=sibling, stranger=stranger, on_note=on_note,
                on_player=on_player, unlinked=unlinked)


def test_card_media_is_note_media_plus_player_media(conn, world):
    ids = {m.id for m in repo.list_card_media(conn, world["note"].id)}
    assert ids == {world["on_note"].id, world["on_player"].id}


def test_player_media_reaches_every_note_of_the_player_but_note_media_does_not(conn, world):
    ids = {m.id for m in repo.list_card_media(conn, world["sibling"].id)}
    assert ids == {world["on_player"].id}
    assert repo.list_card_media(conn, world["stranger"].id) == []


def test_media_linked_twice_is_listed_once(conn, world):
    repo.link_note_media(conn, world["note"].id, world["on_player"].id)
    assert len(repo.list_card_media(conn, world["note"].id)) == 2


def test_get_card_media(conn, world):
    note = world["note"].id
    assert repo.get_card_media(conn, note, world["on_note"].id) == world["on_note"]
    assert repo.get_card_media(conn, note, world["on_player"].id) == world["on_player"]
    assert repo.get_card_media(conn, note, world["unlinked"].id) is None
    assert repo.get_card_media(conn, "nope", world["on_note"].id) is None


@pytest.fixture
def client(world):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def test_route_serves_linked_media_with_safe_headers(client, world):
    r = client.get(f"/cards/{world['note'].id}/media/{world['on_note'].id}")
    assert r.status_code == 200 and r.content == b"note-pic"
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["access-control-allow-origin"] == "*"
    assert client.get(f"/cards/{world['note'].id}/media/{world['on_player'].id}").status_code == 200


def test_route_refuses_media_the_card_may_not_use(client, world):
    assert client.get(f"/cards/{world['note'].id}/media/{world['unlinked'].id}").status_code == 404
    assert client.get(f"/cards/{world['sibling'].id}/media/{world['on_note'].id}").status_code == 404
    assert client.get(f"/cards/{world['stranger'].id}/media/{world['on_player'].id}").status_code == 404
    assert client.get(f"/cards/nope/media/{world['on_note'].id}").status_code == 404


@pytest.mark.parametrize("bad", ["abc", "Z" * 64, "../../etc/passwd"])
def test_route_rejects_malformed_ids(client, world, bad):
    assert client.get(f"/cards/{world['note'].id}/media/{bad}").status_code == 404


def test_row_without_file_is_404(client, world, settings):
    (settings.media_dir / world["on_note"].id).unlink()
    assert client.get(f"/cards/{world['note'].id}/media/{world['on_note'].id}").status_code == 404
    connect().close()
