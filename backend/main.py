import sys
from pathlib import Path
from backend.scripts.utlis import check_connection


ROOT = Path(__file__).resolve().parent.parent
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi import Request
from pydantic import BaseModel
from backend.scripts.search import search_by_title
from backend.scripts.download import download_film
from backend.scripts.urlgetter import URL, COVER_URL
from backend.db import list_films, delete_film_db, get_path, get_cover, get_status
import asyncio
import shutil
import qrcode
import urllib.parse
import base64
import io
import re
import uuid

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = ROOT / "frontend"
ONLINE_FRONTEND = FRONTEND_DIR / "index.html"
OFFLINE_FRONTEND = FRONTEND_DIR / "offline.html"
COVERS_DIR = ROOT / "Covers"
MOVIES_DIR = ROOT / "Movies"
COVERS_DIR.mkdir(exist_ok=True)
MOVIES_DIR.mkdir(exist_ok=True)

phone_sessions = {}

class FilmIn(BaseModel):
    title: str
    cover: str | None = None
    slug: str | None = None
    id: int


@app.get("/")
def home():
    return FileResponse(ONLINE_FRONTEND if check_connection() else OFFLINE_FRONTEND)


@app.post("/api/search")
def search(title: str):
    if not title:
        raise HTTPException(status_code=400, detail="Title required")
    films = search_by_title(title)
    return films


@app.post("/api/download")
async def download(film: FilmIn, background_tasks : BackgroundTasks):
    background_tasks.add_task(download_film,film) # download_film only reads .id and .title
    return {"status": "ok"}

@app.get('/api/config')
def config():
    return {"url": URL, "cover_base": COVER_URL}

@app.get('/api/films')
def get_film():
    return list_films()

@app.get('/api/delete')
def del_film(id):
    status = get_status(id)
    if status == None:
        raise HTTPException(status_code=400,detail="No film with the provided id")
    if status != "completed":
        raise HTTPException(status_code=403,detail="It's only possible to delete film that have finished downloading")
    film_name = get_path(id)
    cover_name = get_cover(id)
    if film_name:
        (MOVIES_DIR / Path(film_name).name).unlink(missing_ok=True)
    if cover_name:
        (COVERS_DIR / Path(cover_name).name).unlink(missing_ok=True)

    delete_film_db(id)
    return {"status":"ok"}


