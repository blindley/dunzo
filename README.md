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

- `src/dunzo/` – application code (`app.py` FastAPI app factory, `main.py` CLI entry point, `config.py` settings, `db.py` SQLite connection and schema)
- `tests/` – pytest tests (`uv run pytest`)
- `data/` – application data: `dunzo.db` and `media/` (gitignored)
- `scratch/` – throwaway experiments (gitignored)
