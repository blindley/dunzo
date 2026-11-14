"""Import and export of content packages (zip files): see packages.py for the format."""

import os
import re
import sqlite3
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.background import BackgroundTask

from dunzo import content_repo, packages
from dunzo.config import get_settings
from dunzo.db import get_db
from dunzo.templating import templates

router = APIRouter()

Db = Annotated[sqlite3.Connection, Depends(get_db)]


def _page(
    request: Request,
    db: sqlite3.Connection,
    *,
    error: str | None = None,
    result: packages.ImportResult | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "packages.html",
        {"decks": content_repo.list_decks(db), "error": error, "result": result},
        status_code=status_code,
    )


def _file_name(label: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.")[:60]
    return f"dunzo-{slug or 'deck'}.zip"


def _zip_response(db: sqlite3.Connection, deck_ids: list[str], file_name: str) -> Response:
    """Build the package in a temporary file and send it (the file is removed afterwards)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)  # noqa: SIM115 (closed below)
    try:
        with tmp:
            packages.write_package(db, get_settings().media_dir, deck_ids, tmp)
    except packages.PackageError as exc:
        os.unlink(tmp.name)
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except BaseException:
        os.unlink(tmp.name)
        raise
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=file_name,
        background=BackgroundTask(os.unlink, tmp.name),
    )


@router.get("/packages", response_class=HTMLResponse)
def packages_page(
    request: Request,
    db: Db,
    decks: int | None = None,
    notes: int = 0,
    players_added: int = 0,
    players_reused: int = 0,
    media: int = 0,
) -> Response:
    # After an import the browser is redirected here with the counts in the query string.
    result = None
    if decks is not None:
        result = packages.ImportResult(decks, notes, players_added, players_reused, media)
    return _page(request, db, result=result)


@router.get("/export", include_in_schema=False)
def export_all(db: Db) -> Response:
    deck_ids = [d.id for d in content_repo.list_decks(db)]
    return _zip_response(db, deck_ids, "dunzo-all-decks.zip")


@router.get("/decks/{deck_id}/export", include_in_schema=False)
def export_deck(deck_id: str, db: Db) -> Response:
    deck = content_repo.get_deck(db, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    return _zip_response(db, [deck.id], _file_name(deck.name))


@router.post("/packages/import", response_class=HTMLResponse)
def import_route(
    request: Request, db: Db, file: Annotated[UploadFile | None, File()] = None
) -> Response:
    try:
        if file is None or not file.filename:
            raise packages.PackageError("Choose a package file to import.")
        result = packages.import_package(db, get_settings().media_dir, file.file)
    except packages.PackageError as exc:
        db.rollback()  # the failure may have come after some rows were written
        return _page(request, db, error=str(exc), status_code=422)
    return RedirectResponse(
        f"/packages?decks={result.decks}&notes={result.notes}"
        f"&players_added={result.players_added}&players_reused={result.players_reused}"
        f"&media={result.media}",
        status_code=303,
    )
