import hashlib
import io

import pytest

from dunzo import content_repo


def test_add_media_stores_file_by_hash(conn, tmp_path):
    data = b"\x89PNG fake image bytes"
    media_dir = tmp_path / "media"
    m = content_repo.add_media(conn, media_dir, io.BytesIO(data), "image/png")
    digest = hashlib.sha256(data).hexdigest()
    assert m == content_repo.Media(id=digest, mime="image/png", size=len(data))
    assert (media_dir / digest).read_bytes() == data
    assert content_repo.get_media(conn, digest) == m
    assert [p.name for p in media_dir.iterdir()] == [digest]  # no temp files left behind


def test_same_bytes_are_stored_once(conn, tmp_path):
    media_dir = tmp_path / "media"
    a = content_repo.add_media(conn, media_dir, io.BytesIO(b"same"), "image/png")
    b = content_repo.add_media(conn, media_dir, io.BytesIO(b"same"), "image/png")
    assert a == b
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    assert len(list(media_dir.iterdir())) == 1


def test_mime_is_normalized(conn, tmp_path):
    m = content_repo.add_media(conn, tmp_path / "m", io.BytesIO(b"x"), "Image/PNG; foo=bar")
    assert m.mime == "image/png"


@pytest.mark.parametrize("mime", [None, "", "text/html", "application/javascript", "image/x-icon"])
def test_unsupported_types_rejected(conn, tmp_path, mime):
    media_dir = tmp_path / "m"
    with pytest.raises(ValueError, match="not supported"):
        content_repo.add_media(conn, media_dir, io.BytesIO(b"x"), mime)
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
    assert not media_dir.exists() or list(media_dir.iterdir()) == []


def test_empty_file_rejected_and_cleaned_up(conn, tmp_path):
    media_dir = tmp_path / "m"
    with pytest.raises(ValueError, match="empty"):
        content_repo.add_media(conn, media_dir, io.BytesIO(b""), "image/png")
    assert list(media_dir.iterdir()) == []


def test_too_large_rejected_and_cleaned_up(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(content_repo, "MAX_MEDIA_BYTES", 10)
    media_dir = tmp_path / "m"
    with pytest.raises(ValueError, match="too large"):
        content_repo.add_media(conn, media_dir, io.BytesIO(b"x" * 11), "image/png")
    assert list(media_dir.iterdir()) == []
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
