# Home-Netflix

A small self-hosted app that lets you search a streaming catalog, pull a film down to local disk, watch it from your own "shelf" once it's downloaded, and export finished files out to an external drive or folder. Runs as a single Docker container with a FastAPI backend and a plain HTML/JS frontend — no build step, no external database. Finished films can also be handed off to a phone over the LAN by scanning a QR code, and there is a second, TV-shaped frontend plus a small launcher app for LG webOS televisions.

For an explanation of how it works internally (backend structure, download pipeline, domain rotation handling, filesystem browsing, etc.), see [INDEPTH.md](INDEPTH.md).

## Requirements

- Docker and Docker Compose
- A Telegram account (used only to read the current catalog domain — see below)

## Setup

1. Clone the repo and `cd` into it.
2. Copy the environment template:
   ```bash
   cp .env.example .env
   ```
3. Get a Telegram `api_id` and `api_hash` from https://my.telegram.org (API development tools) and put them in `.env`.
4. Generate a session string once, locally (not inside Docker, since it needs an interactive login):
   ```bash
   pip install telethon
   python -m backend.scripts.tg_session
   ```
   Enter your `api_id` and `api_hash` when prompted, then paste the printed `TG_SESSION` value into `.env`.
5. Set `TG_CHANNEL` in `.env` to the Telegram channel (e.g. `@somechannel`) that posts the current catalog domain.
6. Start the app:
   ```bash
   docker compose up --build
   ```
7. Open http://localhost:8000.

## Using the app

**Find a film** — the home page (`/`). Type a title and press Find. Results come from the live catalog; press "Download" on a result to start pulling it to disk in the background.

**Series** — a search result that is a series shows a season count and an "Episodes" button instead of "Download". Open it and you get the season list: pick a season, then pull a single episode with the button on its card, or take the whole season at once with "Download season". Episodes land on the shelf as ordinary entries, named for the episode, and play and export exactly like a film. There is no series grouping on the shelf yet — a downloaded season shows up as one entry per episode.

**My shelf** — `/offline.html`. Shows every film that's downloading or finished. In-progress downloads show a live progress bar. Click a finished film's cover to play it in the built-in video player.

**Export** — `/offline.html` → Export. Pick a finished film, then choose a destination folder using the built-in folder browser (limited to your home directory, `~/Videos`, and any mounted external drive), and press Export. This moves the file out of the app and removes it from the shelf.

**Send to phone** — the QR button on a finished film's card opens `/qr-phone.html`. The host mints a one-shot code holding a LAN link to that film; point a phone camera at it and the file downloads straight to the phone. The page shows the transfer live, byte count and all, because the host counts what it pushes out. Transfers are resumable — if the phone loses WiFi partway, re-opening the link picks up where it stopped rather than starting over. Each code is good for one transfer; press "New code" to mint another. The phone has to be on the same network as the host, since the link points at the host's LAN address.

**Remove from shelf** — the trash button on a finished film's card deletes the video file, its cover art and its database row, after a confirm. Only finished films can be removed; the backend refuses anything still downloading.

If the app can't reach the internet, it automatically falls back to an offline-friendly landing page.

## Watching on a webOS TV

The desktop pages are written for evergreen Chrome and will not run on a television, so the TV gets its own build of the same app at `/webos.html` — same backend, same endpoints, written in ES5 and driven entirely by the remote's D-pad instead of a mouse. It carries the search view, series and episodes, the shelf, the video player, remove-from-shelf and send-to-phone, all reachable with arrows, OK and Back. Point any webOS browser at `http://<host>:8000/webos.html` and it works as-is; the launcher below only exists so the television has an icon to press.

Series work the same way as on the desktop: OK on a series opens a full-screen season view with the seasons across the top and a reel of episodes underneath. Left and right walk the reel and it scrolls to follow, up and down move between the seasons and the episodes, OK pulls the episode down, and "Download season" takes the lot. Back closes the reel and returns to the search results.

Playing a film shows a progress bar along the bottom with the elapsed and total time. ◀ and ▶ scrub along it: tap for ten seconds at a time, or hold and the jump grows — ten seconds, then thirty, then a minute, then five — so you can cross a whole film in a couple of seconds and still nudge back four seconds for a line you missed. While you're moving, the bar shows where you'd land and how far the jump is ("Scrubbing +46:39"); the film keeps playing and only actually jumps a moment after you stop, or straight away if you press OK. OK otherwise plays and pauses, and Back closes the player. The bar fades out a few seconds after the last press so it isn't sitting on the picture, and any press brings it back; it stays put whenever the film is paused.

`webOS-webapp/` is that launcher: a tiny web app you install on the TV once. It holds no application logic at all — on launch it hunts for the server on the LAN (last host that worked, then its build-time defaults, then a sweep of `192.168.1.*` and `192.168.0.*`) and redirects to `/webos.html` on whichever machine answers. This is because the host's address comes from DHCP and cannot be baked in at build time. If nothing answers, it shows a recovery screen where the address can be typed in with the remote, and remembers it for next time.

To install it: copy `webOS-webapp/appinfo.json.example` to `webOS-webapp/appinfo.json` and fill in your own `id` and `title` (this file is gitignored, so each install keeps its own). Set `SEED_HOSTS` near the top of the script in `webOS-webapp/index.html` to your server's LAN address — this only saves the app a network sweep, it is not required.

Then install it with **webOS Dev Manager** (the desktop GUI — https://github.com/webosbrew/dev-manager-desktop), which is the easier route than the `ares-*` command line:

1. Install Developer Mode on the TV (LG Content Store → Developer Mode app) and switch it on, or use webosbrew's rooted setup if the TV is already rooted.
2. Open Dev Manager, add the TV by its LAN IP, and pair it with the passphrase the Developer Mode app shows on screen.
3. Go to the **Apps** tab → **Install from local file**, and pick the `.ipk`.
4. The app then sits on the TV's home row like any other; launch it with the remote.

Dev Manager can also package a folder into an `.ipk` for you, so no local SDK install is needed. Two prebuilt `.ipk` files are checked in under `webOS-webapp/` for reference; build your own rather than installing those, since they carry someone else's app id and seed host.

## Data and storage

- `Movies/` — downloaded video files
- `Covers/` — downloaded cover art
- `data/database.db` — SQLite database tracking each film's title, status, and download progress

These directories are mounted as Docker volumes, so downloads and the database persist across container restarts.

## Configuration reference

All configuration lives in `.env` (see `.env.example`):

| Variable | Purpose |
|---|---|
| `TG_API_ID` | Telegram API ID from my.telegram.org |
| `TG_API_HASH` | Telegram API hash from my.telegram.org |
| `TG_SESSION` | Session string produced by `backend/scripts/tg_session.py` |
| `TG_CHANNEL` | Telegram channel that announces the current catalog domain |

`docker-compose.yml` also mounts `~/Videos` and `/media` into the container, so folders there are selectable as export destinations from inside Docker.

## Running without Docker

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

`ffmpeg` must be installed on the host (the Docker image installs it automatically; see `Dockerfile`).
