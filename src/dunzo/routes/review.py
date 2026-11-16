"""Review: the review page, the next-card and report endpoints, and the files that make up a
player's sandboxed frame.

Also here: collection stats (`/stats`, `/decks/{id}/stats`) and a note's review history
(`/notes/{id}/history`).

Flow: the review page (a small client shell) asks `/decks/{id}/review/next` for a card and
loads its `frame_url` into a sandboxed iframe. That URL is the player's `index.html` for this
one card: `/review/frame/{note_id}/{card_index}/index.html`. Everything the player loads by
relative URL comes from the same directory: the player's own files, plus the reserved
`dunzo/` directory, whose `api.js` is the player API and whose `card.js` carries the card's
data. When the player reports `done`, the shell POSTs the grade to
`/decks/{id}/review/report`, which records it and answers with the next card.
"""

import json
import sqlite3
import time
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from dunzo import content_repo, progress_repo, review_queue, scheduler
from dunzo.config import get_settings
from dunzo.db import get_db
from dunzo.templating import STATIC_DIR, templates

router = APIRouter()

Db = Annotated[sqlite3.Connection, Depends(get_db)]

MAX_DETAIL_BYTES = 64 * 1024  # what a player may attach to one report

# The frame's files are untrusted code. They run in an opaque origin (sandbox without
# allow-same-origin) and may load only scripts, styles, images, audio, video and fonts from
# this server, never make requests (connect-src 'none') and never navigate or submit. Inline
# scripts and styles are allowed: the sandbox is what contains them.
FRAME_CSP = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self' 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self' data:",
        "connect-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "frame-ancestors 'self'",
        "sandbox allow-scripts",
    ]
)
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
# Sent with every file of the frame, so that opening one directly is sandboxed too. The
# wildcard is for fonts and module scripts, which are fetched with CORS from an opaque origin.
_FRAME_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": FRAME_CSP,
    "Access-Control-Allow-Origin": "*",
}


def _now() -> float:
    """The current time as unix epoch seconds (a function so tests can replace it)."""
    return time.time()


