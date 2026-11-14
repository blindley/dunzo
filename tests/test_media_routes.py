import hashlib

import pytest
from fastapi.testclient import TestClient

from dunzo.app import create_app


@pytest.fixture
def client(settings):
    with TestClient(create_app(), follow_redirects=False) as c:
        yield c


def upload(client, data=b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
           mime="image/svg+xml"):
    r = client.post("/players", data={"name": "p", "source": "x"})
    pid = r.headers["location"].split("?")[0].rsplit("/", 1)[1]
    client.post(f"/players/{pid}/media", files={"file": ("f", data, mime)})
    return hashlib.sha256(data).hexdigest()


def test_served_with_safe_headers(client):
    media_id = upload(client)
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
    media_id = upload(client, data=b"gone-soon", mime="image/png")
    (settings.media_dir / media_id).unlink()
    assert client.get(f"/media/{media_id}").status_code == 404
