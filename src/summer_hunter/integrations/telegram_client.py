"""Minimal Telegram Bot API client (HTTPS, token from the environment, never logged)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None, timeout: float = 40.0) -> None:
        if not token or ":" not in token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is missing or malformed")
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _call(self, method: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        try:
            resp = self._client.post(f"{self._base}/{method}", json=payload or {}, timeout=timeout)
        except httpx.HTTPError as exc:
            # Never include the URL (it contains the token).
            raise TelegramError(f"{method}: network error ({type(exc).__name__})") from None
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {resp.status_code}, non-JSON response") from None
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('error_code')} {data.get('description', '')[:200]}")
        return data["result"]

    def get_me(self) -> dict[str, Any]:
        return self._call("getMe")

    def send_message(self, chat_id: str | int, text: str, buttons: list[list[dict[str, str]]] | None = None,
                     disable_preview: bool = True) -> int:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
                                   "link_preview_options": {"is_disabled": disable_preview}}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return int(self._call("sendMessage", payload)["message_id"])

    def edit_buttons(self, chat_id: str | int, message_id: int, buttons: list[list[dict[str, str]]] | None) -> None:
        self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                              "reply_markup": {"inline_keyboard": buttons or []}})

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

    def send_document(self, chat_id: str | int, path, caption: str = "") -> int:  # noqa: ANN001
        from pathlib import Path

        path = Path(path)
        try:
            with path.open("rb") as handle:
                resp = self._client.post(f"{self._base}/sendDocument", data={"chat_id": str(chat_id), "caption": caption[:1000]},
                                         files={"document": (path.name, handle)}, timeout=60)
        except httpx.HTTPError as exc:
            raise TelegramError(f"sendDocument: network error ({type(exc).__name__})") from None
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"sendDocument: {data.get('error_code')} {data.get('description', '')[:200]}")
        return int(data["result"]["message_id"])

    def download_file(self, file_id: str, dest, max_bytes: int = 10_000_000):  # noqa: ANN001, ANN201
        """Download a file the user sent to the bot (getFile, then the file endpoint)."""
        from pathlib import Path

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
