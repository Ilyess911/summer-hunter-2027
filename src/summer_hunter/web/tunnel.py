"""Run the Mini App: uvicorn on 127.0.0.1 plus a Cloudflare quick tunnel for the HTTPS URL Telegram requires.

The quick-tunnel URL changes at every restart, so each time it is (re)published to the bot's
menu button and stored in kv_state['webapp_url'] for the opportunity cards.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)
_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def cloudflared_path() -> str | None:
    candidates = ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared")
    return shutil.which("cloudflared") or next((p for p in candidates if os.path.exists(p)), None)


def run_tunnel(port: int, on_url, stop: threading.Event) -> None:  # noqa: ANN001
    exe = cloudflared_path()
    if not exe:
        log.error("cloudflared not installed: brew install cloudflared")
        return
    backoff = 5
    while not stop.is_set():
        proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", f"http://127.0.0.1:{port}"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        published = False
        url = None
        assert proc.stdout is not None
        for line in proc.stdout:
            if stop.is_set():
                break
            m = _URL.search(line)
            if m:
                url = m.group(0)
            # Publish only once the edge has registered a connection, or Telegram opens a dead URL.
            if url and not published and "Registered tunnel connection" in line:
                published = True
                backoff = 5
                try:
                    on_url(url)
                except Exception:  # noqa: BLE001
                    log.exception("publishing the Mini App URL failed")
        proc.terminate()
        if not stop.is_set():
            log.warning("tunnel exited; restarting", extra={"backoff_s": backoff})
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


def serve(app_factory, settings, sessions, port: int = 8787) -> None:  # noqa: ANN001
    import uvicorn

    from summer_hunter.database.repository import Repository
    from summer_hunter.integrations.telegram_client import TelegramClient

    telegram = TelegramClient(settings.telegram_bot_token) if settings.telegram_configured else None

    def publish(url: str) -> None:
        with sessions() as session:
            Repository(session).set_kv("webapp_url", url)
            session.commit()
        if telegram:
            telegram.set_menu_button(settings.telegram_chat_id, "Offres", url)
        log.info("Mini App published", extra={"url": url})
        print(f"Mini App URL: {url}", flush=True)

    stop = threading.Event()
    threading.Thread(target=run_tunnel, args=(port, publish, stop), daemon=True).start()
    try:
        uvicorn.run(app_factory(settings, sessions, telegram), host="127.0.0.1", port=port, log_level="warning")
    finally:
        stop.set()
