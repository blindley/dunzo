"""Player management: list, create (from a zip or a starter), edit, replace files, delete."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from dunzo import content_repo, starter_players
from dunzo.config import get_settings
from dunzo.db import get_db
from dunzo.templating import templates

router = APIRouter()

Db = Annotated[sqlite3.Connection, Depends(get_db)]

NEED_ZIP = (
    "Choose a zip file with the player's files (it needs an index.html at its top level)."
)


def _require_player(db: sqlite3.Connection, player_id: str) -> content_repo.Player:
    player = content_repo.get_player(db, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


def _player_list(
    request: Request, db: sqlite3.Connection, status_code: int = 200, headers: dict | None = None
) -> Response:
    return templates.TemplateResponse(
        request,
        "_player_list.html",
        {"players": content_repo.list_players(db)},
        status_code=status_code,
        headers=headers,
    )


def _form_page(
    request: Request,
    db: sqlite3.Connection,
    *,
    player: content_repo.Player | None,
    values: dict[str, str],
    starter: str = "",
    error: str | None = None,
    saved: bool = False,
    files_error: str | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "player_form.html",
        {
            "player": player,
            "values": values,
            "starter": starter,
            "error": error,
            "saved": saved,
            "files_error": files_error,
            "files": content_repo.list_player_files(db, player.id) if player else [],
        },
        status_code=status_code,
    )


def _values_of(player: content_repo.Player) -> dict[str, str]:
    return {"name": player.name, "note_schema": player.note_schema or ""}


def _zip_files(file: UploadFile | None) -> dict[str, bytes] | None:
    """The files in an uploaded zip, or None if no file was chosen. Raises ValueError."""
    if file is None or not file.filename:
        return None
    return content_repo.read_player_zip(file.file)


@router.get("/players", response_class=HTMLResponse)
def players_page(request: Request, db: Db) -> Response:
    return templates.TemplateResponse(
        request, "players.html", {"players": content_repo.list_players(db)}
    )


@router.get("/players/new", response_class=HTMLResponse)
def new_player_page(request: Request, db: Db, starter: str = "") -> Response:
    # ?starter=basic prefills the name and schema; submitting without a zip then creates the
    # player from the starter's files. Nothing is saved until the form is submitted. An
    # unknown starter gives the blank form.
    preset = starter_players.get_starter(starter)
    values = (
        {"name": preset["name"], "note_schema": preset["note_schema"]}
        if preset
        else {"name": "", "note_schema": ""}
    )
    return _form_page(request, db, player=None, values=values, starter=starter if preset else "")


@router.post("/players", response_class=HTMLResponse)
def create_player(
    request: Request,
    db: Db,
    name: Annotated[str, Form()] = "",
    note_schema: Annotated[str, Form()] = "",
    starter: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    try:
        files = _zip_files(file)
        if files is None:
            preset = starter_players.get_starter(starter) if starter else None
            if preset is None:
                raise ValueError(NEED_ZIP)
            files = preset["files"]
        player = content_repo.create_player(
            db, name, files, note_schema, media_dir=get_settings().media_dir
        )
    except ValueError as exc:
        values = {"name": name, "note_schema": note_schema}
        return _form_page(
            request, db, player=None, values=values, starter=starter, error=str(exc), status_code=422
        )
    return RedirectResponse(f"/players/{player.id}?saved=1", status_code=303)


@router.get("/players/{player_id}", response_class=HTMLResponse)
def edit_player_page(player_id: str, request: Request, db: Db, saved: int = 0) -> Response:
    player = _require_player(db, player_id)
    return _form_page(request, db, player=player, values=_values_of(player), saved=bool(saved))


@router.post("/players/{player_id}", response_class=HTMLResponse)
def update_player(
    player_id: str,
    request: Request,
    db: Db,
    name: Annotated[str, Form()] = "",
    note_schema: Annotated[str, Form()] = "",
) -> Response:
    player = _require_player(db, player_id)
    try:
        content_repo.update_player(db, player_id, name, note_schema)
    except ValueError as exc:
        values = {"name": name, "note_schema": note_schema}
        return _form_page(request, db, player=player, values=values, error=str(exc), status_code=422)
    return RedirectResponse(f"/players/{player_id}?saved=1", status_code=303)


@router.post("/players/{player_id}/files", response_class=HTMLResponse)
def replace_player_files(
    player_id: str,
    request: Request,
    db: Db,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    """Replace all of the player's files with the contents of an uploaded zip."""
    player = _require_player(db, player_id)
    try:
        files = _zip_files(file)
        if files is None:
            raise ValueError(NEED_ZIP)
        content_repo.replace_player_files(db, player_id, files, media_dir=get_settings().media_dir)
    except ValueError as exc:
        return _form_page(
            request,
            db,
            player=player,
            values=_values_of(player),
            files_error=str(exc),
            status_code=422,
        )
    return RedirectResponse(f"/players/{player_id}?saved=1", status_code=303)


@router.delete("/players/{player_id}", response_class=HTMLResponse)
def delete_player(player_id: str, request: Request, db: Db) -> Response:
    # Idempotent for a player that is already gone; refused (409) while notes use it.
    try:
        content_repo.delete_player(db, player_id)
    except content_repo.PlayerInUse as exc:
        count = exc.note_count
        message = (
            f"Cannot delete this player: {count} note{'' if count == 1 else 's'} still "
            "use it. Change or delete those notes first."
        )
        return templates.TemplateResponse(
            request,
            "_form_error.html",
            {"error": message},
            status_code=409,
            headers={"HX-Retarget": "#player-list-error", "HX-Reswap": "innerHTML"},
        )
    return _player_list(request, db)
