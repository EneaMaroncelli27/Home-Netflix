# In-depth: how Home-Netflix works

This document explains the internals — how requests flow, why certain design choices were made, and where to look when something breaks. For setup and everyday use, see [README.md](README.md).

## Project layout

```
backend/
  main.py            FastAPI app: routes, static file mounts, filesystem browsing
  db.py              SQLite access layer (schema, CRUD for the films table)
  scripts/
    film.py           Film / Stored data classes
    search.py         Queries the catalog site and parses results
    download.py       Playwright + yt-dlp download pipeline
    urlgetter.py      Resolves the catalog's current (rotating) domain via Telegram
    utlis.py          Connectivity check used to pick online/offline frontend
    tg_session.py     One-time CLI to generate a Telethon session string
frontend/
  index.html          Search page ("Find a film")
  offline.html         Shelf page: downloaded/downloading films, video player
  export.html          Export page: pick a film + destination, move it out
  qr-phone.html        Send-to-phone page: QR code + live transfer progress
  app.css              Shared styling for the desktop pages
  qr-icon.svg          Card glyph for send-to-phone (painted via CSS mask)
  trash-icon.svg       Card glyph for remove-from-shelf
  webos.html           The whole app again, ES5 + D-pad, for webOS TVs
  webos.css            Styling for the TV build only
webOS-webapp/
  index.html           TV launcher: finds the server on the LAN, redirects to webos.html
  appinfo.json.example Template for the per-install appinfo.json (gitignored)
  icon.png, largeIcon.png   Launcher icons
  *.ipk                Prebuilt packages, kept for reference
Movies/, Covers/, data/  Runtime storage (video files, cover art, SQLite DB)
```

There is no frontend build step. The HTML pages are served directly as static files, each with its own inline `<script>` block, and talk to the backend purely over `fetch()` — except `webos.html`, which uses `XMLHttpRequest` for reasons covered in its own section below.

## The domain rotation problem

The catalog this app scrapes (StreamingCommunity) rotates its public domain frequently to dodge blocking. There's no fixed API endpoint to hit. Instead, a public Telegram channel posts the current domain whenever it changes.

`backend/scripts/urlgetter.py` handles this:

- `get_new_url()` uses Telethon (a Telegram client library) to scan the most recent messages in the configured channel (`TG_CHANNEL`) for a URL matching the pattern `streamingcommunity*.<tld>`, newest message first.
- Because this needs an authenticated Telegram session, and creating one requires an interactive phone-number login, `tg_session.py` is a separate one-time script you run locally to produce a reusable `TG_SESSION` string. That string, plus `TG_API_ID`/`TG_API_HASH`, go into `.env` so the container can authenticate non-interactively.
- The Telethon client is async, but this module needs to expose a synchronous `URL` constant at import time. `_run_in_thread()` works around the "event loop already running" problem (Telethon's asyncio loop vs. uvicorn's) by spinning up a dedicated thread with its own fresh event loop for the one-off fetch.
- `URL` and `COVER_URL` are resolved exactly once, at first import, and reused by every other module (`search.py`, `download.py`, the `/api/config` route). If the domain rotates while the process is running, calling `refresh()` re-fetches and updates both in place — but nothing currently calls `refresh()` automatically; a container restart is the practical way to pick up a new domain today.
- If the Telegram env vars are missing or the fetch fails for any reason, `get_new_url()` swallows the error, logs it, and falls back to `DEFAULT_URL` (empty string), rather than crashing the app at import time.

The frontend never hardcodes the catalog domain. It calls `/api/config` on load to get `url` and `cover_base`, and builds cover image URLs from that.

## Search

`POST /api/search?title=...` → `backend/scripts/search.py::search_by_title()`.

The catalog's archive page embeds its search results as a JSON blob inside a `data-page` attribute on a `<div id="app">` (a common pattern for server-rendered Inertia.js-style apps). The scraper:

1. Requests `{URL}/it/archive?search={title}` with headers that mimic a real browser (referrer, `X-Requested-With`, `Sec-Fetch-*`) so the request isn't rejected as non-browser traffic.
2. Parses the HTML with BeautifulSoup, finds `#app`, and `json.loads()`s its `data-page` attribute.
3. Walks `props.titles`, and for each entry builds a `Film` (title, cover filename, slug, id), picking the cover image whose `type` is `"cover"` out of each title's `images` array.

No browser automation is needed for search — it's a plain HTTP request, since the search results are present in the initial HTML response.

## Download pipeline