def _require_deck(db: sqlite3.Connection, deck_id: str) -> content_repo.Deck:
    deck = content_repo.get_deck(db, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found")
    return deck


def _card_payload(db: sqlite3.Connection, note: content_repo.Note, index: int) -> dict[str, Any]:
    """The card as a player sees it: the note, which card of it this is, and its media."""
    media = [
        {"id": m.id, "mime": m.mime, "size": m.size, "url": f"/cards/{note.id}/media/{m.id}"}
        for m in content_repo.list_card_media(db, note.id)
    ]
    return {
        "note": {"id": note.id, "properties": note.properties, "media": media},
        "index": index,
        "card_count": note.card_count,
    }


def _next_payload(db: sqlite3.Connection, deck_id: str, now: float) -> JSONResponse:
    counts = review_queue.counts(db, deck_id, now)
    payload: dict[str, Any] = {"counts": {"due": counts.due, "new": counts.new}}
    queued = review_queue.next_card(db, deck_id, now)
    if queued is None:
        return JSONResponse({"done": True, **payload}, headers=_NO_STORE)
    note = content_repo.get_note(db, queued.note_id)
    if note is None:  # pragma: no cover (same connection: cannot vanish between queries)
        raise HTTPException(status_code=404, detail="Note not found")
    payload.update(
        {
            "done": False,
            "is_new": queued.is_new,
            "frame_url": f"/review/frame/{note.id}/{queued.card_index}/{content_repo.PLAYER_INDEX}",
            "card": _card_payload(db, note, queued.card_index),
        }
    )
    return JSONResponse(payload, headers=_NO_STORE)


@router.get("/decks/{deck_id}/review", response_class=HTMLResponse)
def review_page(deck_id: str, request: Request, db: Db) -> Response:
    deck = _require_deck(db, deck_id)
    return templates.TemplateResponse(request, "review.html", {"deck": deck})


@router.get("/decks/{deck_id}/review/next")
def next_card(deck_id: str, db: Db) -> Response:
    _require_deck(db, deck_id)
    return _next_payload(db, deck_id, _now())


@router.post("/decks/{deck_id}/review/report")
def report(deck_id: str, db: Db, body: Annotated[dict[str, Any], Body()]) -> Response:
    """Record the player's report for a card, then answer with the next card."""
    deck = _require_deck(db, deck_id)
    note_id = body.get("note_id")
    card_index = body.get("card_index")
    if not isinstance(note_id, str) or isinstance(card_index, bool) or not isinstance(card_index, int):
        raise HTTPException(status_code=422, detail="note_id and card_index are required.")
    try:
        grade = scheduler.validate_grade(body.get("grade"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    detail = body.get("detail")
    if detail is not None and (
        len(json.dumps(detail, ensure_ascii=False).encode("utf-8")) > MAX_DETAIL_BYTES
    ):
        raise HTTPException(status_code=422, detail="The report's detail is too large.")

    note = content_repo.get_note(db, note_id)
    if note is None or note.deck_id != deck.id or not 0 <= card_index < note.card_count:
        raise HTTPException(status_code=404, detail="Card not found")

    now = _now()
    existing = progress_repo.get_card_state(db, note.id, card_index)
    try:
        sched = scheduler.get_scheduler(existing.scheduler if existing else scheduler.DEFAULT_SCHEDULER)
        state_before = existing.state if existing else None
        result = sched.review(state_before if existing else sched.initial_state(), grade, now)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    progress_repo.record_review(
        db,
        note_id=note.id,
        card_index=card_index,
        reviewed_at=now,
        grade=grade,
        detail=detail,
        scheduler=sched.name,
        state_before=state_before,
        state_after=result.state,
        due=result.due,
    )
    return _next_payload(db, deck.id, now)


# ----------------------------------------------------------------- the player's frame


@router.get("/review/frame/{note_id}/{card_index}/{path:path}")
def frame_file(note_id: str, card_index: int, path: str, db: Db) -> Response:
    """One file of the player's directory, as seen while reviewing one card.

    The card is part of the URL so that `dunzo/card.js` (relative to `index.html`) can carry
    this card's data. An empty path is `index.html`. The `dunzo/` directory is reserved: only
    `api.js` and `card.js` exist there. Everything else is looked up by exact path among the
    player's files (never on disk by name), so no path can reach another file.
    """
    note = content_repo.get_note(db, note_id)
    if note is None or not 0 <= card_index < note.card_count:
        raise HTTPException(status_code=404, detail="Card not found")
    path = path or content_repo.PLAYER_INDEX
    reserved = content_repo.PLAYER_RESERVED_DIR
    if path == f"{reserved}/card.js":
        body = "window.dunzoCard = " + json.dumps(_card_payload(db, note, card_index), ensure_ascii=False) + ";\n"
        return Response(
            body,
            media_type="text/javascript; charset=utf-8",
            headers={**_FRAME_HEADERS, "Cache-Control": "no-store"},
        )
    if path == f"{reserved}/api.js":
        return FileResponse(
            STATIC_DIR / "player-api.js",
            media_type="text/javascript",
            headers={**_FRAME_HEADERS, "Cache-Control": "no-cache"},
        )
    if path == reserved or path.startswith(reserved + "/"):
        raise HTTPException(status_code=404, detail="File not found")
    entry = content_repo.get_player_file(db, note.player_id, path)
    file_path = get_settings().media_dir / entry.hash if entry else None
    if entry is None or file_path is None or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        file_path,
        media_type=entry.mime,
        headers={**_FRAME_HEADERS, "Cache-Control": "no-cache"},
    )


# ------------------------------------------------------------------- stats and history

STATS_DEFAULT_DAYS = 30
STATS_MAX_DAYS = 365


def _stats_page(
    request: Request, db: sqlite3.Connection, deck: content_repo.Deck | None, days: int
) -> Response:
    days = max(1, min(days, STATS_MAX_DAYS))
    result = review_queue.stats(db, _now(), deck.id if deck else None, days)
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "deck": deck,
            "stats": result,
            "days": days,
            "max_history": max(1, *(d.count for d in result.history)),
            "max_forecast": max(1, *(d.count for d in result.forecast)),
        },
    )


@router.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, db: Db, days: int = STATS_DEFAULT_DAYS) -> Response:
    return _stats_page(request, db, None, days)


@router.get("/decks/{deck_id}/stats", response_class=HTMLResponse)
def deck_stats_page(
    deck_id: str, request: Request, db: Db, days: int = STATS_DEFAULT_DAYS
) -> Response:
    return _stats_page(request, db, _require_deck(db, deck_id), days)


@router.get("/notes/{note_id}/history", response_class=HTMLResponse)
def note_history_page(note_id: str, request: Request, db: Db) -> Response:
    note = content_repo.get_note(db, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return templates.TemplateResponse(
        request,
        "note_history.html",
        {
            "note": note,
            "deck": content_repo.get_deck(db, note.deck_id),
            "reviews": progress_repo.list_reviews(db, note.id, limit=200),
        },
    )