# utlis for browsing files in export
def _within(child: Path, parent: Path) -> bool:
    """True if child is parent itself or sits underneath it."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _drive_mounts(home: Path) -> list[Path]:
    """Resolved roots of mounted hard drives / removable media (Linux)."""
    user = home.name
    candidates = [Path("/media") / user, Path(f"/run/media/{user}"), Path("/media"), Path("/mnt")]
    mounts: list[Path] = []
    seen: set[Path] = set()
    for root in candidates:
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir(), key=lambda e: e.name.lower())
        except PermissionError:
            continue
        for mount in entries:
            if not mount.is_dir() or mount.name.startswith('.'):
                continue
            r = mount.resolve()
            if r in seen:
                continue
            seen.add(r)
            mounts.append(r)
    return mounts


@app.get('/api/browse')
def browse(path: str | None = None):
    # Browsing is locked to: home (launchpad only), the Videos folder, and any
    # mounted external drive. Everything else is off-limits.
    home = Path.home().resolve()
    videos = (home / "Videos")
    drives = _drive_mounts(home)
    allowed_roots = list(drives)
    if videos.is_dir():
        allowed_roots.append(videos.resolve())

    base = (Path(path).expanduser() if path else home).resolve()
    if not base.is_dir():
        raise HTTPException(status_code=404, detail="Not a directory")

    is_home = base == home
    if not (is_home or any(_within(base, r) for r in allowed_roots)):
        raise HTTPException(status_code=403, detail="That folder is off-limits")

    try:
        names = sorted(
            (e.name for e in base.iterdir() if e.is_dir() and not e.name.startswith('.')),
            key=str.lower,
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied for that folder")

    # Only show sub-folders the user is actually allowed to enter. At home this
    # leaves just Videos; drives are reached via the shortcuts below.
    dirs = [n for n in names if any(_within((base / n).resolve(), r) for r in allowed_roots)]

    # No climbing above a root (home or a drive/Videos top): hide the parent there.
    is_root = is_home or any(base == r for r in allowed_roots)
    parent = None if is_root else str(base.parent)

    shortcuts = [{"name": "Home", "path": str(home)}]
    if videos.is_dir():
        shortcuts.append({"name": "Videos", "path": str(videos.resolve())})
    for d in drives:
        shortcuts.append({"name": d.name, "path": str(d)})

    return {
        "path": str(base),
        "parent": parent,
        "dirs": dirs,
        "shortcuts": shortcuts,
        # Paths the UI may sit at or climb to (used to gate breadcrumb clicks).
        "roots": [str(home)] + [str(r) for r in allowed_roots],
    }

@app.post('/api/export')
def export(id, new_path : str):
    path = get_path(id)
    if path == None:
        raise HTTPException(status_code=400, detail="No film was found with that id")
    src = MOVIES_DIR / Path(path)
    dest = Path(new_path)
    if not src.is_file():
        raise HTTPException(status_code=404, detail="The source isn't a file or doesn't exist")
    if not dest.is_dir():
        raise HTTPException(status_code=400, detail="The destination has to be an existing directory")
    dest = dest / src.name
    if dest.exists():
        raise HTTPException(status_code=409, detail="A file by that name already exists in the destination")
    shutil.move(str(src),str(dest))
    delete_film_db(id)
    return {"status":"ok"}

# Bytes pushed per turn of the streaming loop below. Measured on this box:
# 256 KB gives ~1.3 GB/s, 1 MB ~1.9 GB/s, 4 MB no better. The counter still
# moves in fine enough steps for the progress bar at 1 MB.
CHUNK = 1024 * 1024


@app.post('/api/qr-download')
def qr_download(id, request: Request):
    path = get_path(id)
    if path == None:
            raise HTTPException(status_code=400, detail="No film was found with that id")
    src = MOVIES_DIR / Path(path)
    if not src.is_file():
        raise HTTPException(status_code=404, detail="That film is missing on disk")

    p_session = str(uuid.uuid4())
    phone_sessions[p_session] = {
        "path": path,              
        "size": src.stat().st_size,
        "sent": 0,
        "state": "waiting",
    }
    qr_img = qrcode.make(str(request.base_url).rstrip('/') + '/dwnld/' + p_session)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode()
    return {"data": f"data:image/png;base64,{encoded}", "token": p_session, "size": src.stat().st_size}

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _resolve_range(header: str | None, size: int):
    """Turn a Range header into inclusive byte bounds, or None for the whole file.

    StaticFiles used to do this for us. Serving the file by hand means picking it
    back up, otherwise a phone that loses WiFi has to start from zero.
    """
    if not header:
        return None
    match = _RANGE_RE.fullmatch(header.strip())
    if not match:
        return None
    first, last = match.group(1), match.group(2)
    if not first and not last:
        return None
    if not first:                                   # bytes=-500 -> the final 500 bytes
        start, end = max(0, size - int(last)), size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    if start > end or start >= size:
        raise HTTPException(status_code=416, detail="Requested range not satisfiable",
                            headers={"Content-Range": f"bytes */{size}"})
    return start, end


@app.get('/api/qr-progress/{sess}')
def qr_progress(sess: str):
    """Where the transfer has got to. Polled by the host while the phone pulls."""
    session = phone_sessions.get(sess)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown or expired code")
    return {
        "state": session["state"],
        "sent": session["sent"],
        "size": session["size"],
        "pct": min(100, round(session["sent"] * 100 / (session["size"] or 1))),
    }


@app.get('/dwnld/{sess}')
async def phone_download(sess: str, request: Request):
    # Push the film to the phone by hand, counting the bytes on the way out to track progress.
    session = phone_sessions.get(sess)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown or expired code")
    if session["state"] == "done":
        raise HTTPException(status_code=410, detail="This code has already been used - generate a new one")
    if session["state"] == "active":
        raise HTTPException(status_code=409, detail="This code is already downloading somewhere")

    src = MOVIES_DIR / Path(session["path"])
    if not src.is_file():
        session["state"] = "aborted"
        raise HTTPException(status_code=404, detail="The film went missing from disk")

    bounds = _resolve_range(request.headers.get("range"), session["size"])
    start, end = bounds if bounds else (0, session["size"] - 1)
    session["state"] = "active"
    
    async def stream():
        reached = start
        try:
            with open(src, 'rb') as f:
                await run_in_threadpool(f.seek, start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = await run_in_threadpool(f.read, min(CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    reached += len(chunk)
                    # The furthest byte reached, never a running total: a resume
                    # starts partway in, and summing would double-count the head.
                    session["sent"] = max(session["sent"], reached)
                    yield chunk
        finally:
            # Reached on a clean finish and on an early close alike. Anything
            # short of the last byte means the phone walked away.
            session["state"] = "done" if reached >= session["size"] else "aborted"

    filename = urllib.parse.quote(src.name)
    headers = {
        "Content-Length": str(end - start + 1),
        # Advertise resume support, otherwise the phone never asks for one.
        "Accept-Ranges": "bytes",
        # Nudge the phone into saving the film instead of opening a player.
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
    }
    if bounds:
        headers["Content-Range"] = f"bytes {start}-{end}/{session['size']}"
    return StreamingResponse(
        stream(),
        status_code=206 if bounds else 200,
        media_type="video/mp4",
        headers=headers,
    )


app.mount("/covers", StaticFiles(directory=COVERS_DIR), name="covers")
app.mount("/movies", StaticFiles(directory=MOVIES_DIR), name="movies")
app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend")