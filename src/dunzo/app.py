"""FastAPI application factory."""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from dunzo.config import get_settings
from dunzo.db import SCHEMA_VERSION, get_db, init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # fails loudly if PORT is missing
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="dunzo", lifespan=lifespan)

    @app.get("/health")
    def health(db: Annotated[sqlite3.Connection, Depends(get_db)]) -> dict[str, str | int]:
        db.execute("SELECT 1").fetchone()
        return {"status": "ok", "schema_version": SCHEMA_VERSION}

    # Routers (decks, players, notes, review, packages) are included here in later steps.
    return app


app = create_app()
