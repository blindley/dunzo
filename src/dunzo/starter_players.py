"""Starter players: ready-made players the user can create with one click.

Used by the "New player" page (`/players/new?starter=basic`): the form is prefilled with the
starter's name and note schema, and submitting it without choosing a zip creates the player
from the starter's files. Nothing is stored until the user submits the form, after which it
is an ordinary player they can replace the files of.
"""

import json
from typing import Any

BASIC_SCHEMA = {
    "type": "object",
    "required": ["front", "back"],
    "properties": {
        "front": {"type": "string", "title": "Front"},
        "back": {"type": "string", "title": "Back"},
    },
}

BASIC_INDEX = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>Basic front/back</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <main id="card">
    <p id="prompt"></p>
    <button type="button" id="reveal">Show answer</button>
    <div id="after" hidden>
      <p id="answer"></p>
      <div id="grades">
        <button type="button" data-grade="1">Again (1)</button>
        <button type="button" data-grade="2">Hard (2)</button>
        <button type="button" data-grade="3">Good (3)</button>
        <button type="button" data-grade="4">Easy (4)</button>
      </div>
    </div>
  </main>
  <script src="dunzo/api.js"></script>
  <script src="dunzo/card.js"></script>
  <script src="player.js"></script>
</body>
</html>
"""

BASIC_STYLE = """\
body { font-family: system-ui, sans-serif; margin: 0; }
#card { max-width: 36rem; margin: 1.5rem auto; padding: 0 1rem; text-align: center; }
#prompt, #answer { font-size: 1.5rem; white-space: pre-wrap; }
#answer { border-top: 1px solid #8886; padding-top: 1rem; }
button { padding: 0.5rem 1rem; font: inherit; cursor: pointer; }
#grades { display: flex; gap: 0.5rem; justify-content: center; }
[hidden] { display: none !important; }
"""

BASIC_SOURCE = """\
// Basic front/back player.
// Note properties: "front" and "back".
// Card 0 asks the front and reveals the back. Card 1 (if the note has two cards)
// asks the back and reveals the front.
dunzo.onInit(function (card) {
  var props = card.note.properties || {};
  var reversed = card.index % 2 === 1;
  var question = text(reversed ? props.back : props.front);
  var answer = text(reversed ? props.front : props.back);

  function text(value) {
    return value == null ? "" : String(value);
  }

  var reveal = document.getElementById("reveal");
  var after = document.getElementById("after");
  document.getElementById("prompt").textContent = question;
  document.getElementById("answer").textContent = answer;

  var shown = false;
  function show() {
    if (shown) return;
    shown = true;
    reveal.hidden = true;
    after.hidden = false;
  }
  reveal.onclick = show;

  Array.prototype.forEach.call(document.querySelectorAll("[data-grade]"), function (button) {
    button.onclick = function () {
      dunzo.done(Number(button.dataset.grade));
    };
  });

  document.addEventListener("keydown", function (e) {
    if (!shown && (e.key === " " || e.key === "Enter")) {
      e.preventDefault();
      show();
    } else if (shown && e.key.length === 1 && "1234".indexOf(e.key) !== -1) {
      dunzo.done(Number(e.key));
    }
  });

  reveal.focus();
});
"""

BASIC_FILES: dict[str, bytes] = {
    "index.html": BASIC_INDEX.encode("utf-8"),
    "style.css": BASIC_STYLE.encode("utf-8"),
    "player.js": BASIC_SOURCE.encode("utf-8"),
}

STARTERS: dict[str, dict[str, Any]] = {
    "basic": {
        "name": "Basic front/back",
        "note_schema": json.dumps(BASIC_SCHEMA, indent=2),
        "files": BASIC_FILES,
    },
}


def get_starter(key: str) -> dict[str, Any] | None:
    """A starter as {"name", "note_schema", "files"} (files: {path: bytes}), or None if unknown.
    The result is a copy: changing it does not change the starter."""
    starter = STARTERS.get(key)
    return {**starter, "files": dict(starter["files"])} if starter else None
