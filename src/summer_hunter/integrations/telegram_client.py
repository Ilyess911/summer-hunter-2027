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

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self._call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})
