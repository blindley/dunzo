"""Note management: the notes of a deck, create/edit (schema-driven form), delete, reorder,
and the media attached to a note."""

import json
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import FormData

from dunzo import content_repo, progress_repo, schema_form
from dunzo.config import get_settings
from dunzo.db import get_db
from dunzo.templating import templates

router = APIRouter()

Db = Annotated[sqlite3.Connection, Depends(get_db)]


async def _read_form(request: Request) -> FormData:
    return await request.form()


Body = Annotated[FormData, Depends(_read_form)]


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _require_deck(db: sqlite3.Connection, deck_id: str) -> content_repo.Deck:
    deck = content_repo.get_deck(db, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    return deck


def _require_note(db: sqlite3.Connection, note_id: str) -> content_repo.Note:
    note = content_repo.get_note(db, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


def _note_list(request: Request, db: sqlite3.Connection, deck: content_repo.Deck) -> Response:
    return templates.TemplateResponse(
        request, "_note_list.html", {"deck": deck, "notes": content_repo.list_notes(db, deck.id)}
    )


def _media_list(request: Request, db: sqlite3.Connection, note_id: str) -> Response:
    return templates.TemplateResponse(
        request,
        "_note_media.html",
        {"note_id": note_id, "media_list": content_repo.list_note_media(db, note_id)},
    )


def _render_form(
    request: Request,
    db: sqlite3.Connection,
    *,
    deck: content_repo.Deck,
    player: content_repo.Player,
    note: content_repo.Note | None,
    fields: list[schema_form.Field] | None,
    values: dict[str, Any],
    properties_json: str,
    card_count: str,
    selected_deck_id: str,
    before: str = "",
    errors: list[str] | None = None,
    saved: bool = False,
    shrink: dict[str, Any] | None = None,
    media_error: str | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "note_form.html",
        {
            "deck": deck,
            "player": player,
            "note": note,
            "fields": fields,
            "values": values,
            "properties_json": properties_json,
            "card_count": card_count,
            "decks": content_repo.list_decks(db) if note else [],
            "selected_deck_id": selected_deck_id,
            "before": before,
            "errors": errors or [],
            "saved": saved,
            "shrink": shrink,
            "media_error": media_error,
            "media_list": content_repo.list_note_media(db, note.id) if note else [],
        },
        status_code=status_code,
    )


def _pretty(properties: dict[str, Any]) -> str:
    return json.dumps(properties, indent=2, ensure_ascii=False)


def _read_properties(
    fields: list[schema_form.Field] | None,
    form: FormData,
    base: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Note properties from the submitted form: schema-driven fields, or the raw JSON box."""
    if fields is not None:
        return schema_form.parse_form(fields, form, base)
    try:
        return schema_form.parse_properties_json(str(form.get("properties_json", ""))), []
    except ValueError as exc:
        return {}, [str(exc)]


def _read_card_count(form: FormData) -> tuple[int | None, list[str]]:
    try:
        return content_repo.validate_card_count(int(str(form.get("card_count", "")).strip())), []
    except ValueError:
        return None, [f"Number of cards must be a whole number between 1 and {content_repo.MAX_CARD_COUNT}."]


def _form_values(
    fields: list[schema_form.Field] | None, form: FormData
) -> tuple[dict[str, Any], str]:
    values = schema_form.submitted_values(fields, form) if fields is not None else {}
    return values, str(form.get("properties_json", ""))


# ------------------------------------------------------------------- notes of a deck


@router.get("/decks/{deck_id}/notes/new", response_class=HTMLResponse)
def new_note_page(
    deck_id: str, request: Request, db: Db, player_id: str = "", before: str = ""
) -> Response:
    deck = _require_deck(db, deck_id)
    if not player_id:
        return templates.TemplateResponse(
            request,
            "note_choose_player.html",
            {"deck": deck, "players": content_repo.list_players(db), "before": before},
        )
    player = content_repo.get_player(db, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    fields = schema_form.build_fields(player.note_schema)
    return _render_form(
        request,
        db,
        deck=deck,
        player=player,
        note=None,
        fields=fields,
        values=schema_form.initial_values(fields, None) if fields is not None else {},
        properties_json="{}",
        card_count="1",
        selected_deck_id=deck.id,
        before=before,
    )


@router.post("/decks/{deck_id}/notes", response_class=HTMLResponse)
def create_note(deck_id: str, request: Request, db: Db, form: Body) -> Response:
    deck = _require_deck(db, deck_id)
    player = content_repo.get_player(db, str(form.get("player_id", "")))
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    before = str(form.get("before", ""))
    fields = schema_form.build_fields(player.note_schema)
    properties, errors = _read_properties(fields, form, None)
    count, count_errors = _read_card_count(form)
    errors += count_errors
    if not errors:
        try:
            content_repo.create_note(
                db, deck.id, player.id, count, properties, before_note_id=before or None
            )
        except content_repo.NoteInvalid as exc:
            errors = exc.messages
        except ValueError as exc:
            errors = [str(exc)]
    if errors:
        values, properties_json = _form_values(fields, form)
        return _render_form(
            request,
            db,
            deck=deck,
            player=player,
            note=None,
            fields=fields,
            values=values,
            properties_json=properties_json,
            card_count=str(form.get("card_count", "")),
            selected_deck_id=deck.id,
            before=before,
            errors=errors,
            status_code=422,
        )
    return RedirectResponse(f"/decks/{deck.id}", status_code=303)


@router.post("/decks/{deck_id}/notes/reorder", response_class=HTMLResponse)
def reorder_notes(deck_id: str, request: Request, db: Db, form: Body) -> Response:
    """Set the whole order of the deck's notes: the form field `order` repeated once per note id.

    Used by the drag-and-drop on the deck page. The ids must be exactly the deck's notes, each
    once; otherwise (a stale page, a note added or deleted elsewhere) nothing changes and the
    answer is 409. Only note positions change, never progress.
    """
    deck = _require_deck(db, deck_id)
    order = [str(value) for value in form.getlist("order")]
    try:
        content_repo.reorder_deck(db, deck.id, order)
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail="The notes in this deck have changed. Reload the page and try again.",
        ) from None
    if not _is_htmx(request):
        return RedirectResponse(f"/decks/{deck.id}", status_code=303)
    return _note_list(request, db, deck)


@router.delete("/decks/{deck_id}/notes/{note_id}", response_class=HTMLResponse)
def delete_note(deck_id: str, note_id: str, request: Request, db: Db) -> Response:
    deck = _require_deck(db, deck_id)
    note = content_repo.get_note(db, note_id)
    if note is not None and note.deck_id == deck.id:  # idempotent: already gone is fine
        content_repo.delete_note(db, note_id)
    return _note_list(request, db, deck)


@router.post("/decks/{deck_id}/notes/{note_id}/move", response_class=HTMLResponse)
def move_note(
    deck_id: str,
    note_id: str,
    request: Request,
    db: Db,
    direction: Annotated[str, Form()],
) -> Response:
    deck = _require_deck(db, deck_id)
    notes = content_repo.list_notes(db, deck.id)
    ids = [n.id for n in notes]
    if direction not in ("up", "down"):
        raise HTTPException(status_code=422, detail="direction must be 'up' or 'down'")
    if note_id in ids:
        index = ids.index(note_id)
        if direction == "up" and index > 0:
            content_repo.move_note(db, note_id, before_note_id=ids[index - 1])
        elif direction == "down" and index < len(ids) - 1:
            # just before the note two places down, or to the end if there is none
            after_next = ids[index + 2] if index + 2 < len(ids) else None
            content_repo.move_note(db, note_id, before_note_id=after_next)
    if not _is_htmx(request):
        return RedirectResponse(f"/decks/{deck.id}", status_code=303)
    return _note_list(request, db, deck)


# ------------------------------------------------------------------ a single note


@router.get("/notes/{note_id}", response_class=HTMLResponse)
def edit_note_page(note_id: str, request: Request, db: Db, saved: int = 0) -> Response:
    note = _require_note(db, note_id)
    deck = _require_deck(db, note.deck_id)
    player = content_repo.get_player(db, note.player_id)
    fields = schema_form.build_fields(player.note_schema)
    return _render_form(
        request,
        db,
        deck=deck,
        player=player,
        note=note,
        fields=fields,
        values=schema_form.initial_values(fields, note.properties) if fields is not None else {},
        properties_json=_pretty(note.properties),
        card_count=str(note.card_count),
        selected_deck_id=deck.id,
        saved=bool(saved),
    )


@router.post("/notes/{note_id}", response_class=HTMLResponse)
def update_note(note_id: str, request: Request, db: Db, form: Body) -> Response:
    note = _require_note(db, note_id)
    deck = _require_deck(db, note.deck_id)
    player = content_repo.get_player(db, note.player_id)
    fields = schema_form.build_fields(player.note_schema)
    properties, errors = _read_properties(fields, form, note.properties)
    count, count_errors = _read_card_count(form)
    errors += count_errors

    target_deck_id = str(form.get("deck_id", "")) or note.deck_id
    if not errors and content_repo.get_deck(db, target_deck_id) is None:
        errors = ["Deck not found."]
    if not errors:
        errors = content_repo.validate_properties(player.note_schema, properties)

    shrink = None
    if not errors and count < note.card_count:
        lost = progress_repo.count_progress_from(db, note.id, count)
        if lost and not form.get("confirm_shrink"):
            shrink = {"new_count": count, "card_states": lost.card_states, "reviews": lost.reviews}

    if errors or shrink:
        values, properties_json = _form_values(fields, form)
        return _render_form(
            request,
            db,
            deck=deck,
            player=player,
            note=note,
            fields=fields,
            values=values,
            properties_json=properties_json,
            card_count=str(form.get("card_count", "")),
            selected_deck_id=target_deck_id,
            errors=errors,
            shrink=shrink,
            status_code=409 if shrink else 422,
        )

    if count < note.card_count:
        progress_repo.delete_progress_from(db, note.id, count)  # only reached when confirmed
    content_repo.update_note(db, note.id, count, properties)
    if target_deck_id != note.deck_id:
        content_repo.move_note(db, note.id, deck_id=target_deck_id)  # to the end of that deck
    return RedirectResponse(f"/notes/{note.id}?saved=1", status_code=303)


# ------------------------------------------------------------------ media on a note


@router.post("/notes/{note_id}/media", response_class=HTMLResponse)
def upload_note_media(
    note_id: str,
    request: Request,
    db: Db,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    note = _require_note(db, note_id)
    try:
        if file is None or not file.filename:
            raise ValueError("Choose a file to upload.")
        media = content_repo.add_media(db, get_settings().media_dir, file.file, file.content_type)
        content_repo.link_note_media(db, note_id, media.id)
    except ValueError as exc:
        if _is_htmx(request):
            return templates.TemplateResponse(
                request,
                "_form_error.html",
                {"error": str(exc)},
                status_code=422,
                headers={"HX-Retarget": "#media-form-error", "HX-Reswap": "innerHTML"},
            )
        deck = _require_deck(db, note.deck_id)
        player = content_repo.get_player(db, note.player_id)
        fields = schema_form.build_fields(player.note_schema)
        return _render_form(
            request,
            db,
            deck=deck,
            player=player,
            note=note,
            fields=fields,
            values=schema_form.initial_values(fields, note.properties) if fields is not None else {},
            properties_json=_pretty(note.properties),
            card_count=str(note.card_count),
            selected_deck_id=deck.id,
            media_error=str(exc),
            status_code=422,
        )
    if not _is_htmx(request):
        return RedirectResponse(f"/notes/{note_id}", status_code=303)
    return _media_list(request, db, note_id)


@router.delete("/notes/{note_id}/media/{media_id}", response_class=HTMLResponse)
def unlink_note_media(note_id: str, media_id: str, request: Request, db: Db) -> Response:
    _require_note(db, note_id)
    content_repo.unlink_note_media(db, note_id, media_id)  # idempotent
    return _media_list(request, db, note_id)
