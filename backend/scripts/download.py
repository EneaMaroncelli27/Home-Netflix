import asyncio
import logging
import yt_dlp
import requests
from playwright.async_api import async_playwright
from backend.scripts.film import Film
from backend.scripts.urlgetter import URL, COVER_URL
from backend.db import add_film, update_status, get_status, delete_film_db, update_progress

log = logging.getLogger("download")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s:download: %(message)s"))
    log.addHandler(_h)
log.setLevel(logging.INFO)
log.propagate = False


async def download_film(movie : Film):
    async with async_playwright() as p:
        download_url = None
        found = asyncio.Event()
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        def handle_req(request):
            nonlocal download_url
            if "token" in request.url and "playlist/" in request.url and not "jwplayer" in request.url:
                download_url = request.url
                found.set()

        BLOCK_TYPES = {"image", "stylesheet", "font"}
        async def block_assets(route):
            if route.request.resource_type in BLOCK_TYPES:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_assets)
        page.on("request", handle_req)
        await page.goto(URL + f'/it/watch/{movie.id}', timeout=20000)
        try:
            await asyncio.wait_for(found.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pass
        await browser.close()
        if not download_url:
            log.warning("no playlist url found for %s (id=%s)", movie.title, movie.id)
            return
    log.info("found playlist url for %s (id=%s)", movie.title, movie.id)

    if add_film(movie) == "error":
        if get_status(movie.id) == "completed":
            return "already exists"

    class FwdLogger:
        def debug(self, msg):
            if not str(msg).startswith('[debug] '):
                log.info("yt-dlp: %s", msg)
        def info(self, msg): log.info("yt-dlp: %s", msg)
        def warning(self, msg): log.warning("yt-dlp: %s", msg)
        def error(self, msg): log.error("yt-dlp: %s", msg)

    last_written = -1
    def custom_progress_hook(d):
        nonlocal last_written
        if d['status'] == 'downloading':
            pct = d.get('_percent')
            if pct is None:
                total = d.get('total_bytes') or d.get('total_bytes_estimate')
                dl = d.get('downloaded_bytes')
                if total and dl:
                    pct = dl / total * 100
                else:
                    # Native HLS always knows the fragment count — use it as the source of truth.
                    fi = d.get('fragment_index')
                    fc = d.get('fragment_count')
                    pct = (fi / fc * 100) if fc else 0
            # Persist whole-percent steps so the shelf can poll progress (avoids a DB write per fragment).
            ipct = int(pct)
            if ipct != last_written:
                last_written = ipct
                update_progress(movie.id, min(ipct, 99))

    options = {
        # Prefer a single muxed stream so the NATIVE HLS downloader runs (reports
        # per-fragment progress); fall back to merge only if no muxed variant exists.
        'format': 'best/bestvideo+bestaudio',
        'logger': FwdLogger(),
        'outtmpl' : f'Movies/{movie.title}.mp4',
        'progress_hooks' : [custom_progress_hook],
        'concurrent_fragment_downloads': 16,
        'hls_prefer_native': True,
        'noprogress': True,
        'socket_timeout': 15,
        'retries': 5,
        'fragment_retries': 5,
        'skip_unavailable_fragments': False,
    }

    def _blocking_download():
        resp = requests.get(COVER_URL + movie.cover)
        if resp.status_code == 200:
            with open('Covers/' + movie.title + '.webp', 'wb') as f:
                f.write(resp.content)
        log.info("starting yt-dlp for %s url=%s", movie.title, download_url)
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([download_url])
        log.info("yt-dlp finished file for %s", movie.title)

    try:
        await asyncio.to_thread(_blocking_download)
    except Exception as e:
        log.exception("download failed for %s (id=%s): %s", movie.title, movie.id, e)
        delete_film_db(movie.id)
        raise
    update_progress(movie.id, 100)
    update_status(movie.id, "completed")
    log.info("completed %s (id=%s)", movie.title, movie.id)
