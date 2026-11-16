import ast
import io
import zipfile
from pathlib import Path

import pytest

from dunzo import player_files as pf


def make_zip(members: dict[str, bytes | str]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


# ------------------------------------------------------------------------------ paths


@pytest.mark.parametrize("path", ["index.html", "css/main.css", "a/b/c.js", "img/ñandú.png", "x-1_2.js", ".hidden.js"])
def test_valid_paths(path):
    assert pf.normalize_path(path) == path


@pytest.mark.parametrize(
    "path",
    ["", "/abs.js", "../x.js", "a/../b.js", "a//b.js", "./a.js", "a/", "a\\b.js", "a b.js",
     "a?b.js", "a#b.js", "a%20b.js", "x" * 201, "a\x00.js"],
)
def test_invalid_paths(path):
    with pytest.raises(ValueError):
        pf.normalize_path(path)


@pytest.mark.parametrize("path", ["dunzo/api.js", "dunzo/x/y.js", "Dunzo/card.js", "dunzo"])
def test_reserved_directory(path):
    with pytest.raises(ValueError, match="reserved"):
        pf.normalize_path(path)


def test_similar_names_are_not_reserved():
    assert pf.normalize_path("dunzo.js") == "dunzo.js"
    assert pf.normalize_path("lib/dunzo/a.js") == "lib/dunzo/a.js"


def test_file_types():
    assert pf.file_type("a/b.CSS") == "text/css"
    assert pf.file_type("x.mjs") == "text/javascript"
    assert pf.file_type("f.woff2") == "font/woff2"
    for bad in ("run.exe", "noext", ".hidden", "a.php", "a.wasm"):
        with pytest.raises(ValueError, match="not supported"):
            pf.file_type(bad)


# ---------------------------------------------------------------------------- file sets


def test_check_files_accepts_a_valid_set():
    files = {"index.html": b"<p>", "css/a.css": b"a{}"}
    assert pf.check_files(files) == files


@pytest.mark.parametrize(
    "files, message",
    [
        ({}, "needs an index.html"),
        ({"page.html": b"x"}, "needs an index.html"),
        ({"sub/index.html": b"x"}, "needs an index.html"),
        ({"index.html": b""}, "is empty"),
        ({"index.html": b"x", "a.exe": b"x"}, "not supported"),
        ({"index.html": b"x", "dunzo/api.js": b"x"}, "reserved"),
        ({"index.html": "text"}, "not a file"),
    ],
)
def test_check_files_rejects(files, message):
    with pytest.raises(ValueError, match=message):
        pf.check_files(files)


def test_size_and_count_limits(monkeypatch):
    monkeypatch.setattr(pf, "MAX_FILE_BYTES", 5)
    with pytest.raises(ValueError, match="too large"):
        pf.check_files({"index.html": b"123456"})
    monkeypatch.setattr(pf, "MAX_FILE_BYTES", 100)
    monkeypatch.setattr(pf, "MAX_TOTAL_BYTES", 8)
    with pytest.raises(ValueError, match="altogether"):
        pf.check_files({"index.html": b"12345", "a.js": b"12345"})
    monkeypatch.setattr(pf, "MAX_TOTAL_BYTES", 1000)
    monkeypatch.setattr(pf, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="more than 1"):
        pf.check_files({"index.html": b"x", "a.js": b"x"})


def test_hash_depends_on_paths_and_bytes_not_order():
    a = {"index.html": b"1", "a.js": b"2"}
    assert pf.hash_files(a) == pf.hash_files(dict(reversed(list(a.items()))))
    assert pf.hash_files(a) != pf.hash_files({"index.html": b"1", "a.js": b"3"})
    assert pf.hash_files(a) != pf.hash_files({"index.html": b"1", "b.js": b"2"})
    assert pf.hash_files({"index.html": b"1", "a.js": b"2"}) != pf.hash_files({"index.html": b"12"})


# ------------------------------------------------------------------------------- zips


def test_read_zip():
    files = pf.read_zip(make_zip({"index.html": "<p>", "js/a.js": "var a;"}))
    assert files == {"index.html": b"<p>", "js/a.js": b"var a;"}


def test_zip_of_a_folder_is_unwrapped():
    files = pf.read_zip(make_zip({"myplayer/index.html": "x", "myplayer/a/b.css": "y"}))
    assert set(files) == {"index.html", "a/b.css"}


def test_folder_is_kept_when_there_is_a_top_level_index():
    files = pf.read_zip(make_zip({"index.html": "x", "lib/a.js": "y"}))
    assert set(files) == {"index.html", "lib/a.js"}


def test_two_top_level_folders_are_not_unwrapped():
    with pytest.raises(ValueError, match="needs an index.html"):
        pf.read_zip(make_zip({"a/index.html": "x", "b/x.js": "y"}))


def test_directory_entries_and_metadata_are_skipped():
    files = pf.read_zip(make_zip({"index.html": "x", "css/": "", "__MACOSX/._index.html": "junk",
                                  ".DS_Store": "junk", "css/.DS_Store": "junk"}))
    assert set(files) == {"index.html"}


@pytest.mark.parametrize(
    "members, message",
    [
        ({"index.html": "x", "../evil.js": "x"}, "not a valid file path"),
        ({"index.html": "x", "/abs.js": "x"}, "not a valid file path"),
        ({"index.html": "x", "dunzo/api.js": "x"}, "reserved"),
        ({"index.html": "x", "bad.exe": "x"}, "not supported"),
        ({"a.js": "x"}, "needs an index.html"),
        ({}, "needs an index.html"),
    ],
)
def test_bad_zip_contents(members, message):
    with pytest.raises(ValueError, match=message):
        pf.read_zip(make_zip(members))


def test_not_a_zip():
    with pytest.raises(ValueError, match="not a valid zip"):
        pf.read_zip(io.BytesIO(b"nope"))


def test_zip_limits(monkeypatch):
    monkeypatch.setattr(pf, "MAX_FILE_BYTES", 3)
    with pytest.raises(ValueError, match="too large"):
        pf.read_zip(make_zip({"index.html": "1234"}))
    monkeypatch.setattr(pf, "MAX_FILE_BYTES", 1000)
    monkeypatch.setattr(pf, "MAX_ZIP_MEMBERS", 1)
    with pytest.raises(ValueError, match="too many"):
        pf.read_zip(make_zip({"index.html": "x", "a.js": "y"}))


def test_duplicate_zip_entries_are_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "a")
        with pytest.warns(UserWarning):
            zf.writestr("index.html", "b")
    buf.seek(0)
    with pytest.raises(ValueError, match="twice"):
        pf.read_zip(buf)


def test_module_is_pure():
    tree = ast.parse(Path(pf.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"hashlib", "re", "zipfile", "collections", "typing"}, imported
