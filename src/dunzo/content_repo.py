"""SQL for content tables (deck, player, player_file, note, media and the note_media link).

Content only: nothing here may touch card_state or review_log (see progress_repo.py).
Functions take an open connection and do not commit; the request-scoped connection from
`dunzo.db.get_db` commits on success.

The non-SQL exceptions are `add_media` and the player-file functions (`create_player`,
`replace_player_files`), which also write bytes to `<media_dir>/<sha256>`. The media table and
the player_file table only hold the metadata. `media_dir` defaults to the configured one.
"""

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing.exceptions import Unresolvable

from dunzo import player_files
from dunzo.config import get_settings

MAX_DECK_NAME_LENGTH = 200
MAX_PLAYER_NAME_LENGTH = 200
MAX_MEDIA_BYTES = 50 * 1024 * 1024
MAX_CARD_COUNT = 1000
POSITION_GAP = 1024  # spacing of note.position values within a deck
_CHUNK = 1024 * 1024

# The rules for a player's files live in the pure player_files module; they are re-exported
# here so that packages.py (which may only use content_repo) can use them.
PLAYER_INDEX = player_files.INDEX
PLAYER_RESERVED_DIR = player_files.RESERVED_DIR
MAX_PLAYER_FILE_BYTES = player_files.MAX_FILE_BYTES
normalize_player_path = player_files.normalize_path
check_player_files = player_files.check_files
hash_player_files = player_files.hash_files
read_player_zip = player_files.read_zip

# Media types the app will store and serve. Anything else is refused at upload.
ALLOWED_MEDIA_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/svg+xml",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/mp4",
        "audio/aac",
        "video/mp4",
        "video/webm",
        "font/woff",
        "font/woff2",
        "font/ttf",
        "font/otf",
    }
)


class PlayerInUse(Exception):
    """Raised when deleting a player that notes still use."""

    def __init__(self, note_count: int):
        super().__init__(f"Player is used by {note_count} note(s).")
        self.note_count = note_count


def _normalize_name(name: str, label: str, max_length: int) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValueError(f"{label} name cannot be empty.")
    if len(cleaned) > max_length:
        raise ValueError(f"{label} name cannot be longer than {max_length} characters.")
    return cleaned


def _media_dir(media_dir: Path | None) -> Path:
    return media_dir if media_dir is not None else get_settings().media_dir


