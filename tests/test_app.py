from fastapi.testclient import TestClient

from dunzo.app import create_app
from dunzo.db import SCHEMA_VERSION


def test_health(settings):
    with TestClient(create_app()) as client:  # context manager runs the lifespan (init_db)
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "schema_version": SCHEMA_VERSION}


def test_startup_creates_data_layout(settings):
    with TestClient(create_app()):
        pass
    assert settings.db_path.is_file()
    assert settings.media_dir.is_dir()
