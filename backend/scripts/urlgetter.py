"""Single source of truth for the live StreamingCommunity domain.

StreamingCommunity rotates its domain often. A Telegram channel announces the
current one; we read the latest message there and extract the live URL via
Telethon. Config comes from env so no secrets live in the repo (run
backend/scripts/tg_session.py once to generate TG_SESSION).

Every script imports URL / COVER_URL from here. Because Python caches imported
modules, get_new_url() runs exactly ONCE per process (at first import) and the
result is reused everywhere — no repeated Telegram fetches. Call refresh() to
force a re-fetch if a domain rotates while the process is running.
"""

import os
import re
import asyncio
import threading

DEFAULT_URL = os.getenv('URL','').strip()
_URL_RE = re.compile(r"(?:https?://)?(streamingcommunity[\w-]*\.[a-z]{2,})", re.I)


async def _fetch_latest_url(limit: int = 40) -> str | None:
    """Newest-first scan of the channel for a streamingcommunity URL."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session = os.environ["TG_SESSION"]
    channel = os.environ["TG_CHANNEL"]

    async with TelegramClient(StringSession(session), api_id, api_hash) as client:
        async for msg in client.iter_messages(channel, limit=limit):
            text = msg.message or ""
            m = _URL_RE.search(text)
            if m:
                url = "https://" + m.group(1).lower().rstrip("/")
                print(f"[*] LAST URL FETCHED {url}")
                return url
    return None


def _run_in_thread(coro):
    """Run an async coroutine on a fresh loop in its own thread.

    asyncio.run() blows up when a loop is already running (e.g. import happens
    during uvicorn startup). A dedicated thread+loop works either way and keeps
    Telethon's client bound to that loop.
    """
    box: dict = {}

    def runner():
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(coro)
        except BaseException as e:  # surface env/telethon errors to caller
            box["error"] = e
        finally:
            loop.close()

    t = threading.Thread(target=runner)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def get_new_url() -> str:
    """Current StreamingCommunity URL from Telegram, falling back to DEFAULT_URL."""
    try:
        url = _run_in_thread(_fetch_latest_url())
    except KeyError as e:
        print(f"[urlgetter] missing telegram env {e}; using fallback url")
        return DEFAULT_URL
    except Exception as e:
        print(f"[urlgetter] telegram url fetch failed: {e}; using fallback url")
        return DEFAULT_URL
    return url or DEFAULT_URL


def _cover_url(base: str) -> str:
    """Covers live on the cdn.<domain> host: https://cdn.<domain>/images/"""
    return base.replace("https://", "https://cdn.", 1).rstrip("/") + "/images/"


# Resolved ONCE at first import, shared across every script.
URL = get_new_url()
COVER_URL = _cover_url(URL)


def refresh() -> str:
    """Re-fetch the domain and update the shared URL / COVER_URL in place."""
    global URL, COVER_URL
    URL = get_new_url()
    COVER_URL = _cover_url(URL)
    return URL

