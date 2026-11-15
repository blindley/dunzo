"""FastAPI application factory."""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from dunzo.config import get_settings
from dunzo.db import SCHEMA_VERSION, get_db, init_db
from dunzo.errors import register_error_handlers
from dunzo.routes import decks, media, notes, packages, players, review
from dunzo.templating import STATIC_DIR


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # fails loudly if PORT is missing
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="dunzo", lifespan=lifespan)
    register_error_handlers(app)

    @app.get("/health")
    def health(db: Annotated[sqlite3.Connection, Depends(get_db)]) -> dict[str, str | int]:
        db.execute("SELECT 1").fetchone()
        return {"status": "ok", "schema_version": SCHEMA_VERSION}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(decks.router)
    app.include_router(players.router)
    app.include_router(notes.router)
    app.include_router(media.router)
    app.include_router(review.router)
    app.include_router(packages.router)
    return app


app = create_app()
