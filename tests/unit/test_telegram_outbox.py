"""Offline outbox: messages queued on a filtered network, delivered later in order, reply ids re-keyed."""

from __future__ import annotations

import json

import httpx
import pytest

from summer_hunter.integrations.telegram_client import TelegramClient, TelegramError

TOKEN = "123:abc"


class Net:
    def __init__(self) -> None:
        self.up = False
        self.sent: list[tuple[str, dict]] = []
        self.next_id = 100

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("blocked")
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content) if request.headers.get("content-type", "").startswith("application/json") else {}
        self.sent.append((method, body))
        self.next_id += 1
        return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id} if method != "editMessageReplyMarkup" else True})


def client(tmp_path, net):
    return TelegramClient(TOKEN, transport=httpx.MockTransport(net), outbox=tmp_path / "box")


def test_offline_send_is_queued_then_flushed_in_order(tmp_path):
    net = Net()
    tg = client(tmp_path, net)
    a = tg.send_message(1, "first", [[{"text": "x", "callback_data": "y"}]])
    b = tg.send_message(1, "second")
    tg.edit_buttons(1, a, None)
    assert a < 0 and b < 0 and tg.outbox.pending() == 3
    net.up = True
    seen = {}
    assert tg.outbox.flush(tg, lambda ph, real: seen.__setitem__(ph, real)) == 0
    assert [m for m, _ in net.sent] == ["sendMessage", "sendMessage", "editMessageReplyMarkup"]
    assert net.sent[0][1]["text"] == "first" and net.sent[1][1]["text"] == "second"
    assert net.sent[2][1]["message_id"] == seen[a]            # the edit targets the real id
    assert set(seen) == {a, b}


def test_flush_stops_while_still_offline(tmp_path):
    net = Net()
    tg = client(tmp_path, net)
    tg.send_message(1, "hello")
    assert tg.outbox.flush(tg) == 1
    assert tg.outbox.pending() == 1


def test_photo_copy_survives_overwrite(tmp_path):
    net = Net()
    tg = client(tmp_path, net)
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"one")
    tg.send_photo(1, shot, "cap")
    shot.write_bytes(b"two")
    item = tg.outbox._read()[0]
    from pathlib import Path
    assert Path(item["path"]).read_bytes() == b"one"
    net.up = True
    assert tg.outbox.flush(tg) == 0
    assert not Path(item["path"]).exists()


def test_without_outbox_network_errors_still_raise(tmp_path):
    tg = TelegramClient(TOKEN, transport=httpx.MockTransport(Net()))
    with pytest.raises(TelegramError):
        tg.send_message(1, "x")


def test_kv_rekey(tmp_path):
    from summer_hunter.database.migrations import init_db, make_engine
    from summer_hunter.database.repository import Repository

    S = init_db(make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}"))
    with S() as s:
        r = Repository(s)
        r.set_kv("aa_msg:-1700", "12:3")
        r.set_kv("aa_msg:-17001", "other")
        assert r.rename_kv_suffix("-1700", "555") == 1
        s.commit()
        assert r.get_kv("aa_msg:555") == "12:3" and r.get_kv("aa_msg:-1700") is None
        assert r.get_kv("aa_msg:-17001") == "other"
