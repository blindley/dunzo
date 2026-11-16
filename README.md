# dunzo

Spaced-repetition flashcards web app (codename)

## Setup

```bash
uv sync
cp .env.example .env   # skip if .env already exists; edit PORT etc. as needed
```

## Run

```bash
uv run dunzo            # start the server
uv run dunzo --reload   # start with hot reload (development)
```

The port comes from `PORT` in `.env`. Visit `http://127.0.0.1:<PORT>/docs` for the API docs.

## Configuration (`.env`)

| Variable   | Required | Default     | Purpose                                              |
|------------|----------|-------------|------------------------------------------------------|
| `PORT`     | yes      |             | Port the server listens on                           |
| `HOST`     | no       | `127.0.0.1` | Interface to bind                                    |
| `DATA_DIR` | no       | `data`      | Where application data is stored (relative paths are relative to the project root) |

There are no database migrations. A database from an older schema version is refused at
startup with a message; move `data/` away (or point `DATA_DIR` elsewhere) to start fresh.

## Layout

- `src/dunzo/` – application code (`app.py` FastAPI app factory, `main.py` CLI entry point, `config.py` settings, `db.py` SQLite connection and schema, `scheduler.py` pure SM-2 scheduling, `review_queue.py` due and new cards plus stats, `player_files.py` pure rules for a player's files and zip reading, `packages.py` content export/import, `starter_players.py` ready-made players)
- `tests/` – pytest tests (`uv run pytest`)
- `data/` – application data: `dunzo.db` and `media/` (gitignored)
- `scratch/` – throwaway experiments (gitignored)

## Reviewing

Open a deck and press **Review**. Due cards come first (earliest due), then new cards in deck
order. To try it quickly: Players → "New basic front/back player", create it, add notes to a deck
with it, then review.

## Stats and history

**Stats** (top menu, or a deck's Stats button) shows card counts, reviews per day, a seven-day
forecast of due cards, the spread of answers and retention. Days are calendar days in the
server's local time zone. Each note's **History** link (on the deck page) lists its logged
reviews, newest first. History is stored per note, so deleting a note deletes its history.

## Import and export

**Import/Export** (top menu) exports all decks or one deck (also a deck page's Export button)
as a zip package, and imports one. A package holds decks with their notes in order, the
players those notes use (all their files), and the notes' media. It never contains review
progress (scheduling state or history), so a package can be shared freely.

- Layout (format version 2): `manifest.json`, `players/<id>/<path>` for each player file,
  `media/<sha256>`. Version 1 packages (a player was a single JavaScript file) are not read.
- Importing adds decks and notes as new copies (new ids), so importing a package twice gives
  two copies. Note order is preserved. Imported cards are new: they have no progress.
- A player is recognised by its id. One that is already here with the same files and schema is
  reused; one with the same id but different files or schema is added as a separate copy and
  the existing player is left alone.
- The whole package is checked before anything is written, including every player's files and
  every note's properties against its player's schema. A package that fails the checks imports
  nothing.

## Writing a player

A player is a small, mostly static web page: a directory of files with an `index.html` at its
top level, plus any scripts, styles, images, audio, video and fonts it uses (allowed types are
listed in `player_files.py`). Upload it as a zip on the Players page (a zip of the folder
itself also works), or replace the files of an existing player there. Paths use only letters,
digits and `. _ - @ + = ~`.

For each card, `index.html` is loaded into a sandboxed iframe (opaque origin, no network, no
access to the app). Everything it loads by relative URL comes from the player's directory. The
directory `dunzo/` is reserved: the app provides two files there.

```html
<script src="dunzo/api.js"></script>   <!-- window.dunzo -->
<script src="dunzo/card.js"></script>  <!-- window.dunzoCard: the card being reviewed -->
<script src="player.js"></script>      <!-- your code -->
```

```js
dunzo.onInit(function (card) {
  // card = { note: { id, properties, media: [{ id, mime, size, url }] }, index, card_count }
  // Use card.index to decide what to show: there is no built-in front/back.
  // dunzo.card is the same object. onInit runs once the page has loaded.
});
dunzo.done(grade, detail);   // grade 1-4 (Again, Hard, Good, Easy); detail is optional JSON
dunzo.mediaUrl(hash);        // URL for a media hash listed in card.note.media, else null
```

- `card.note.media` lists the media attached to the note (add them on the note's edit page). A
  player can load only those files, through `dunzo.mediaUrl`. A player's own files are not
  media: refer to them by relative path.
- The player draws its own UI (including grade buttons) in `document.body`.
- Inline scripts and styles, and scripts, styles, images, audio, video and fonts from the
  player's directory, are allowed; network requests (`fetch`, XHR), forms, navigation and
  `eval` are not. Put data a player needs in a `.js` file or in the note's properties.
- Each card gets its own frame URL (`/review/frame/<note id>/<card index>/index.html`), which is
  how `dunzo/card.js` can carry that card's data.
- Under the hood the frame reports to the host with `postMessage`: `{type: "done", grade, detail}`
  and `{type: "error", message}`. The card reaches the player as a script, not as a message.
