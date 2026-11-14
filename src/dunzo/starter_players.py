"""Starter players: ready-made player sources the user can copy into a new player.

Used by the "New player" page (`/players/new?starter=basic`) to prefill the form. Nothing is
stored until the user saves it, after which it is an ordinary player they can edit.
"""

import json

BASIC_SCHEMA = {
    "type": "object",
    "required": ["front", "back"],
    "properties": {
        "front": {"type": "string", "title": "Front"},
        "back": {"type": "string", "title": "Back"},
    },
}

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

  function el(tag, content, css) {
    var node = document.createElement(tag);
    if (content != null) node.textContent = content;
    if (css) node.style.cssText = css;
    return node;
  }

  var buttonCss = "padding:.5rem 1rem;font:inherit;cursor:pointer";
  var page = el("div", null, "max-width:36rem;margin:1.5rem auto;text-align:center");
  var prompt = el("p", question, "font-size:1.5rem;white-space:pre-wrap");
  var reveal = el("button", "Show answer", buttonCss);
  var after = el("div", null, "display:none");
  after.appendChild(
    el("p", answer, "font-size:1.5rem;white-space:pre-wrap;border-top:1px solid #8886;padding-top:1rem")
  );
  var row = el("div", null, "display:flex;gap:.5rem;justify-content:center");
  [["Again", 1], ["Hard", 2], ["Good", 3], ["Easy", 4]].forEach(function (g) {
    var button = el("button", g[0] + " (" + g[1] + ")", buttonCss);
    button.onclick = function () {
      dunzo.done(g[1]);
    };
    row.appendChild(button);
  });
  after.appendChild(row);

  var shown = false;
  function show() {
    if (shown) return;
    shown = true;
    reveal.style.display = "none";
    after.style.display = "block";
  }
  reveal.onclick = show;
  document.addEventListener("keydown", function (e) {
    if (!shown && (e.key === " " || e.key === "Enter")) {
      e.preventDefault();
      show();
    } else if (shown && e.key.length === 1 && "1234".indexOf(e.key) !== -1) {
      dunzo.done(Number(e.key));
    }
  });

  document.body.textContent = "";
  page.appendChild(prompt);
  page.appendChild(reveal);
  page.appendChild(after);
  document.body.appendChild(page);
  reveal.focus();
});
"""

STARTERS: dict[str, dict[str, str]] = {
    "basic": {
        "name": "Basic front/back",
        "source": BASIC_SOURCE,
        "note_schema": json.dumps(BASIC_SCHEMA, indent=2),
    },
}


def get_starter(key: str) -> dict[str, str] | None:
    """Form values (name, source, note_schema) for a starter player, or None if unknown."""
    starter = STARTERS.get(key)
    return dict(starter) if starter else None