`POST /api/download` takes a `FilmIn` body and immediately returns `{"status": "ok"}`, handing the actual work to a FastAPI `BackgroundTasks` task running `download_film()`. This keeps the request non-blocking; progress is polled separately via `/api/films`.

`backend/scripts/download.py::download_film()` does two very different things in sequence:

**1. Finding the real video URL (Playwright).** The catalog site doesn't expose a direct, stable download link — the actual HLS playlist URL is only visible in network traffic once the player loads. So a headless Chromium instance (Playwright) visits the watch page (`{URL}/it/watch/{id}`), with images/stylesheets/fonts blocked for speed, and a request listener watches for any request whose URL contains `token` and `playlist/` (excluding jwplayer's own internal requests). The first match found within a 10-second window is taken as the download URL; the browser is closed immediately after, whether or not anything was found.

**2. Downloading the file (yt-dlp).** Once a playlist URL is known, the film row is inserted into the DB (status `downloading`, progress `0`) via `add_film()`. If the id already exists and its status is `completed`, the function returns early — a rerun on an already-downloaded film is a no-op rather than a fresh save. yt-dlp then downloads the HLS stream directly to `Movies/{title}.mp4`, using native HLS with 16 concurrent fragment downloads. A custom progress hook computes a percentage from whichever fields yt-dlp provides for the current stream (native `_percent`, byte counts, or fragment index/count as a last resort) and writes it to the DB only when the whole-percent value changes, to avoid a DB write per fragment. Progress is capped at 99% during download and explicitly set to 100 + status `completed` only after `ydl.download()` returns successfully. The cover image is fetched synchronously in the same worker thread, right before the yt-dlp call.

If the download throws at any point, the film's DB row is deleted (`delete_film_db`) and the exception is re-raised into the background task (where FastAPI logs it, but there's no user-facing error surface for a failed background download today — the film simply disappears from the shelf).

The blocking parts (`requests.get` for the cover, `yt_dlp.YoutubeDL(...).download(...)`) run inside `asyncio.to_thread()` so they don't block the event loop that's also serving other requests (like the polling `/api/films` calls from the shelf page).

## Data model and storage

`backend/db.py` is a thin wrapper around a single SQLite table:

```sql
CREATE TABLE films (
  id INTEGER PRIMARY KEY,       -- catalog's own film id, reused as our primary key
  title VARCHAR(255) NOT NULL,
  path VARCHAR(255) NOT NULL,   -- filename under Movies/
  cover VARCHAR(255),           -- filename under Covers/
  status VARCHAR(255) NOT NULL, -- "downloading" | "completed"
  progress INTEGER NOT NULL DEFAULT 0
)
```

Each function (`add_film`, `update_status`, `update_progress`, `delete_film`, `list_films`, `get_path`) opens its own short-lived `sqlite3.connect()` — there's no connection pool or ORM. `films.py` defines two small data classes: `Film` (search result, before it's ever downloaded) and `Stored` (a DB row, after `add_film`).

On startup, `db.py` also runs a lightweight migration: if an existing `database.db` predates the `progress` column, it's added via `ALTER TABLE`. This is intentionally the only migration strategy — there's no versioned migration framework, since the schema is small and changes infrequently.

`DB_PATH` defaults to `data/database.db` but can be overridden with the `DB_PATH` env var. The parent directory is always created up front — this matters specifically under Docker, where a bind mount of a not-yet-existing file can otherwise turn into a directory mount and break things silently.

## Filesystem browsing and export

`GET /api/browse` powers the folder picker on the export page. It's deliberately sandboxed: a caller can only browse the user's home directory (top level only — to reach shortcuts), `~/Videos`, or any directory mounted under `/media/<user>`, `/run/media/<user>`, `/media`, or `/mnt` (covers common Linux external-drive mount points). `_within()` checks a candidate path is the allowed root or nested under it; `_drive_mounts()` enumerates what's actually mounted right now, so newly plugged-in drives show up without a restart. The response includes the current directory's subfolders, a parent link (omitted at a root, to block climbing above it), a shortcuts list, and the full set of allowed roots so the frontend can grey out unreachable breadcrumb segments.

`POST /api/export` takes a film `id` and a `new_path` destination directory. It looks up the film's stored path, validates the source file exists and the destination is an existing directory, guards against overwriting a same-named file already there, then does a plain `shutil.move()` followed by `delete_film(id)` — export is a one-way move, not a copy, and it always removes the film from the shelf/DB regardless of whether the destination is on the same filesystem (Python's `shutil.move` copies + deletes automatically when moving across filesystems, e.g. a container volume mount to `/media/...`).