def _store_bytes(media_dir: Path, data: bytes) -> str:
    """Write the bytes to `<media_dir>/<sha256>` (once) and return the hash."""
    digest = hashlib.sha256(data).hexdigest()
    final_path = media_dir / digest
    if not final_path.exists():
        media_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=media_dir, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(data)
            os.replace(tmp_name, final_path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
    return digest


# ------------------------------------------------------------------------------ decks


@dataclass(frozen=True)
class Deck:
    id: str
    name: str
    note_count: int


def normalize_deck_name(name: str) -> str:
    """Trim the name and check it. Raises ValueError with a message fit to show the user."""
    return _normalize_name(name, "Deck", MAX_DECK_NAME_LENGTH)


_DECK_SELECT = """
    SELECT d.id, d.name, COUNT(n.id) AS note_count
    FROM deck d
    LEFT JOIN note n ON n.deck_id = d.id
"""


def _deck_from_row(row: sqlite3.Row) -> Deck:
    return Deck(id=row["id"], name=row["name"], note_count=row["note_count"])


def list_decks(conn: sqlite3.Connection) -> list[Deck]:
    """All decks, sorted by name (case-insensitive), with their note counts."""
    rows = conn.execute(
        _DECK_SELECT + " GROUP BY d.id ORDER BY d.name COLLATE NOCASE, d.id"
    ).fetchall()
    return [_deck_from_row(r) for r in rows]


def get_deck(conn: sqlite3.Connection, deck_id: str) -> Deck | None:
    row = conn.execute(_DECK_SELECT + " WHERE d.id = ? GROUP BY d.id", (deck_id,)).fetchone()
    return _deck_from_row(row) if row else None


def create_deck(conn: sqlite3.Connection, name: str) -> Deck:
    """Create a deck. Raises ValueError if the name is invalid."""
    cleaned = normalize_deck_name(name)
    deck_id = str(uuid.uuid4())
    conn.execute("INSERT INTO deck (id, name) VALUES (?, ?)", (deck_id, cleaned))
    return Deck(id=deck_id, name=cleaned, note_count=0)


def rename_deck(conn: sqlite3.Connection, deck_id: str, name: str) -> Deck | None:
    """Change a deck's name. Returns the updated deck, or None if there is no such deck.

    Raises ValueError if the name is invalid (the same rules as `create_deck`). Duplicate
    names are allowed. Only the name changes: notes, their order and any progress are untouched.
    """
    cleaned = normalize_deck_name(name)
    cursor = conn.execute("UPDATE deck SET name = ? WHERE id = ?", (cleaned, deck_id))
    if cursor.rowcount == 0:
        return None
    return get_deck(conn, deck_id)


def delete_deck(conn: sqlite3.Connection, deck_id: str) -> bool:
    """Delete a deck. Foreign keys cascade to its notes (and any progress for them).

    Returns False if there was no such deck.
    """
    cursor = conn.execute("DELETE FROM deck WHERE id = ?", (deck_id,))
    return cursor.rowcount > 0


# --------------------------------------------------------------------------- players


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    files_hash: str  # identifies the player's whole file set (see player_files.hash_files)
    note_schema: str | None  # JSON Schema as text, or None
    note_count: int


@dataclass(frozen=True)
class PlayerFile:
    path: str  # relative, '/'-separated
    hash: str  # sha256 of the bytes; also the file's name in the media directory
    mime: str
    size: int


def normalize_player_name(name: str) -> str:
    return _normalize_name(name, "Player", MAX_PLAYER_NAME_LENGTH)


def normalize_note_schema(text: str | None) -> str | None:
    """Validate a JSON Schema given as text. Blank means no schema (returns None).

    Returns the schema re-serialized as pretty-printed JSON. Raises ValueError if the text
    is not JSON, not an object, or not a valid JSON Schema (draft 2020-12).
    """
    if text is None or not text.strip():
        return None
    try:
        schema = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Note schema is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})."
        ) from None
    if not isinstance(schema, dict):
        raise ValueError("Note schema must be a JSON object.")  # noqa: TRY004 (user-facing)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"Note schema is not a valid JSON Schema: {exc.message}") from None
    return json.dumps(schema, indent=2, ensure_ascii=False)


def _as_files(files: str | Mapping[str, bytes]) -> Mapping[str, bytes]:
    """A string is shorthand for a player that is just an index.html with that text."""
    if isinstance(files, str):
        text = files.replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            raise ValueError("The player's index.html cannot be empty.")
        return {PLAYER_INDEX: text.encode("utf-8")}
    return files


_PLAYER_SELECT = """
    SELECT p.id, p.name, p.files_hash, p.note_schema, COUNT(n.id) AS note_count
    FROM player p
    LEFT JOIN note n ON n.player_id = p.id
"""


def _player_from_row(row: sqlite3.Row) -> Player:
    return Player(
        id=row["id"],
        name=row["name"],
        files_hash=row["files_hash"],
        note_schema=row["note_schema"],
        note_count=row["note_count"],
    )


def list_players(conn: sqlite3.Connection) -> list[Player]:
    """All players, sorted by name (case-insensitive), with the number of notes using each."""
    rows = conn.execute(
        _PLAYER_SELECT + " GROUP BY p.id ORDER BY p.name COLLATE NOCASE, p.id"
    ).fetchall()
    return [_player_from_row(r) for r in rows]


def get_player(conn: sqlite3.Connection, player_id: str) -> Player | None:
    row = conn.execute(_PLAYER_SELECT + " WHERE p.id = ? GROUP BY p.id", (player_id,)).fetchone()
    return _player_from_row(row) if row else None


def _store_player_files(
    conn: sqlite3.Connection, media_dir: Path, player_id: str, files: Mapping[str, bytes]
) -> None:
    for path, data in sorted(files.items()):
        digest = _store_bytes(media_dir, data)
        conn.execute(
            "INSERT INTO player_file (player_id, path, hash, mime, size) VALUES (?, ?, ?, ?, ?)",
            (player_id, path, digest, player_files.file_type(path), len(data)),
        )


