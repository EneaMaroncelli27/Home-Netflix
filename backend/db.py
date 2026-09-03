import os
import sqlite3
from backend.scripts.film import Film, Stored
from backend.scripts.utlis import safe_name

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
                title VARCHAR(255) NOT NULL,
                type VARCHAR(255) NOT NULL,
                path VARCHAR(255) NOT NULL,
                cover VARCHAR(255),
                status VARCHAR(255) NOT NULL,
                season_count INTEGER NOT NULL DEFAULT 0,
                progress INTEGER NOT NULL DEFAULT 0
               );""")
    # Migrate older tables that predate the progress column.
    cols = [c[1] for c in conn.execute("PRAGMA table_info(films)").fetchall()]
    if "progress" not in cols:
        conn.execute("ALTER TABLE films ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")

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
    stem = safe_name(film.title) + '-' + safe_name(film.id)
    path = stem + '.mp4'
    cover = stem + '.webp'
    status = "downloading"
    query = "INSERT INTO FILMS (id, title, type, path, cover, status, season_count, progress) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    with get_conn() as conn:
        conn.execute(query, (film.id, film.title, film.type, path, cover, status, film.season_c, 0))
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
    query = "SELECT * FROM films"
    with get_conn() as conn:
        db_entries = conn.execute(query).fetchall()
    films = []
    for f in db_entries:
        films.append(Stored(f[0],f[1],f[2],f[3],f[4],f[5],f[6],f[7]))
    return films

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
    