"""Serving stored media: GET /media/{id}, and the per-card route players use."""

import re
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from dunzo import content_repo
from dunzo.config import get_settings
from dunzo.db import get_db

router = APIRouter()

_MEDIA_ID = re.compile(r"[0-9a-f]{64}")

# Uploaded files are untrusted. Even if one were opened directly in the browser it must not
# be able to run script (SVG, for example), so: no sniffing, and a sandboxing CSP.
_SAFE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "public, max-age=31536000, immutable",  # content-addressed: never changes
}


@router.get("/media/{media_id}", include_in_schema=False)
def serve_media(media_id: str, db: Annotated[sqlite3.Connection, Depends(get_db)]) -> FileResponse:
    if not _MEDIA_ID.fullmatch(media_id):
        raise HTTPException(status_code=404)
    media = content_repo.get_media(db, media_id)
    path = get_settings().media_dir / media_id
    if media is None or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=media.mime, headers=_SAFE_HEADERS)


@router.get("/cards/{note_id}/media/{media_id}", include_in_schema=False)
def serve_card_media(
    note_id: str, media_id: str, db: Annotated[sqlite3.Connection, Depends(get_db)]
) -> FileResponse:
    """Media for a player while it reviews a note's cards.

    Only media linked to the note or to the note's player is served, so a player can resolve
    just the hashes the host listed for its card. The player's frame has an opaque origin, so
    fonts (which are fetched with CORS) need the wildcard; the bytes are not secret.
    """
    if not _MEDIA_ID.fullmatch(media_id):
        raise HTTPException(status_code=404)
    media = content_repo.get_card_media(db, note_id, media_id)
    path = get_settings().media_dir / media_id
    if media is None or not path.is_file():
        raise HTTPException(status_code=404)
    headers = {**_SAFE_HEADERS, "Access-Control-Allow-Origin": "*"}
    return FileResponse(path, media_type=media.mime, headers=headers)