def create_player(
    conn: sqlite3.Connection,
    name: str,
    files: str | Mapping[str, bytes],
    note_schema: str | None = None,
    player_id: str | None = None,
    media_dir: Path | None = None,
) -> Player:
    """Create a player from its files: a {path: bytes} mapping (it needs an `index.html`), or a
    string, which is shorthand for a player consisting of just an index.html with that text.

    Raises ValueError if the name, files or schema is invalid. Nothing is written then.

    The id is a new UUID unless `player_id` is given (package import keeps a player's id so
    the same player is recognised across installations). The caller makes sure it is unused.
    """
    cleaned_name = normalize_player_name(name)
    checked = player_files.check_files(_as_files(files))
    schema = normalize_note_schema(note_schema)
    player_id = player_id or str(uuid.uuid4())
    files_hash = player_files.hash_files(checked)
    conn.execute(
        "INSERT INTO player (id, name, files_hash, note_schema) VALUES (?, ?, ?, ?)",
        (player_id, cleaned_name, files_hash, schema),
    )
    _store_player_files(conn, _media_dir(media_dir), player_id, checked)
    return Player(player_id, cleaned_name, files_hash, schema, 0)


def update_player(
    conn: sqlite3.Connection, player_id: str, name: str, note_schema: str | None
) -> Player | None:
    """Change a player's name and note schema (not its files: see `replace_player_files`).

    Returns None if there is no such player. Raises ValueError if the new values are invalid.
    There is no versioning in v1: notes that use the player simply get the new code, and
    existing notes are not re-validated against a changed schema.
    """
    cleaned_name = normalize_player_name(name)
    schema = normalize_note_schema(note_schema)
    cursor = conn.execute(
        "UPDATE player SET name = ?, note_schema = ? WHERE id = ?",
        (cleaned_name, schema, player_id),
    )
    if cursor.rowcount == 0:
        return None
    return get_player(conn, player_id)


def replace_player_files(
    conn: sqlite3.Connection,
    player_id: str,
    files: str | Mapping[str, bytes],
    media_dir: Path | None = None,
) -> Player | None:
    """Replace all of a player's files with a new set (same rules as `create_player`).

    Returns None if there is no such player. Raises ValueError for an invalid file set, in
    which case the player keeps its current files. Bytes of files that are no longer used stay
    in the media directory (garbage collection is deferred).
    """
    if get_player(conn, player_id) is None:
        return None
    checked = player_files.check_files(_as_files(files))
    conn.execute("DELETE FROM player_file WHERE player_id = ?", (player_id,))
    _store_player_files(conn, _media_dir(media_dir), player_id, checked)
    conn.execute(
        "UPDATE player SET files_hash = ? WHERE id = ?",
        (player_files.hash_files(checked), player_id),
    )
    return get_player(conn, player_id)


def _file_from_row(row: sqlite3.Row) -> PlayerFile:
    return PlayerFile(path=row["path"], hash=row["hash"], mime=row["mime"], size=row["size"])


def list_player_files(conn: sqlite3.Connection, player_id: str) -> list[PlayerFile]:
    """A player's files, sorted by path."""
    rows = conn.execute(
        "SELECT path, hash, mime, size FROM player_file WHERE player_id = ? ORDER BY path",
        (player_id,),
    ).fetchall()
    return [_file_from_row(r) for r in rows]


def get_player_file(conn: sqlite3.Connection, player_id: str, path: str) -> PlayerFile | None:
    """One file of a player, by its exact path, or None."""
    row = conn.execute(
        "SELECT path, hash, mime, size FROM player_file WHERE player_id = ? AND path = ?",
        (player_id, path),
    ).fetchone()
    return _file_from_row(row) if row else None


def delete_player(conn: sqlite3.Connection, player_id: str) -> bool:
    """Delete a player and its file rows (the bytes stay until garbage collection exists).

    Raises PlayerInUse if any note uses the player. Returns False if there was no such player.
    """
    in_use = conn.execute(
        "SELECT COUNT(*) FROM note WHERE player_id = ?", (player_id,)
    ).fetchone()[0]
    if in_use:
        raise PlayerInUse(in_use)
    cursor = conn.execute("DELETE FROM player WHERE id = ?", (player_id,))
    return cursor.rowcount > 0


# ----------------------------------------------------------------------------- media