## Deleting a film

`GET /api/delete?id=...` removes a film for good: it unlinks `Movies/{path}` and `Covers/{cover}` (both `missing_ok=True`, so a file already gone off disk is not an error) and then drops the DB row. It refuses with 400 if no film carries that id, and with 403 if the film's status is anything other than `completed` — deleting a row out from under a running download would leave the yt-dlp worker writing to a file nobody tracks and the progress hook updating a row that no longer exists. Only the DB name is passed through `Path(...).name` before joining, so a stored path can't walk out of `Movies/`.

The `delete_film` DB helper was renamed to `delete_film_db` in the same change, purely so the route handler could take the plain name; `download.py` calls the renamed helper on a failed download exactly as before.

## Phone handoff (send to phone)

The goal is to get a finished film off the host and onto a phone without either device knowing anything about the other beforehand. The whole exchange is three endpoints and a dictionary.

**Minting a code.** `POST /api/qr-download?id=...` looks the film up, confirms the file is on disk, and creates a session keyed by a fresh `uuid4()` in the module-level `phone_sessions` dict: `{path, size, sent: 0, state: "waiting"}`. It then renders `{request.base_url}/dwnld/{token}` into a PNG with the `qrcode` library, base64s it, and returns it as a `data:` URI alongside the token and the file size. Using `request.base_url` rather than a configured hostname is what makes the code contain the address the browser actually reached the host on — which is the LAN address when the shelf page is opened from another machine, and `localhost` when it isn't (see the rough edges).

**Serving the file.** `GET /dwnld/{token}` streams the film out by hand rather than handing it to `StaticFiles`, because the byte counting is the entire point: the host cannot observe the phone, so the only progress signal available is what it has managed to push out of the socket. The handler refuses a token that is already `active` (409) or `done` (410) — a code is good for one transfer. The body is a `StreamingResponse` over a generator that reads the file in 1 MB chunks (`CHUNK`; measured on this box, 256 KB gave ~1.3 GB/s and 1 MB ~1.9 GB/s, with no gain past that), each `f.seek`/`f.read` pushed through `run_in_threadpool` so a multi-gigabyte transfer never parks the event loop that is also serving the polling shelf.

**Resume.** `_resolve_range()` reimplements the slice of RFC 7233 that `StaticFiles` would otherwise have provided: `bytes=start-end`, `bytes=start-`, and the suffix form `bytes=-n`, returning inclusive bounds or `None` for the whole file, and raising 416 with a `Content-Range: bytes */{size}` header when the range falls outside the file. The response advertises `Accept-Ranges: bytes` (without it the phone never asks for a resume), sets `Content-Range` and status 206 for a partial request, and sends `Content-Disposition: attachment` so the phone saves the film instead of opening it in a player.

**Progress accounting.** The generator tracks `reached` — the furthest byte position it has written — and stores `session["sent"] = max(session["sent"], reached)`. It is deliberately a high-water mark and not a running total: a resumed transfer starts partway into the file, so summing chunk lengths across attempts would double-count the head and sail past 100%. The generator's `finally` block runs on a clean finish and on an early client disconnect alike, and sets `state` to `done` if the last byte was reached and `aborted` otherwise. `GET /api/qr-progress/{token}` just reads that state back out, returning `state`, `sent`, `size` and a rounded `pct`.

`frontend/qr-phone.html` is the desktop half. It mints a code on load, fetches `/api/films` purely to put the film's title above the code (the QR endpoint doesn't return one), and polls progress twice a second. `aborted` is treated as a pause rather than a failure — it keeps polling, since the phone can come back and range-request the rest. A finished code is greyed out and shrunk away in CSS, and "New code" mints a fresh session.

## Frontend

Four independent static pages for the desktop, no framework, no bundler, plus a separate TV build covered in the next section:

- **index.html** — search box, results grid, calls `/api/search` and `/api/download`.
- **offline.html** — the shelf. Polls `/api/films` once, then re-polls every second only while at least one film has status `downloading`. To avoid flicker, it distinguishes between "the set of films or their statuses changed" and "only progress changed" (in-place progress bar update via `patchProgress()`, no DOM rebuild). Clicking a completed film's cover opens a modal `<video>` player pointed at `/movies/{path}`. `render()` reconciles the grid against the data by id rather than wiping and rebuilding: cards for films that are gone are removed, new films are appended, and a card is only rebuilt when its `data-status` actually changed — so an unchanged card keeps its DOM node, its focus and its already-played rise animation. A finished film's card foot carries two icon buttons: a link to `qr-phone.html?id=...` and a delete button. Both are only rendered for `completed` films, mirroring the backend's 403 rather than offering a control that would fail.
- **qr-phone.html** — the send-to-phone page described above.
- **export.html** — lists completed films for selection, and a folder browser modal that walks `/api/browse` interactively, rendering clickable breadcrumbs and a shortcuts bar. The last-used destination is remembered in `localStorage`.

