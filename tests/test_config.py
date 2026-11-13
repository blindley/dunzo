import pytest

from dunzo.config import get_settings


def test_missing_port_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("dunzo.config.load_dotenv", lambda *a, **k: None)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="PORT"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_paths_derive_from_data_dir(settings, tmp_path):
    assert settings.data_dir == tmp_path / "data"
    assert settings.db_path == tmp_path / "data" / "dunzo.db"
    assert settings.media_dir == tmp_path / "data" / "media"
