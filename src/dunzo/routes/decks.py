"""Deck management pages: list, create, rename, delete (server-rendered, HTMX for the interactions)."""

import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from dunzo import content_repo, review_queue
from dunzo.db import get_db
from dunzo.templating import templates

router = APIRouter()

Db = Annotated[sqlite3.Connection, Depends(get_db)]


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _deck_list(request: Request, db: sqlite3.Connection, status_code: int = 200) -> Response:
    return templates.TemplateResponse(
        request,
        "_deck_list.html",
        {"decks": content_repo.list_decks(db)},
        status_code=status_code,
    )


def _require_deck(db: sqlite3.Connection, deck_id: str) -> content_repo.Deck:
    deck = content_repo.get_deck(db, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    return deck


def _deck_page(
    request: Request,
    db: sqlite3.Connection,
    deck: content_repo.Deck,
    *,
    rename_value: str | None = None,
    error: str | None = None,
    saved: bool = False,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "deck.html",
        {
            "deck": deck,
            "notes": content_repo.list_notes(db, deck.id),
            "counts": review_queue.counts(db, deck.id, time.time()),
            "rename_value": deck.name if rename_value is None else rename_value,
            "error": error,
            "saved": saved,
        },
        status_code=status_code,
    )


@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/decks", status_code=307)


@router.get("/decks", response_class=HTMLResponse)
def decks_page(request: Request, db: Db) -> Response:
    return templates.TemplateResponse(
        request, "decks.html", {"decks": content_repo.list_decks(db)}
    )


@router.get("/decks/{deck_id}", response_class=HTMLResponse)
def deck_page(deck_id: str, request: Request, db: Db, saved: int = 0) -> Response:
    return _deck_page(request, db, _require_deck(db, deck_id), saved=bool(saved))


@router.post("/decks", response_class=HTMLResponse)
def create_deck(request: Request, db: Db, name: Annotated[str, Form()] = "") -> Response:
    try:
        content_repo.create_deck(db, name)
    except ValueError as exc:
        if not _is_htmx(request):
            return templates.TemplateResponse(
                request,
                "decks.html",
                {"decks": content_repo.list_decks(db), "error": str(exc), "name": name},
                status_code=422,
            )
        # The page's script lets 422 responses swap; HX-Retarget sends them to the error slot.
        return templates.TemplateResponse(
            request,
            "_form_error.html",
            {"error": str(exc)},
            status_code=422,
            headers={"HX-Retarget": "#deck-form-error", "HX-Reswap": "innerHTML"},
        )
    if not _is_htmx(request):
        return RedirectResponse("/decks", status_code=303)
    return _deck_list(request, db)


@router.post("/decks/{deck_id}/rename", response_class=HTMLResponse)
def rename_deck(
    deck_id: str, request: Request, db: Db, name: Annotated[str, Form()] = ""
) -> Response:
    """Rename a deck. A whole-page form: 303 back to the deck, or a 422 re-render that keeps
    what the user typed."""
    deck = _require_deck(db, deck_id)
    try:
        content_repo.rename_deck(db, deck.id, name)
    except ValueError as exc:
        return _deck_page(request, db, deck, rename_value=name, error=str(exc), status_code=422)
    return RedirectResponse(f"/decks/{deck.id}?saved=1", status_code=303)


@router.delete("/decks/{deck_id}", response_class=HTMLResponse)
def delete_deck(deck_id: str, request: Request, db: Db) -> Response:
    # Idempotent: deleting a deck that is already gone (stale page) just refreshes the list.
    content_repo.delete_deck(db, deck_id)
    return _deck_list(request, db)