@dataclass(frozen=True)
class Media:
    id: str  # sha256 hex digest of the bytes
    mime: str
    size: int


def normalize_media_type(mime: str | None) -> str:
    """Lower-case the type, drop parameters, and check it against the allow-list."""
    cleaned = (mime or "").split(";", 1)[0].strip().lower()
    if cleaned not in ALLOWED_MEDIA_TYPES:
        raise ValueError(f"Files of type “{cleaned or 'unknown'}” are not supported.")
    return cleaned


def _media_from_row(row: sqlite3.Row) -> Media:
    return Media(id=row["id"], mime=row["mime"], size=row["size"])


def get_media(conn: sqlite3.Connection, media_id: str) -> Media | None:
    row = conn.execute("SELECT id, mime, size FROM media WHERE id = ?", (media_id,)).fetchone()
    return _media_from_row(row) if row else None


def add_media(
    conn: sqlite3.Connection, media_dir: Path, fileobj: BinaryIO, mime: str | None
) -> Media:
    """Store the bytes under `<media_dir>/<sha256>` and record them in the media table.

    Content-addressed: uploading the same bytes again returns the existing record. Raises
    ValueError for an unsupported type, an empty file or a file over MAX_MEDIA_BYTES.
    """
    cleaned_mime = normalize_media_type(mime)
    media_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    fd, tmp_name = tempfile.mkstemp(dir=media_dir, prefix=".upload-")
    try:
        with os.fdopen(fd, "wb") as tmp:
            while chunk := fileobj.read(_CHUNK):
                size += len(chunk)
                if size > MAX_MEDIA_BYTES:
                    raise ValueError(
                        f"File is too large (limit {MAX_MEDIA_BYTES // (1024 * 1024)} MB)."
                    )
                digest.update(chunk)
                tmp.write(chunk)
        if size == 0:
            raise ValueError("The file is empty.")
        media_id = digest.hexdigest()
        final_path = media_dir / media_id
        if final_path.exists():
            os.unlink(tmp_name)
        else:
            os.replace(tmp_name, final_path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    existing = get_media(conn, media_id)
    if existing is not None:
        return existing
    conn.execute("INSERT INTO media (id, mime, size) VALUES (?, ?, ?)", (media_id, cleaned_mime, size))
    return Media(media_id, cleaned_mime, size)


# ----------------------------------------------------------------------------- notes


class NoteInvalid(ValueError):
    """Note properties do not satisfy the player's note_schema. `messages` lists each problem."""

    def __init__(self, messages: list[str]):
        super().__init__(" ".join(messages))
        self.messages = messages


@dataclass(frozen=True)
class Note:
    id: str
    deck_id: str
    player_id: str
    player_name: str
    card_count: int
    position: int
    properties: dict[str, Any]
    created_at: float
    updated_at: float


def validate_card_count(value: Any) -> int:
    """Check a card count (1..MAX_CARD_COUNT). Raises ValueError with a user-facing message."""
    message = f"Number of cards must be a whole number between 1 and {MAX_CARD_COUNT}."
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CARD_COUNT:
        raise ValueError(message)
    return value


def validate_properties(note_schema: str | None, properties: Any) -> list[str]:
    """Check note properties against a player's note_schema. Returns a list of problems.

    No schema means any JSON object is accepted. Properties must always be a JSON object.
    """
    if not isinstance(properties, dict):
        return ["Note properties must be a JSON object."]
    if note_schema is None:
        return []
    validator = Draft202012Validator(json.loads(note_schema))
    try:
        errors = sorted(
            validator.iter_errors(properties), key=lambda e: [str(p) for p in e.absolute_path]
        )
    except Unresolvable as exc:
        return [f"The player's note schema could not be applied: {exc}"]
    messages = []
    for error in errors:
        path = ".".join(str(part) for part in error.absolute_path)
        messages.append(f"{path}: {error.message}" if path else error.message)
    return messages


_NOTE_SELECT = """
    SELECT n.id, n.deck_id, n.player_id, p.name AS player_name, n.card_count, n.position,
           n.properties, n.created_at, n.updated_at
    FROM note n JOIN player p ON p.id = n.player_id
"""


def _note_from_row(row: sqlite3.Row) -> Note:
    return Note(
        id=row["id"],
        deck_id=row["deck_id"],
        player_id=row["player_id"],
        player_name=row["player_name"],
        card_count=row["card_count"],
        position=row["position"],
        properties=json.loads(row["properties"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_note(conn: sqlite3.Connection, note_id: str) -> Note | None:
    row = conn.execute(_NOTE_SELECT + " WHERE n.id = ?", (note_id,)).fetchone()
    return _note_from_row(row) if row else None


def list_notes(conn: sqlite3.Connection, deck_id: str) -> list[Note]:
    """A deck's notes in deck order."""
    rows = conn.execute(_NOTE_SELECT + " WHERE n.deck_id = ? ORDER BY n.position", (deck_id,))
    return [_note_from_row(r) for r in rows.fetchall()]


def _assign_positions(conn: sqlite3.Connection, deck_id: str, ordered_ids: list[str]) -> None:
    """Give a deck's notes positions GAP, 2*GAP, ... in the given order.

    Positions are negated first so the UNIQUE (deck_id, position) constraint is never hit
    halfway through. `ordered_ids` must name every note in the deck.
    """
    conn.execute("UPDATE note SET position = -position WHERE deck_id = ?", (deck_id,))
    for index, note_id in enumerate(ordered_ids, start=1):
        conn.execute("UPDATE note SET position = ? WHERE id = ?", (index * POSITION_GAP, note_id))


def _renumber_deck(conn: sqlite3.Connection, deck_id: str) -> None:
    rows = conn.execute("SELECT id FROM note WHERE deck_id = ? ORDER BY position", (deck_id,))
    _assign_positions(conn, deck_id, [r["id"] for r in rows.fetchall()])


def _end_position(conn: sqlite3.Connection, deck_id: str, exclude_id: str | None = None) -> int:
    top = conn.execute(
        "SELECT MAX(position) FROM note WHERE deck_id = ? AND id IS NOT ?", (deck_id, exclude_id)
    ).fetchone()[0]
    return POSITION_GAP if top is None else top + POSITION_GAP


def _position_before(
    conn: sqlite3.Connection, deck_id: str, before_id: str, exclude_id: str | None = None
) -> int:
    """A free position just before note `before_id`, renumbering the deck if no gap is left.

    `exclude_id` is a note that is being moved and so does not count as a neighbour.
    """
    for _ in range(2):
        row = conn.execute(
            "SELECT position FROM note WHERE id = ? AND deck_id = ?", (before_id, deck_id)
        ).fetchone()
        if row is None:
            raise ValueError("The note to insert before is not in this deck.")
        high = row["position"]
        low = conn.execute(
            "SELECT MAX(position) FROM note WHERE deck_id = ? AND position < ? AND id IS NOT ?",
            (deck_id, high, exclude_id),
        ).fetchone()[0] or 0
        if high - low >= 2:
            return (low + high) // 2
        _renumber_deck(conn, deck_id)
    raise AssertionError("renumbering a deck did not open a gap")  # pragma: no cover


def create_note(
    conn: sqlite3.Connection,
    deck_id: str,
    player_id: str,
    card_count: int,
    properties: dict[str, Any],
    before_note_id: str | None = None,
) -> Note:
    """Create a note at the end of the deck, or just before `before_note_id`.

    Never touches progress tables. Raises ValueError (NoteInvalid for schema problems).
    """
    if get_deck(conn, deck_id) is None:
        raise ValueError("Deck not found.")
    player = get_player(conn, player_id)
    if player is None:
        raise ValueError("Player not found.")
    count = validate_card_count(card_count)
    problems = validate_properties(player.note_schema, properties)
    if problems:
        raise NoteInvalid(problems)
    if before_note_id is None:
        position = _end_position(conn, deck_id)
    else:
        position = _position_before(conn, deck_id, before_note_id)
    note_id = str(uuid.uuid4())
    now = time.time()
    conn.execute(
        "INSERT INTO note (id, deck_id, player_id, card_count, position, properties,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            note_id,
            deck_id,
            player_id,
            count,
            position,
            json.dumps(properties, ensure_ascii=False),
            now,
            now,
        ),
    )
    return get_note(conn, note_id)  # type: ignore[return-value]


def update_note(
    conn: sqlite3.Connection, note_id: str, card_count: int, properties: dict[str, Any]
) -> Note | None:
    """Change a note's card_count and properties. Returns None if there is no such note.

    Does not touch progress tables: when card_count shrinks, the caller decides what happens
    to progress for the removed indices (see progress_repo.delete_progress_from).
    """
    note = get_note(conn, note_id)
    if note is None:
        return None
    count = validate_card_count(card_count)
    player = get_player(conn, note.player_id)
    problems = validate_properties(player.note_schema if player else None, properties)
    if problems:
        raise NoteInvalid(problems)
    conn.execute(
        "UPDATE note SET card_count = ?, properties = ?, updated_at = ? WHERE id = ?",
        (count, json.dumps(properties, ensure_ascii=False), time.time(), note_id),
    )
    return get_note(conn, note_id)


def delete_note(conn: sqlite3.Connection, note_id: str) -> bool:
    """Delete a note; foreign keys cascade to its media links and any progress."""
    return conn.execute("DELETE FROM note WHERE id = ?", (note_id,)).rowcount > 0


def move_note(
    conn: sqlite3.Connection,
    note_id: str,
    deck_id: str | None = None,
    before_note_id: str | None = None,
) -> bool:
    """Move a note within its deck or to another deck.

    With `before_note_id` the note goes just before that note (which must be in the target
    deck); otherwise it goes to the end of the target deck. Returns False if there is no such
    note. Card indices and progress are untouched.
    """
    note = get_note(conn, note_id)
    if note is None:
        return False
    target = deck_id or note.deck_id
    if target != note.deck_id and get_deck(conn, target) is None:
        raise ValueError("Deck not found.")
    if before_note_id == note_id:
        return True
    if before_note_id is None:
        position = _end_position(conn, target, exclude_id=note_id)
    else:
        position = _position_before(conn, target, before_note_id, exclude_id=note_id)
    conn.execute(
        "UPDATE note SET deck_id = ?, position = ?, updated_at = ? WHERE id = ?",
        (target, position, time.time(), note_id),
    )
    return True


def reorder_deck(conn: sqlite3.Connection, deck_id: str, ordered_note_ids: list[str]) -> None:
    """Set the complete order of a deck's notes. Raises ValueError unless the ids are exactly
    the deck's notes, each once."""
    current = {r["id"] for r in conn.execute("SELECT id FROM note WHERE deck_id = ?", (deck_id,))}
    if len(ordered_note_ids) != len(set(ordered_note_ids)) or set(ordered_note_ids) != current:
        raise ValueError("The order must list every note in the deck exactly once.")
    _assign_positions(conn, deck_id, list(ordered_note_ids))


def link_note_media(conn: sqlite3.Connection, note_id: str, media_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO note_media (note_id, media_id) VALUES (?, ?)", (note_id, media_id)
    )


def unlink_note_media(conn: sqlite3.Connection, note_id: str, media_id: str) -> bool:
    """Remove the link only; the media row and file stay."""
    cursor = conn.execute(
        "DELETE FROM note_media WHERE note_id = ? AND media_id = ?", (note_id, media_id)
    )
    return cursor.rowcount > 0


def list_note_media(conn: sqlite3.Connection, note_id: str) -> list[Media]:
    rows = conn.execute(
        "SELECT m.id, m.mime, m.size FROM note_media nm JOIN media m ON m.id = nm.media_id"
        " WHERE nm.note_id = ? ORDER BY m.mime, m.id",
        (note_id,),
    ).fetchall()
    return [_media_from_row(r) for r in rows]


# ------------------------------------------------------------ media a card may use

# A card may use the media linked to its note. (A player's own files are not media: they are
# served from the player's directory.) This is the set a player can resolve to URLs.


def list_card_media(conn: sqlite3.Connection, note_id: str) -> list[Media]:
    """The media attached to the note."""
    return list_note_media(conn, note_id)


def get_card_media(conn: sqlite3.Connection, note_id: str, media_id: str) -> Media | None:
    """The media record if the note's cards may use it, otherwise None."""
    row = conn.execute(
        "SELECT m.id, m.mime, m.size FROM note_media nm JOIN media m ON m.id = nm.media_id"
        " WHERE nm.note_id = ? AND nm.media_id = ?",
        (note_id, media_id),
    ).fetchone()
    return _media_from_row(row) if row else None
