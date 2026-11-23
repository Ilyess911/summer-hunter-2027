"""Minimal Telegram Bot API client (HTTPS, token from the environment, never logged)."""

from __future__ import annotations

import fcntl
import json
import logging
import shutil
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class TelegramOffline(TelegramError):
    """Telegram unreachable (filtered network): the message waits in the outbox."""


OUTBOX_MAX_AGE_S = 7 * 86400


class Outbox:
    """Messages that could not leave (e.g. a corporate network blocks Telegram), sent later in order.

    Every process (bot, nightly run, web) shares one JSONL file under a file lock. A queued message gets a
    negative placeholder id; when it is finally sent, `on_sent(placeholder, real_id)` lets the caller rename
    what it stored under the placeholder (reply mappings)."""

    def __init__(self, folder: Path) -> None:
        self.dir = Path(folder)
        self.file = self.dir / "outbox.jsonl"

    @contextmanager
    def _locked(self):  # noqa: ANN202
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / ".lock").open("w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)

    def _read(self) -> list[dict[str, Any]]:
        if not self.file.exists():
            return []
        out = []
        for line in self.file.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def _write(self, items: list[dict[str, Any]]) -> None:
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
        tmp.replace(self.file)

    def add(self, item: dict[str, Any]) -> int:
        with self._locked():
            items = self._read()
            ph = -int(time.time() * 1000)
            while any(i.get("ph") == ph for i in items):
                ph -= 1
            if item.get("path"):                       # screenshots are overwritten by the next run: keep a copy
                src = Path(item["path"])
                dst = self.dir / f"{-ph}_{src.name}"
                shutil.copyfile(src, dst)
                item["path"] = str(dst)
            item.update(ph=ph, at=time.time())
            items.append(item)
            self._write(items)
            return ph

    def pending(self) -> int:
        return len(self._read())

    def flush(self, client: TelegramClient, on_sent: Callable[[int, int], None] | None = None) -> int:
        """Send what waits, oldest first; stop at the first network failure. Returns how many left."""
        if not self.file.exists():
            return 0
        with self._locked():
            items = self._read()
            ids: dict[int, int] = {}
            left: list[dict[str, Any]] = []
            for n, it in enumerate(items):
                if time.time() - float(it.get("at") or 0) > OUTBOX_MAX_AGE_S:
                    self._drop_file(it)
                    continue
                try:
                    real = self._send(client, it, ids)
                except TelegramOffline:
                    left = [i for i in items[n:] if time.time() - float(i.get("at") or 0) <= OUTBOX_MAX_AGE_S]
                    break
                except (TelegramError, OSError) as exc:     # refused by Telegram: retrying would not help
                    log.warning("outbox item dropped", extra={"error": str(exc)[:200]})
                    self._drop_file(it)
                    continue
                self._drop_file(it)
                if real is not None:
                    ids[int(it["ph"])] = real
                    if on_sent:
                        try:
                            on_sent(int(it["ph"]), real)
                        except Exception:  # noqa: BLE001
                            log.exception("outbox on_sent failed")
            self._write(left)
            return len(left)

    @staticmethod
    def _drop_file(it: dict[str, Any]) -> None:
        if it.get("path") and Path(it["path"]).name.startswith(str(-int(it["ph"]))):
            Path(it["path"]).unlink(missing_ok=True)

    @staticmethod
    def _send(client: TelegramClient, it: dict[str, Any], ids: dict[int, int]) -> int | None:
        kind = it["kind"]
        if kind == "message":
            return client.send_message(it["chat_id"], it["text"], it.get("buttons"), it.get("no_preview", True),
                                       _queue=False)
        if kind == "photo":
            return client.send_photo(it["chat_id"], it["path"], it.get("caption", ""), _queue=False)
        if kind == "document":
            return client.send_document(it["chat_id"], it["path"], it.get("caption", ""), _queue=False)
        if kind == "edit":
            mid = int(it["message_id"])
            mid = ids.get(mid, mid)
            if mid > 0:
                client.edit_buttons(it["chat_id"], mid, it.get("buttons"), _queue=False)
        return None


class TelegramClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None, timeout: float = 40.0,
                 outbox: Path | None = None) -> None:
        if not token or ":" not in token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is missing or malformed")
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self.outbox = Outbox(outbox) if outbox else None

    def _queue_or_raise(self, exc: TelegramOffline, queue: bool, item: dict[str, Any]) -> int:
        if queue and self.outbox is not None:
            ph = self.outbox.add(item)
            log.info("telegram offline: message queued", extra={"kind": item["kind"]})
            return ph
        raise exc

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        try:
            resp = self._client.post(f"{self._base}/{method}", json=payload or {}, timeout=timeout)
        except httpx.HTTPError as exc:
            # Never include the URL (it contains the token).
            raise TelegramOffline(f"{method}: network error ({type(exc).__name__})") from None
        try:
            data = resp.json()
        except ValueError:
            # Telegram always answers JSON: an HTML page is a network filter's block page.
            raise TelegramOffline(f"{method}: HTTP {resp.status_code}, non-JSON response") from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('error_code')} {data.get('description', '')[:200]}")
        return data["result"]

    def get_me(self) -> dict[str, Any]:
        return self._call("getMe")

    def send_message(self, chat_id: str | int, text: str, buttons: list[list[dict[str, str]]] | None = None,
                     disable_preview: bool = True, _queue: bool = True) -> int:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
                                   "link_preview_options": {"is_disabled": disable_preview}}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        try:
            return int(self._call("sendMessage", payload)["message_id"])
        except TelegramOffline as exc:
            return self._queue_or_raise(exc, _queue, {"kind": "message", "chat_id": chat_id, "text": text,
                                                      "buttons": buttons, "no_preview": disable_preview})

    def edit_buttons(self, chat_id: str | int, message_id: int, buttons: list[list[dict[str, str]]] | None,
                     _queue: bool = True) -> None:
        item = {"kind": "edit", "chat_id": chat_id, "message_id": message_id, "buttons": buttons}
        if message_id < 0:                         # the message itself still waits in the outbox
            self._queue_or_raise(TelegramOffline("editMessageReplyMarkup: message not sent yet"), _queue, item)
            return
        try:
            self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                  "reply_markup": {"inline_keyboard": buttons or []}})
        except TelegramOffline as exc:
            self._queue_or_raise(exc, _queue, item)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})
        except TelegramError as exc:
            log.warning("answerCallbackQuery failed", extra={"error": str(exc)})

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout=timeout + 10)

    def send_document(self, chat_id: str | int, path, caption: str = "", _queue: bool = True) -> int:  # noqa: ANN001
        path = Path(path)
        try:
            with path.open("rb") as handle:
                resp = self._client.post(f"{self._base}/sendDocument", data={"chat_id": str(chat_id), "caption": caption[:1000]},
                                         files={"document": (path.name, handle)}, timeout=60)
        except httpx.HTTPError as exc:
            return self._queue_or_raise(TelegramOffline(f"sendDocument: network error ({type(exc).__name__})"), _queue,
                                        {"kind": "document", "chat_id": chat_id, "path": str(path), "caption": caption})
        try:
            data = resp.json()
        except ValueError:
            return self._queue_or_raise(TelegramOffline(f"sendDocument: HTTP {resp.status_code}, non-JSON response"),
                                        _queue, {"kind": "document", "chat_id": chat_id, "path": str(path),
                                                 "caption": caption})
        if not data.get("ok"):
            raise TelegramError(f"sendDocument: {data.get('error_code')} {data.get('description', '')[:200]}")
        return int(data["result"]["message_id"])

    def send_photo(self, chat_id: str | int, path, caption: str = "", _queue: bool = True) -> int:  # noqa: ANN001
        """A screenshot; Telegram refuses very tall photos, so those go as a file instead."""
        path = Path(path)
        try:
            with path.open("rb") as handle:
                resp = self._client.post(f"{self._base}/sendPhoto", data={"chat_id": str(chat_id), "caption": caption[:1000]},
                                         files={"photo": (path.name, handle, "image/png")}, timeout=60)
            data = resp.json()
        except httpx.HTTPError as exc:
            return self._queue_or_raise(TelegramOffline(f"sendPhoto: network error ({type(exc).__name__})"), _queue,
                                        {"kind": "photo", "chat_id": chat_id, "path": str(path), "caption": caption})
        except ValueError:
            data = {"ok": False}
        if data.get("ok"):
            return int(data["result"]["message_id"])
        return self.send_document(chat_id, path, caption, _queue=_queue)

    def download_file(self, file_id: str, dest, max_bytes: int = 10_000_000):  # noqa: ANN001, ANN201
        """Download a file the user sent to the bot (getFile, then the file endpoint)."""
        info = self._call("getFile", {"file_id": file_id})
        if int(info.get("file_size") or 0) > max_bytes:
            raise TelegramError("getFile: file too large")
        file_path = str(info.get("file_path") or "")
        if not file_path or ".." in file_path:
            raise TelegramError("getFile: no usable file path")
        try:
            resp = self._client.get(self._base.replace("/bot", "/file/bot", 1) + "/" + file_path, timeout=60)
        except httpx.HTTPError as exc:
            raise TelegramError(f"download: network error ({type(exc).__name__})") from None
        if resp.status_code != 200 or len(resp.content) > max_bytes:
            raise TelegramError(f"download: HTTP {resp.status_code}")
        dest = Path(dest)
        dest.write_bytes(resp.content)
        return dest

    def set_menu_button(self, chat_id: str | int, text: str, url: str) -> None:
        self._call("setChatMenuButton", {"chat_id": int(chat_id),
                                         "menu_button": {"type": "web_app", "text": text, "web_app": {"url": url}}})

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})
