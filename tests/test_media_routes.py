import hashlib
import io

import pytest
from fastapi.testclient import TestClient

from dunzo import content_repo as repo
from dunzo.app import create_app
from dunzo.db import connect


@pytest.fixture
def client(settings):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def store(settings, data=b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
          mime="image/svg+xml"):
    db = connect()
    repo.add_media(db, settings.media_dir, io.BytesIO(data), mime)
    db.commit()
    db.close()
    return hashlib.sha256(data).hexdigest()


def test_served_with_safe_headers(client, settings):
    media_id = store(settings)
    r = client.get(f"/media/{media_id}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert "immutable" in r.headers["cache-control"]


def test_unknown_media_is_404(client):
    assert client.get("/media/" + "0" * 64).status_code == 404


@pytest.mark.parametrize("bad", ["abc", "../../etc/passwd", "Z" * 64, "A" * 64, "0" * 63])
def test_malformed_ids_are_404(client, bad):
    assert client.get(f"/media/{bad}").status_code == 404


def test_row_without_file_is_404(client, settings):
    media_id = store(settings, data=b"gone-soon", mime="image/png")
    (settings.media_dir / media_id).unlink()
    assert client.get(f"/media/{media_id}").status_code == 404


def test_a_players_files_are_not_served_as_media(client, settings):
    db = connect()
    player = repo.create_player(db, "p", "<p>secret</p>")
    db.commit()
    digest = repo.list_player_files(db, player.id)[0].hash
    db.close()
    assert client.get(f"/media/{digest}").status_code == 404
