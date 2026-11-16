"""Content export and import: a package is a zip file holding decks, notes, players and media.

Content only. This module uses `content_repo` and nothing else from the app, so it cannot read
or write progress (card_state, review_log): a package never contains any.

Package layout (format version 2):

    manifest.json           decks with their notes in deck order, players (with their file
                            lists), media list
    players/<id>/<path>     each player's files, at the paths the player uses
    media/<sha256>          each media file's bytes

Notes are stored as an ordered list inside their deck; position numbers are not exported, so
import preserves the order without depending on them. Version 1 packages (a player was one
JavaScript file) are no longer read.

Import rules:
- Everything is checked before anything is written (structure, names, card counts, every
  player's files, every note's properties against its player's schema, media references). A
  package that fails those checks writes nothing.
- Decks and notes always get new ids, so importing never collides with existing content
  (importing the same package twice gives two copies of its decks).
- Players keep their id. If a player with that id already exists with the same files and
  schema it is reused. If it exists with different files or schema, the package's player is
  added as a separate copy with a new id; the existing player is never changed (there is no
  player versioning in v1).
- Media is content-addressed, so files already present are not stored twice. Each file's
  bytes must match the hash it is filed under.

Like the repos, `import_package` does not commit. Rows written before a late failure (a media
file that does not match its hash) are the caller's to roll back; the web route does.
"""

import json
import re
import sqlite3
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from dunzo import content_repo

FORMAT = "dunzo-package"
FORMAT_VERSION = 2
MANIFEST_NAME = "manifest.json"

MAX_MANIFEST_BYTES = 20 * 1024 * 1024
MAX_PACKAGE_BYTES = 1024 * 1024 * 1024  # total uncompressed size of all members
MAX_MEMBERS = 100_000

_MEDIA_ID = re.compile(r"[0-9a-f]{64}")


class PackageError(ValueError):
    """The package cannot be exported or imported. The message is fit to show the user."""


@dataclass(frozen=True)
class ImportResult:
    decks: int
    notes: int
    players_added: int  # new players, including copies made because an id was taken
    players_reused: int  # players already present with identical files and schema
    media: int  # media files in the package


# ------------------------------------------------------------------------------ export


def write_package(
    conn: sqlite3.Connection, media_dir: Path, deck_ids: list[str], fileobj: BinaryIO
) -> None:
    """Write a package with the given decks (in the given order) to a binary file object.

    Includes the players those decks' notes use (with all their files) and every media file
    linked to the notes. Raises PackageError for an unknown deck or a file missing on disk.
    """
    decks_out: list[dict[str, Any]] = []
    players: dict[str, content_repo.Player] = {}
    player_files: dict[str, list[content_repo.PlayerFile]] = {}
    media: dict[str, content_repo.Media] = {}

    for deck_id in dict.fromkeys(deck_ids):
        deck = content_repo.get_deck(conn, deck_id)
        if deck is None:
            raise PackageError("Deck not found.")
        notes_out = []
        for note in content_repo.list_notes(conn, deck.id):
            if note.player_id not in players:
                player = content_repo.get_player(conn, note.player_id)
                assert player is not None  # the foreign key guarantees it
                players[player.id] = player
                player_files[player.id] = content_repo.list_player_files(conn, player.id)
            linked = content_repo.list_note_media(conn, note.id)
            media.update({m.id: m for m in linked})
            notes_out.append(
                {
                    "id": note.id,
                    "player_id": note.player_id,
                    "card_count": note.card_count,
                    "properties": note.properties,
                    "media": [m.id for m in linked],
                }
            )
        decks_out.append({"id": deck.id, "name": deck.name, "notes": notes_out})

    manifest = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "decks": decks_out,
        "players": [
            {
                "id": p.id,
                "name": p.name,
                "note_schema": json.loads(p.note_schema) if p.note_schema else None,
                "files": [f.path for f in player_files[p.id]],
            }
            for p in players.values()
        ],
        "media": [{"id": m.id, "mime": m.mime, "size": m.size} for m in media.values()],
    }

    with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
        for p in players.values():
            for f in player_files[p.id]:
                path = media_dir / f.hash
                if not path.is_file():
                    raise PackageError(f"A file of player “{p.name}” ({f.path}) is missing on disk.")
                zf.write(path, f"players/{p.id}/{f.path}")
        for m in media.values():
            path = media_dir / m.id
            if not path.is_file():
                raise PackageError(f"A media file ({m.id[:12]}…) is missing on disk.")
            zf.write(path, f"media/{m.id}", compress_type=zipfile.ZIP_STORED)


# ------------------------------------------------------------------------------ import