The two card glyphs (`qr-icon.svg`, `trash-icon.svg`) are painted through a CSS `mask` on a `::before` rather than dropped in as `<img>`, so they take `currentColor` and can change colour on hover like any other icon. The delete button hovers to `--ember`, the one warning colour in the palette, reserved for the single control on a card that does not give the film back.

`backend/main.py` decides which landing page to serve at `/` based on `utlis.check_connection()` — a 3-second timeout request to `https://www.google.com`. If it fails, `offline.html`'s sibling `offline.html`-labeled fallback (actually served from the same file structure) is returned instead of the search page, since search requires reaching the catalog site.

## The webOS TV build

`frontend/webos.html` is the whole app a second time — search, shelf, player, delete, send-to-phone — in one file, with `webos.css` beside it. It is a separate build rather than a few patches to the desktop pages, and the reason is a hard one.

**Why a rewrite and not a patch.** webOS TV browsers run Chromium 38 (webOS 3.x) through 94 (webOS 23), depending on model year. The desktop pages use `??`, which is a *syntax error* below Chromium 80. A syntax error in an inline `<script>` discards the entire block at parse time, before a single statement runs, so none of the listeners at the bottom of `index.html` were ever attached. The page painted perfectly and then ignored every button — that was the original "the TV doesn't respond to the remote" report, and no amount of pressing was going to fix it. So `webos.html` is ES5 throughout and verified against Chromium 38: `var` only, no arrow functions, no template literals, `XMLHttpRequest` instead of `fetch` (Chromium 42) and instead of `async`/`await` (55), no `??`/`?.`/spread/default parameters, no `Set`/`Map`/`Promise`, no `NodeList.forEach` (51), and `e.keyCode` instead of `e.key` (51 — the second reason Enter never started a search on older sets).

**Viewport.** `width=1280`, not `device-width`. webOS reports a device width that varies by model year and panel mode, which is what made the desktop layout land at a different scale on every set; 1280 is the standard webOS app canvas and the TV upscales it to the panel.

**Focus is the cursor.** A TV has no pointer, so the file carries its own spatial navigation. Everything focusable is marked `data-nav="1"`; `move(dir)` collects the visible candidates, compares centre points, and picks the nearest one in the requested direction with sideways drift penalised so a move stays in its row or column where it can. The desktop UI expresses its affordances through `:hover` and `:focus-visible`, neither of which exists on a remote-driven set, so the TV build draws a loud focus ring of its own. `ensureVisible()` nudges the scroller by hand because `scrollIntoView` is unreliable across these engines.

**The Back key, and why it is history and not a keydown.** Back during playback used to kill the whole app rather than close the player, despite the keydown handler claiming key 461 and calling `preventDefault`. The cause is that the launcher lands on this page with `location.replace()`, so the page is the only entry in the session history — and webOS's Web App Manager handles Back itself, in native code, before and regardless of what the page does with the event: if the webview can go back it goes back, and if it cannot it closes the app. `preventDefault` on a DOM event cannot cancel a decision taken outside the DOM. The fix is not to fight for the key but to give WAM somewhere to go: every layer the app opens (the shelf view, the action sheet, the player) pushes a same-URL history entry, so Back consumes an entry and arrives back as a `popstate`, and `popstate` is the *only* thing that tears a layer down — history depth and visible layer therefore cannot drift apart. At the outermost level the stack is empty, WAM finds nothing, and the app closes, which is what Back should do there. Sheet → player is a swap rather than a nesting, so it reuses its entry instead of pushing a second. For sets that deliver the key but leave history alone, the keydown starts a short grace timer and walks back by hand only if no `popstate` arrived; it compares a counter rather than setting a flag, so a platform-driven pop and a manual one can never both fire.

**The action sheet.** The desktop card's two icon buttons don't port to a remote — they would be D-pad targets nested inside a D-pad target — so a finished film's card stays one target and OK opens a sheet instead: Watch, Send to phone, Remove from shelf, Cancel. One overlay in the document serves the menu, the delete confirm and the QR code, so the shelf's node count doesn't grow with the shelf and the QR image's decode buffer is allocated once and released on close. `showSheet` rebuilds only the button row. The delete confirm is a sheet rather than `window.confirm`, which blocks the webOS JS thread and whose dialog isn't reliably reachable with a D-pad; "Keep it" is focused first.

