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

## Layout

- `src/dunzo/` – application code (`app.py` FastAPI app factory, `main.py` CLI entry point, `config.py` settings, `db.py` SQLite connection and schema, `scheduler.py` pure SM-2 scheduling, `review_queue.py` due and new cards plus stats, `packages.py` content export/import, `starter_players.py` ready-made player sources)
- `tests/` – pytest tests (`uv run pytest`)
- `data/` – application data: `dunzo.db` and `media/` (gitignored)
- `scratch/` – throwaway experiments (gitignored)

## Reviewing

Open a deck and press **Review**. Due cards come first (earliest due), then new cards in deck
order. To try it quickly: Players → "New basic front/back player", save it, add notes to a deck
with it, then review.

## Stats and history

**Stats** (top menu, or a deck's Stats button) shows card counts, reviews per day, a seven-day
forecast of due cards, the spread of answers and retention. Days are calendar days in the
server's local time zone. Each note's **History** link (on the deck page) lists its logged
reviews, newest first. History is stored per note, so deleting a note deletes its history.

## Import and export

**Import/Export** (top menu) exports all decks or one deck (also a deck page's Export button)
as a zip package, and imports one. A package holds decks with their notes in order, the
players those notes use, and their media. It never contains review progress (scheduling state
or history), so a package can be shared freely.

- Layout: `manifest.json`, `players/<id>.js`, `media/<sha256>`.
- Importing adds decks and notes as new copies (new ids), so importing a package twice gives
  two copies. Note order is preserved. Imported cards are new: they have no progress.
- A player is recognised by its id. One that is already here with the same code and schema is
  reused; one with the same id but different code or schema is added as a separate copy and
  the existing player is left alone.
- The whole package is checked before anything is written, including every note's properties
  against its player's schema. A package that fails the checks imports nothing.

## Writing a player

A player is JavaScript that renders and runs one card. It runs in a sandboxed iframe (opaque
origin, no network, no access to the app) and gets a small API as `window.dunzo`:

```js
dunzo.onInit(function (card) {
  // card = { note: { id, properties, media: [{ id, mime, size, url }] }, index, card_count }
  // Use card.index to decide what to show: there is no built-in front/back.
});
dunzo.done(grade, detail);   // grade 1-4 (Again, Hard, Good, Easy); detail is optional JSON
dunzo.mediaUrl(hash);        // URL for a media hash listed in card.note.media, else null
```

- `card.note.media` lists the media attached to the note and to the player. A player can load
  only those files.
- The player draws its own UI (including grade buttons) in `document.body`.
- Scripts, styles, images, audio, video and fonts from this server are allowed; network
  requests, forms, navigation and `eval` are not.
- Under the hood the host and the frame exchange `postMessage` envelopes: host to player
  `{type: "init", card}`; player to host `{type: "ready"}`, `{type: "done", grade, detail}` and
  `{type: "error", message}`.