@dataclass
class _PlayerPlan:
    id: str
    name: str
    files: dict[str, bytes]
    files_hash: str
    schema: str | None  # normalized, as stored


@dataclass
class _NotePlan:
    player_id: str
    card_count: int
    properties: dict[str, Any]
    media: list[str]


@dataclass
class _DeckPlan:
    name: str
    notes: list[_NotePlan] = field(default_factory=list)


def _object(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PackageError(f"{what} must be an object.")
    return value


def _array(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise PackageError(f"{what} must be a list.")
    return value


def _string(obj: dict[str, Any], key: str, what: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise PackageError(f"{what}: “{key}” must be text.")
    return value


def _media_refs(obj: dict[str, Any], declared: dict[str, str], what: str) -> list[str]:
    refs = _array(obj.get("media", []), f"{what}: media")
    for ref in refs:
        if not isinstance(ref, str) or not _MEDIA_ID.fullmatch(ref):
            raise PackageError(f"{what}: media entries must be sha256 hashes.")
        if ref not in declared:
            raise PackageError(f"{what}: refers to media {ref[:12]}… that the package lacks.")
    return list(dict.fromkeys(refs))


def _open(fileobj: BinaryIO) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(fileobj)
    except (zipfile.BadZipFile, OSError):
        raise PackageError("This is not a valid zip file.") from None
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        zf.close()
        raise PackageError("The package has too many files.")
    if sum(i.file_size for i in infos) > MAX_PACKAGE_BYTES:
        zf.close()
        raise PackageError("The package is too large.")
    return zf


def _read_member(zf: zipfile.ZipFile, name: str, limit: int, what: str) -> bytes:
    """Read a member by its exact name. Member names are never used as file paths."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise PackageError(f"The package has no {what}.") from None
    if info.file_size > limit:
        raise PackageError(f"The {what} is too large.")
    try:
        with zf.open(info) as member:
            data = member.read(limit + 1)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError):
        raise PackageError(f"The {what} could not be read.") from None
    if len(data) > limit:
        raise PackageError(f"The {what} is too large.")
    return data


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    raw = _read_member(zf, MANIFEST_NAME, MAX_MANIFEST_BYTES, "manifest.json")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PackageError("manifest.json is not valid JSON.") from None
    manifest = _object(manifest, "manifest.json")
    if manifest.get("format") != FORMAT:
        raise PackageError("This is not a dunzo package.")
    version = manifest.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PackageError("The package has no valid version.")
    if version > FORMAT_VERSION:
        raise PackageError(
            f"The package is format version {version}; this app understands up to "
            f"version {FORMAT_VERSION}. Upgrade the app to import it."
        )
    if version < FORMAT_VERSION:
        raise PackageError(
            f"The package is format version {version}, which this app no longer reads "
            f"(it reads version {FORMAT_VERSION}: a player is a directory of files)."
        )
    return manifest


def _canonical_uuid(value: Any, what: str) -> str:
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise PackageError(f"{what}: “id” must be a UUID.") from None
    if canonical != value:
        raise PackageError(f"{what}: “id” must be a lower-case UUID.")
    return canonical


def _parse_player(
    entry: dict[str, Any], what: str, zf: zipfile.ZipFile
) -> _PlayerPlan:
    player_id = _canonical_uuid(entry.get("id"), what)
    try:
        name = content_repo.normalize_player_name(_string(entry, "name", what))
        files: dict[str, bytes] = {}
        for raw_path in _array(entry.get("files", []), f"{what}: files"):
            if not isinstance(raw_path, str):
                raise PackageError(f"{what}: file paths must be text.")
            path = content_repo.normalize_player_path(raw_path)
            if path in files:
                raise PackageError(f"{what}: “{path}” is listed twice.")
            files[path] = _read_member(
                zf,
                f"players/{player_id}/{path}",
                content_repo.MAX_PLAYER_FILE_BYTES,
                f"file “{path}” of player “{name}”",
            )
        files = content_repo.check_player_files(files)
        raw_schema = entry.get("note_schema")
        if raw_schema is not None and not isinstance(raw_schema, dict):
            raise PackageError(f"{what}: “note_schema” must be an object or null.")
        schema = content_repo.normalize_note_schema(
            None if raw_schema is None else json.dumps(raw_schema)
        )
    except PackageError:
        raise
    except ValueError as exc:
        raise PackageError(f"{what}: {exc}") from None
    return _PlayerPlan(player_id, name, files, content_repo.hash_player_files(files), schema)


def _parse(
    manifest: dict[str, Any], zf: zipfile.ZipFile
) -> tuple[dict[str, str], dict[str, _PlayerPlan], list[_DeckPlan]]:
    """Check the whole package and return what to write: (media id -> mime, players, decks)."""
    names = set(zf.namelist())

    declared: dict[str, str] = {}
    for i, entry in enumerate(_array(manifest.get("media", []), "media"), start=1):
        what = f"Media entry {i}"
        entry = _object(entry, what)
        media_id = entry.get("id")
        if not isinstance(media_id, str) or not _MEDIA_ID.fullmatch(media_id):
            raise PackageError(f"{what}: “id” must be a sha256 hash.")
        if media_id in declared:
            raise PackageError(f"{what}: listed twice.")
        try:
            declared[media_id] = content_repo.normalize_media_type(entry.get("mime"))
        except ValueError as exc:
            raise PackageError(f"{what}: {exc}") from None
        if f"media/{media_id}" not in names:
            raise PackageError(f"{what}: the file media/{media_id[:12]}… is missing.")

    players: dict[str, _PlayerPlan] = {}
    for i, entry in enumerate(_array(manifest.get("players", []), "players"), start=1):
        what = f"Player {i}"
        plan = _parse_player(_object(entry, what), what, zf)
        if plan.id in players:
            raise PackageError(f"{what}: listed twice.")
        players[plan.id] = plan

    decks: list[_DeckPlan] = []
    for i, entry in enumerate(_array(manifest.get("decks", []), "decks"), start=1):
        what = f"Deck {i}"
        entry = _object(entry, what)
        try:
            deck = _DeckPlan(content_repo.normalize_deck_name(_string(entry, "name", what)))
        except ValueError as exc:
            if isinstance(exc, PackageError):
                raise
            raise PackageError(f"{what}: {exc}") from None
        for j, raw_note in enumerate(_array(entry.get("notes", []), f"{what}: notes"), start=1):
            where = f"Deck “{deck.name}”, note {j}"
            raw_note = _object(raw_note, where)
            player_ref = raw_note.get("player_id")
            player = players.get(player_ref) if isinstance(player_ref, str) else None
            if player is None:
                raise PackageError(f"{where}: its player is not in the package.")
            try:
                count = content_repo.validate_card_count(raw_note.get("card_count"))
            except ValueError as exc:
                raise PackageError(f"{where}: {exc}") from None
            properties = raw_note.get("properties", {})
            problems = content_repo.validate_properties(player.schema, properties)
            if problems:
                raise PackageError(f"{where}: " + " ".join(problems))
            deck.notes.append(_NotePlan(player.id, count, properties, _media_refs(raw_note, declared, where)))
        decks.append(deck)

    return declared, players, decks


def import_package(conn: sqlite3.Connection, media_dir: Path, fileobj: BinaryIO) -> ImportResult:
    """Import a package (see the module docstring for the rules). Raises PackageError."""
    zf = _open(fileobj)
    with zf:
        declared, players, decks = _parse(_read_manifest(zf), zf)

        for media_id, mime in declared.items():
            with zf.open(f"media/{media_id}") as member:
                try:
                    stored = content_repo.add_media(conn, media_dir, member, mime)
                except ValueError as exc:
                    raise PackageError(f"Media {media_id[:12]}…: {exc}") from None
                except (zipfile.BadZipFile, EOFError, OSError):
                    raise PackageError(f"Media {media_id[:12]}… could not be read.") from None
            if stored.id != media_id:
                raise PackageError(f"Media {media_id[:12]}… does not match its hash.")

        player_ids: dict[str, str] = {}  # package player id -> id in this database
        added = reused = 0
        for plan in players.values():
            existing = content_repo.get_player(conn, plan.id)
            if existing is None:
                local = content_repo.create_player(
                    conn, plan.name, plan.files, plan.schema, player_id=plan.id, media_dir=media_dir
                ).id
                added += 1
            elif existing.files_hash == plan.files_hash and existing.note_schema == plan.schema:
                local = existing.id
                reused += 1
            else:  # same id, different files or schema: keep both, never overwrite
                local = content_repo.create_player(
                    conn, plan.name, plan.files, plan.schema, media_dir=media_dir
                ).id
                added += 1
            player_ids[plan.id] = local

        note_count = 0
        for deck_plan in decks:
            deck = content_repo.create_deck(conn, deck_plan.name)
            for note_plan in deck_plan.notes:  # appended one after another: order preserved
                note = content_repo.create_note(
                    conn,
                    deck.id,
                    player_ids[note_plan.player_id],
                    note_plan.card_count,
                    note_plan.properties,
                )
                for media_id in note_plan.media:
                    content_repo.link_note_media(conn, note.id, media_id)
                note_count += 1

    return ImportResult(
        decks=len(decks),
        notes=note_count,
        players_added=added,
        players_reused=reused,
        media=len(declared),
    )
