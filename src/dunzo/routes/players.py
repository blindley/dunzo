"""Player management: list, create, edit, delete, and the media attached to a player."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from dunzo import content_repo
from dunzo.config import get_settings
from dunzo.db import get_db
from dunzo.templating import templates

router = APIRouter()

Db = Annotated[sqlite3.Connection, Depends(get_db)]


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


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


def _media_list(request: Request, db: sqlite3.Connection, player_id: str) -> Response:
    return templates.TemplateResponse(
        request,
        "_player_media.html",
        {"player_id": player_id, "media_list": content_repo.list_player_media(db, player_id)},
    )


def _form_page(
    request: Request,
    db: sqlite3.Connection,
    *,
    player: content_repo.Player | None,
    values: dict[str, str],
    error: str | None = None,
    saved: bool = False,
    media_error: str | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "player_form.html",
        {
            "player": player,
            "values": values,
            "error": error,
            "saved": saved,
            "media_error": media_error,
            "media_list": content_repo.list_player_media(db, player.id) if player else [],
        },
        status_code=status_code,
    )


def _values_of(player: content_repo.Player) -> dict[str, str]:
    return {"name": player.name, "source": player.source, "note_schema": player.note_schema or ""}


@router.get("/players", response_class=HTMLResponse)
def players_page(request: Request, db: Db) -> Response:
    return templates.TemplateResponse(
        request, "players.html", {"players": content_repo.list_players(db)}
    )


@router.get("/players/new", response_class=HTMLResponse)
def new_player_page(request: Request, db: Db) -> Response:
    return _form_page(request, db, player=None, values={"name": "", "source": "", "note_schema": ""})


@router.post("/players", response_class=HTMLResponse)
def create_player(
    request: Request,
    db: Db,
    name: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "",
    note_schema: Annotated[str, Form()] = "",
) -> Response:
    try:
        player = content_repo.create_player(db, name, source, note_schema)
    except ValueError as exc:
        values = {"name": name, "source": source, "note_schema": note_schema}
        return _form_page(request, db, player=None, values=values, error=str(exc), status_code=422)
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
    source: Annotated[str, Form()] = "",
    note_schema: Annotated[str, Form()] = "",
) -> Response:
    player = _require_player(db, player_id)
    try:
        content_repo.update_player(db, player_id, name, source, note_schema)
    except ValueError as exc:
        values = {"name": name, "source": source, "note_schema": note_schema}
        return _form_page(request, db, player=player, values=values, error=str(exc), status_code=422)
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


@router.post("/players/{player_id}/media", response_class=HTMLResponse)
def upload_player_media(
    player_id: str,
    request: Request,
    db: Db,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    player = _require_player(db, player_id)
    try:
        if file is None or not file.filename:
            raise ValueError("Choose a file to upload.")
        media = content_repo.add_media(db, get_settings().media_dir, file.file, file.content_type)
        content_repo.link_player_media(db, player_id, media.id)
    except ValueError as exc:
        if _is_htmx(request):
            return templates.TemplateResponse(
                request,
                "_form_error.html",
                {"error": str(exc)},
                status_code=422,
                headers={"HX-Retarget": "#media-form-error", "HX-Reswap": "innerHTML"},
            )
        return _form_page(
            request,
            db,
            player=player,
            values=_values_of(player),
            media_error=str(exc),
            status_code=422,
        )
    if not _is_htmx(request):
        return RedirectResponse(f"/players/{player_id}", status_code=303)
    return _media_list(request, db, player_id)


@router.delete("/players/{player_id}/media/{media_id}", response_class=HTMLResponse)
def unlink_player_media(player_id: str, media_id: str, request: Request, db: Db) -> Response:
    _require_player(db, player_id)
    content_repo.unlink_player_media(db, player_id, media_id)  # idempotent
    return _media_list(request, db, player_id)
