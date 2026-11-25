import os
import sqlite3
from backend.scripts.film import Film, Stored
from backend.scripts.utils import safe_name

# Keep the DB inside a directory so a missing file can't be turned into a
# directory by a Docker single-file bind mount. Override with DB_PATH env.
DB_PATH = os.environ.get('DB_PATH', 'data/database.db')
os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)

def get_conn():
    return sqlite3.connect(DB_PATH)

with get_conn() as conn:
    conn.execute("""
                CREATE TABLE IF NOT EXISTS films (
                id TEXT PRIMARY KEY NOT NULL,
                name VARCHAR(255) NOT NULL,
                type VARCHAR(255) NOT NULL,
                path VARCHAR(255) NOT NULL,
                cover VARCHAR(255),
                status VARCHAR(255) NOT NULL,
                seasons_count INTEGER NOT NULL DEFAULT 0,
                progress INTEGER NOT NULL DEFAULT 0,
                serie_title VARCHAR(255),
                season INTEGER NOT NULL DEFAULT 0,
                episode_n INTEGER NOT NULL DEFAULT 0,
                lang VARCHAR(8)
               );""")

    # Migrate tables created by older versions so every column carries the
    # same name as its field in film.py. Each step only runs when needed, so
    # this is a no-op on a table that is already up to date.
    cols = [c[1] for c in conn.execute("PRAGMA table_info(films)").fetchall()]
    if "title" in cols:
        conn.execute("ALTER TABLE films RENAME COLUMN title TO name")
    if "series_title" in cols:
        conn.execute("ALTER TABLE films RENAME COLUMN series_title TO serie_title")
    if "season_count" in cols:
        conn.execute("ALTER TABLE films RENAME COLUMN season_count TO seasons_count")

    # Columns that older tables never had.
    cols = [c[1] for c in conn.execute("PRAGMA table_info(films)").fetchall()]
    if "progress" not in cols:
        conn.execute("ALTER TABLE films ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
    if "serie_title" not in cols:
        conn.execute("ALTER TABLE films ADD COLUMN serie_title VARCHAR(255)")
    if "season" not in cols:
        conn.execute("ALTER TABLE films ADD COLUMN season INTEGER NOT NULL DEFAULT 0")
    if "episode_n" not in cols:
        conn.execute("ALTER TABLE films ADD COLUMN episode_n INTEGER NOT NULL DEFAULT 0")
    if "lang" not in cols:
        # Everything downloaded before language support was Italian.
        conn.execute("ALTER TABLE films ADD COLUMN lang VARCHAR(8) NOT NULL DEFAULT 'it'")

def check_already_exists(id):
    query = "SELECT 1 FROM films WHERE id = ?"
    with get_conn() as conn:
        film = conn.execute(query, (id,)).fetchone()
    return film is not None

def add_film(film : Film):
    if check_already_exists(film.id):
        return "error"
    # safe_name runs on the id too: it is client-supplied like the title, so
    # sanitising only the title would leave the same hole one field to the right.
    safe_t = safe_name(film.name) + '-' + safe_name(film.lang) + '-'+ safe_name(film.id)
    path = safe_t + '.mp4'
    cover = safe_t + '.webp'
    status = "downloading"
    query = "INSERT INTO FILMS (id, name, type, path, cover, status, seasons_count, progress, serie_title, season, episode_n, lang) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    with get_conn() as conn:
        conn.execute(query, (film.id, film.name, film.type, path, cover, status, film.seasons_count, 0,
                             film.serie_title, film.season, film.episode_n, film.lang))
    return "ok"

def get_status(id):
    query = "SELECT status FROM films WHERE id = ?"
    with get_conn() as conn:
        row = conn.execute(query, (id,)).fetchone()
    return row[0] if row else None

def update_status(id, status="completed"):
    query = "UPDATE films SET status = ? WHERE id = ?"
    with get_conn() as conn:
        conn.execute(query, (status, id))

def update_progress(id, progress):
    query = "UPDATE films SET progress = ? WHERE id = ?"
    with get_conn() as conn:
        conn.execute(query, (int(progress), id))

def delete_film_db(id):
    query = "DELETE FROM films WHERE id = ?"
    with get_conn() as conn:
        conn.execute(query,(id,))

def list_films():
    query = "SELECT id, name, type, path, cover, status, seasons_count, progress, serie_title, season, episode_n, lang FROM films"
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()
    return [Stored.model_validate(dict(row)) for row in rows]

def get_path(id : int):
    query = "SELECT path FROM films WHERE id = ?"
    with get_conn() as conn:
        path = conn.execute(query, (id,)).fetchone()
    if path == None:
        return None
    return path[0]

def get_cover(id : int):
    query = "SELECT cover FROM films WHERE id = ?"
    with get_conn() as conn:
        path = conn.execute(query, (id,)).fetchone()
    if path == None:
        return None
    return path[0]
    