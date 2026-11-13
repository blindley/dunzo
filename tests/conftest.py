import pytest

from dunzo.config import get_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Point the app at a throwaway data directory and a dummy port."""
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def conn(settings):
    from dunzo.db import connect, init_db

    init_db()
    c = connect()
    yield c
    c.close()
