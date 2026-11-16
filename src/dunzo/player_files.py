"""The rules for the files that make up a player. PURE: no database, no web.

A player is a small, mostly static web page: a set of files at relative paths, one of which
is `index.html` at the top level. The player's author supplies them (usually as a zip). The
reserved directory `dunzo/` is not part of the author's files: the server provides
`dunzo/api.js` and `dunzo/card.js` there when it serves the player to review a card.

This module checks paths, types, sizes and the file set as a whole, reads a zip into a
{path: bytes} dict, and computes the hash that identifies a file set.
"""

import hashlib
import re
import zipfile
from collections.abc import Mapping
from typing import BinaryIO

INDEX = "index.html"
RESERVED_DIR = "dunzo"

MAX_FILES = 500
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_PATH_LENGTH = 200
MAX_ZIP_MEMBERS = 5000

# File types a player may contain, by extension. The type is decided here, from the name.
FILE_TYPES = {
    "html": "text/html",
    "css": "text/css",
    "js": "text/javascript",
    "mjs": "text/javascript",
    "json": "application/json",
    "txt": "text/plain",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "avif": "image/avif",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "weba": "audio/webm",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "ttf": "font/ttf",
    "otf": "font/otf",
}

_SEGMENT = re.compile(r"[\w.@+=~-]+")


def normalize_path(path: str) -> str:
    """Check a file path: relative, '/'-separated, plain characters. Returns it unchanged.

    Raises ValueError (with a message fit to show the user) for anything else, including
    anything under the reserved `dunzo/` directory.
    """
    if not isinstance(path, str) or not path:
        raise ValueError("A file path cannot be empty.")
    if len(path) > MAX_PATH_LENGTH:
        raise ValueError(f"The file path “{path[:40]}…” is too long.")
    segments = path.split("/")
    for segment in segments:
        if segment in ("", ".", "..") or not _SEGMENT.fullmatch(segment):
            raise ValueError(
                f"“{path}” is not a valid file path. Use relative paths made of letters, "
                "digits and . _ - @ + = ~ only."
            )
    if segments[0].lower() == RESERVED_DIR:
        raise ValueError(f"“{path}”: the {RESERVED_DIR}/ directory is reserved for the app.")
    return path


def file_type(path: str) -> str:
    """The media type for a file name, from its extension. Raises ValueError if not allowed."""
    name = path.rsplit("/", 1)[-1]
    extension = name.rsplit(".", 1)[1].lower() if "." in name else ""
    mime = FILE_TYPES.get(extension)
    if mime is None:
        raise ValueError(f"“{path}”: files of this type are not supported in a player.")
    return mime


def check_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    """Check a whole file set and return it as a plain dict.

    Every path must be valid and of an allowed type, no file may be empty or too large, the
    set must be within the count and total size limits, and `index.html` must be present at
    the top level. Raises ValueError.
    """
    if len(files) > MAX_FILES:
        raise ValueError(f"A player cannot have more than {MAX_FILES} files.")
    checked: dict[str, bytes] = {}
    total = 0
    for path, data in files.items():
        normalize_path(path)
        file_type(path)
        if not isinstance(data, bytes | bytearray):
            raise ValueError(f"“{path}” is not a file.")  # noqa: TRY004 (user-facing)
        if not data:
            raise ValueError(f"“{path}” is empty.")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"“{path}” is too large (limit {MAX_FILE_BYTES // (1024 * 1024)} MB).")
        total += len(data)
        checked[path] = bytes(data)
    if total > MAX_TOTAL_BYTES:
        raise ValueError(f"The files are too large altogether (limit {MAX_TOTAL_BYTES // (1024 * 1024)} MB).")
    if INDEX not in checked:
        raise ValueError(f"A player needs an {INDEX} at its top level.")
    return checked


def hash_files(files: Mapping[str, bytes]) -> str:
    """One hash for a file set: it changes when any path or any file's bytes change, and does
    not depend on the order the files are given in."""
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(files[path]).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _is_junk(name: str) -> bool:
    return name.startswith("__MACOSX/") or name.rsplit("/", 1)[-1] in (".DS_Store", "Thumbs.db")


def _common_prefix(names: list[str]) -> str:
    """'folder/' when every file sits in one top-level folder and there is no top-level
    index.html (a zip made from the player's folder rather than its contents), else ''."""
    if not names or INDEX in names or not all("/" in n for n in names):
        return ""
    folders = {n.split("/", 1)[0] for n in names}
    return folders.pop() + "/" if len(folders) == 1 else ""


def read_zip(fileobj: BinaryIO) -> dict[str, bytes]:
    """Read a zip into a checked {path: bytes} dict (see `check_files`).

    Entry names are only ever used as dictionary keys, never as file paths. Directory entries
    and macOS/Windows metadata files are skipped; if everything is inside one folder, the
    folder is stripped. Raises ValueError with a message fit to show the user.
    """
    try:
        zf = zipfile.ZipFile(fileobj)
    except (zipfile.BadZipFile, OSError):
        raise ValueError("This is not a valid zip file.") from None
    with zf:
        all_infos = zf.infolist()
        if len(all_infos) > MAX_ZIP_MEMBERS:
            raise ValueError("The zip has too many files.")
        infos = [i for i in all_infos if not i.is_dir() and not _is_junk(i.filename)]
        prefix = _common_prefix([i.filename for i in infos])
        files: dict[str, bytes] = {}
        total = 0
        for info in infos:
            name = info.filename[len(prefix):]
            if name in files:
                raise ValueError(f"The zip lists “{name}” twice.")
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError(f"“{name}” is too large (limit {MAX_FILE_BYTES // (1024 * 1024)} MB).")
            try:
                with zf.open(info) as member:
                    data = member.read(MAX_FILE_BYTES + 1)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError):
                raise ValueError(f"“{name}” could not be read from the zip.") from None
            if len(data) > MAX_FILE_BYTES:
                raise ValueError(f"“{name}” is too large (limit {MAX_FILE_BYTES // (1024 * 1024)} MB).")
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("The files are too large altogether.")
            files[name] = data
    return check_files(files)