**Send to phone on the TV** hits the same two endpoints as `qr-phone.html`, inline in the sheet rather than as a page of its own — a TV has no address bar to come back from, and navigating away would tear the app down to rebuild it. It skips the desktop page's `/api/films` call, since the film object is already in hand. It also polls at two rates instead of one: an unscanned code costs an XHR, a JSON parse and a repaint every time it's checked, so a code nobody has scanned yet is polled every 2s and only a transfer actually in flight earns 1s.

**Diagnostics.** The `0` or red button on the remote toggles a `<pre>` log at the bottom of the page — the TV has no devtools worth reaching for, so raw responses go there.

## The webOS launcher app

`webOS-webapp/` is the installable half, and it deliberately contains no application logic: it is a shim whose only job is to work out where the server lives and redirect to it. The television ships this one file; the real app is served from a machine on the LAN whose address comes from DHCP and therefore cannot be baked in at build time.

Resolution order is: the last host that worked (from `localStorage`, wrapped in try/catch since storage is disabled on some webOS profiles), then the build-time `SEED_HOSTS`, then a sweep of `192.168.1.*` and `192.168.0.*`. Each candidate is probed with a `GET` to `/api/config` — cheap, and only this backend answers it — treating network failure, timeout and any non-2xx identically as "not our server". Known candidates get a 2.5s window; the sweep uses 900ms and runs in batches of 24, racing hosts inside a batch but keeping the batches sequential, because older engines choke on 254 simultaneous sockets. If nothing answers, the shim shows a recovery screen with a text field and one-tap retry buttons for the hosts it already knows about, drivable with the remote, rather than a black page.

It redirects to `/webos.html` and not to `/`, since the server's root serves the desktop build that no webOS engine below Chromium 80 can parse. The redirect uses `location.replace()` so the shim is not in the history — see the Back key discussion above, which is downstream of exactly this choice. The shim is itself written in ES5 with `XMLHttpRequest` for the same engine-range reasons as the app.

`appinfo.json` is gitignored and generated per install from `appinfo.json.example`, so the app id and title are the installer's own rather than something shared through the repo. The `.ipk` files under `webOS-webapp/` are prebuilt packages kept for reference; they carry a specific app id and seed host, so a fresh `ares-package` is the right way to install.

## Docker

The image is `python:3.11-slim` plus `ffmpeg` (required by yt-dlp for muxing/remuxing) and Playwright's Chromium plus its OS-level dependencies (`playwright install --with-deps chromium`). `docker-compose.yml` bind-mounts `Movies/`, `Covers/`, `data/`, and both `backend/` and `frontend/` from the host (so code edits are picked up by uvicorn's `--reload` without rebuilding the image), plus `~/Videos` and `/media` (with `rshared` propagation, so drives mounted on the host after the container starts are still visible inside it) to support the export destination browser.

## Known rough edges

- A failed download deletes the DB row but leaves no user-visible error — the film just vanishes from the shelf. There's no retry or failure status.
- `urlgetter.refresh()` exists but nothing calls it; picking up a rotated domain currently requires a container restart.
- Every DB call opens a fresh SQLite connection rather than sharing one — fine at this scale (single user, low concurrency), but worth knowing if this ever needs to handle concurrent downloads at higher volume.
- `phone_sessions` is a plain in-process dict with no expiry and no size bound: codes are lost on restart (which is a reasonable expiry policy on its own) but until then a minted-and-never-scanned session sits there forever. There's no TTL sweep.
- Anyone on the LAN who knows a session UUID can pull the film — the token *is* the authorisation, and `/dwnld/{token}` has no other check. That's the same trust level as the rest of the app (no auth anywhere), just worth being explicit about since this endpoint is the one designed to be reached from a second device.
- The QR code is built from `request.base_url`, i.e. whatever host the shelf page was opened on. Open the shelf at `http://localhost:8000` and the code encodes `localhost`, which is useless to a phone — the shelf has to be opened on the host's LAN address for the handoff to work.
- `/api/delete` is a `GET`. It's destructive and should be a `DELETE` (or at least a `POST`); as it stands any prefetcher or link-scanner that follows it would delete a film.
- The webOS build is a second copy of the app, not a shared core — a change to search, the shelf or the handoff has to be made twice, in `offline.html`/`qr-phone.html` and again in `webos.html`. The engine-range constraints make sharing code between them impractical, but the duplication is real and will drift.
