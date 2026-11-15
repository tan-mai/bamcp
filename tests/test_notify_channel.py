# TM - #NOTIFY-CHANNEL - Tách kênh thông báo theo cặp
"""Nghiem thu ticket #NOTIFY-CHANNEL (muc 12 cua spec) + logic route / dong dau / gio yen lang."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="bamcp-nc-")
os.environ.setdefault("BAMCP_DATA_ROOT", _TMP)
os.environ.setdefault("BAMCP_ACCOUNT_ENABLED", "false")

import admin_notify  # noqa: E402
import entry_store  # noqa: E402
import entry_watcher  # noqa: E402
import notifier  # noqa: E402
import notify_logic as nl  # noqa: E402
import notify_store  # noqa: E402
import server  # noqa: E402
import sqlite_db  # noqa: E402

VN = ZoneInfo("Asia/Ho_Chi_Minh")
T0 = int(datetime(2026, 11, 15, 9, 3, tzinfo=VN).timestamp() * 1000)
TOKEN = "123456:SECRET-TELEGRAM-TOKEN"
HOOK = "https://discord.com/api/webhooks/42/SECRET-DISCORD-HOOK"


def tearDownModule():
    if Path(server.DATA_ROOT).resolve() != Path(_TMP).resolve():
        shutil.rmtree(_TMP, ignore_errors=True)


class Fake:
    """Thay provider that: ghi lai tin, gia lap loi / 429 theo ten kenh."""

    def __init__(self, test, channel):
        self.test, self.channel = test, channel

    async def send(self, msg):
        name = self.channel["name"]
        plan = self.test.behavior.get(name)
        if plan == "down":
            secret = HOOK if self.channel["provider"] == "discord" else TOKEN
            raise RuntimeError(f"HTTP 404 webhook sai {secret}")
        if isinstance(plan, list) and plan:
            raise plan.pop(0)
        self.test.sent.append((name, msg))


class HubBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bamcp-nc-h-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.now = T0
        self.store = notify_store.NotifyStore(self.dir / "db.sqlite", now_ms=lambda: self.now)
        self.cfg: dict = {}
        self.sent: list = []
        self.waits: list = []
        self.behavior: dict = {}

        async def sleep(seconds):
            self.waits.append(round(seconds, 3))
            self.now += int(seconds * 1000)

        self.hub = notifier.Hub(self.store, settings=lambda: self.cfg,
                                secret=lambda ref: HOOK if str(ref).startswith("DC_") else TOKEN, tz=VN,
                                now_ms=lambda: self.now, sleep=sleep, sender=lambda ch, secret: Fake(self, ch))

    def channel(self, name, provider="telegram", **kw):
        fields = {"name": name, "provider": provider, "enabled": True}
        if provider == "telegram":
            fields.update(telegram_bot_token_ref="TG_BOT_MAIN", telegram_chat_id=kw.pop("chat", "-1001"),
                          telegram_thread_id=kw.pop("thread", None))
        else:
            fields.update(discord_webhook_ref=f"DC_{name.upper().replace(' ', '_')}")
        fields.update(kw)
        return self.store.save_channel(fields)

    def send(self, symbol, category, severity=None, title="t", body="b", ref=None, **kw):
        async def main():
            out = await self.hub.send(symbol, category, severity, title, body, ref, **kw)
            if self.hub._tasks:
                await asyncio.gather(*list(self.hub._tasks))
            return out
        return asyncio.run(main())

    def names(self):
        return [name for name, _ in self.sent]

    def statuses(self, status):
        return [r for r in self.store.logs(limit=500) if r["status"] == status]


class HeaderAndRouteTest(HubBase):
    # 12.1 - chua co route: moi tin vao kenh mac dinh, dong dau co ten cap
    def test_default_channel_gets_symbol_header(self):
        old = self.channel("Kenh cu", "discord")
        self.cfg = {"default_channel_id": [old["id"]]}
        self.send("BTCUSDT", "ENTRY_TRIGGERED", label="ENTRY TRIGGERED", icon="🎯")
        self.send("ETHUSDT", "OFF_PLAN")
        self.send("XAUUSDT", "ENTRY_IN_ZONE")
        self.send("*", "SYSTEM_ERROR", label="FETCHER ERROR")
        heads = [m.header for _, m in self.sent]
        self.assertEqual(heads, ["🟠 BTC · 🎯 ENTRY TRIGGERED · 09:03", "🔷 ETH · ⚠️ OFF-PLAN · 09:03",
                                 "🟡 XAU · 📍 IN ZONE · 09:03", "⚙️ SYSTEM · ❌ FETCHER ERROR · 09:03"])
        self.assertEqual(self.names(), ["Kenh cu"] * 4)
        self.assertEqual(self.sent[0][1].color, 0xF7931A)
        self.assertEqual(self.sent[0][1].sender_name, "BAMCP · BTC")

    def test_route_order(self):
        routes = [{"symbol": "BTCUSDT", "category": "*", "enabled": True, "channel_id": "a"},
                  {"symbol": "*", "category": "OFF_PLAN", "enabled": True, "channel_id": "b"},
                  {"symbol": "BTCUSDT", "category": "OFF_PLAN", "enabled": False, "channel_id": "c"},
                  {"symbol": "*", "category": "*", "enabled": True, "channel_id": "s"}]
        self.assertEqual(nl.select_routes(routes, "BTCUSDT", "OFF_PLAN")[0], 2)      # cap 1 tat -> cap 2
        self.assertEqual(nl.select_routes(routes, "ETHUSDT", "OFF_PLAN")[0], 3)
        self.assertEqual(nl.select_routes(routes, "ETHUSDT", "ENTRY_IN_ZONE"), (0, []))  # * + * khong bat cap
        self.assertEqual(nl.select_routes(routes, "*", "SYSTEM_ERROR")[1][0]["channel_id"], "s")

    # 12.2 - moi cap mot topic, loi fetcher vao System
    def test_per_symbol_topics_and_system(self):
        chans = {s: self.channel(f"TG {s}", thread=i + 2) for i, s in enumerate(("BTC", "ETH", "XAU", "System"))}
        for sym in ("BTC", "ETH", "XAU"):
            self.store.set_cell(f"{sym}USDT", "*", [chans[sym]["id"]])
        self.store.set_cell("*", "*", [chans["System"]["id"]])
        self.send("BTCUSDT", "ENTRY_TRIGGERED")
        self.send("ETHUSDT", "ENTRY_IN_ZONE")
        self.send("XAUUSDT", "OFF_PLAN")
        alert = notifier.system_alert("FETCHER_STALE", {"symbols": {"BTCUSDT": {"last_open_time": None}}}, VN)
        self.send(alert.symbol, alert.category, alert.severity, alert.title, alert.text, label=alert.label)
        self.assertEqual(self.names(), ["TG BTC", "TG ETH", "TG XAU", "TG System"])
        self.assertTrue(self.sent[3][1].header.startswith("⚙️ SYSTEM · ❌ FETCHER ERROR"))

    # 12.3 - route BTC ra ca Telegram + Discord: dung 2 tin, moi kenh 1
    def test_parallel_two_channels(self):
        tg, dc = self.channel("TG BTC"), self.channel("DC BTC", "discord")
        self.store.set_cell("BTCUSDT", "*", [tg["id"], dc["id"]])
        out = self.send("BTCUSDT", "ENTRY_TRIGGERED")
        self.assertEqual(sorted(self.names()), ["DC BTC", "TG BTC"])
        self.assertEqual(out["status"], "SENT")
        self.assertEqual(len(self.statuses("SENT")), 2)

    # 12.9 - min_severity: tin info XAU bi FILTERED, khong roi xuong kenh mac dinh
    def test_filtered_does_not_fall_through(self):
        default, xau = self.channel("Default"), self.channel("TG XAU")
        self.cfg = {"default_channel_id": [default["id"]]}
        self.store.set_cell("XAUUSDT", "*", [xau["id"]], min_severity="high")
        out = self.send("XAUUSDT", "ENTRY_IN_ZONE")
        self.assertEqual((out["status"], self.sent), ("FILTERED", []))
        self.assertEqual(self.statuses("FILTERED")[0]["channel_id"], xau["id"])
        self.send("XAUUSDT", "ENTRY_TRIGGERED")
        self.assertEqual(self.names(), ["TG XAU"])

    def test_no_channel_at_all_is_failed_not_raised(self):
        out = self.send("BTCUSDT", "ENTRY_TRIGGERED")
        self.assertEqual(out["status"], "FAILED")
        self.assertIn("kenh mac dinh", out["error"])


class ReduceTest(HubBase):
    def setUp(self):
        super().setUp()
        self.default = self.channel("Default")
        self.cfg = {"default_channel_id": [self.default["id"]]}

    # 12.4 - ra vao vung 4 lan trong 5 phut -> 1 tin, 3 dong DEDUPED
    def test_dedupe_in_zone(self):
        for i in range(4):
            self.now = T0 + i * 60_000
            self.send("BTCUSDT", "ENTRY_IN_ZONE", ref="setup-1:IN_ZONE")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.statuses("DEDUPED")), 3)
        self.now = T0 + 6 * 60_000                               # het cua so -> gui lai
        self.send("BTCUSDT", "ENTRY_IN_ZONE", ref="setup-1:IN_ZONE")
        self.assertEqual(len(self.sent), 2)

    # 12.5 - IN_ZONE roi TRIGGERED cung setup trong 5 phut: tin trigger van gui; cung key severity tang cung gui
    def test_severity_increase_passes(self):
        setup = {"id": "s1", "code": "BTC-1", "symbol": "BTCUSDT", "direction": "long", "state": {},
                 "entry": 1, "stop": 0.5, "target": 2, "zone_low": 1, "zone_high": 1.1, "trigger_type": "spring",
                 "label": "BTC 15/11 · LONG · Spring"}
        for event in ("IN_ZONE", "TRIGGERED"):
            alert = notifier.setup_alert(setup, {"event": event, "details": {}}, VN)
            self.send(alert.symbol, alert.category, alert.severity, alert.title, alert.text, alert.dedupe_ref)
        self.assertEqual(len(self.sent), 2)
        self.send("BTCUSDT", "OFF_PLAN", "high", ref="p1")
        self.send("BTCUSDT", "OFF_PLAN", "high", ref="p1")
        self.send("BTCUSDT", "OFF_PLAN", "critical", ref="p1")
        self.assertEqual(len(self.sent), 4)

    # 12.6 - digest: 3 tin low ETH trong 15 phut -> 1 tin tom tat; tin high gui ngay
    def test_digest_low_and_high_immediate(self):
        eth = self.channel("TG ETH")
        self.store.set_cell("ETHUSDT", "*", [eth["id"]], digest=True)
        for i, title in enumerate(("BTC 15/11 · SHORT · UT sau AR#1", "ZONE EXIT x", "Spring y")):
            self.now = T0 + i * 4 * 60_000
            out = self.send("ETHUSDT", "ENTRY_MISSED", title=title, label="MISSED")
            self.assertEqual(out["status"], "DIGESTED")
        self.send("ETHUSDT", "ENTRY_TRIGGERED")
        self.assertEqual(self.names(), ["TG ETH"])
        self.assertEqual(asyncio.run(self.hub.flush_digests()), 0)        # chua toi 09:15
        self.now = int(datetime(2026, 11, 15, 9, 15, 5, tzinfo=VN).timestamp() * 1000)
        self.assertEqual(asyncio.run(self.hub.flush_digests()), 1)
        digest = self.sent[-1][1]
        self.assertEqual(digest.header, "🔷 ETH · 🗂 TÓM TẮT 09:00–09:15")
        self.assertEqual(digest.body.splitlines()[0], "• MISSED: BTC 15/11 · SHORT · UT sau AR#1 (09:03)")
        self.assertEqual(len(digest.body.splitlines()), 3)

    def test_quiet_hours_only_critical(self):
        self.cfg["quiet_hours"] = "23:00-05:00"
        self.now = int(datetime(2026, 11, 15, 23, 30, tzinfo=VN).timestamp() * 1000)
        self.assertEqual(self.send("BTCUSDT", "ENTRY_TRIGGERED")["status"], "DIGESTED")
        self.assertEqual(self.send("BTCUSDT", "OFF_PLAN", "critical")["status"], "SENT")
        self.now += 60 * 60_000
        self.assertEqual(asyncio.run(self.hub.flush_digests()), 0)        # van trong gio yen lang
        self.now = int(datetime(2026, 11, 16, 5, 0, 20, tzinfo=VN).timestamp() * 1000)
        self.assertEqual(asyncio.run(self.hub.flush_digests()), 1)
        self.assertIn("TÓM TẮT", self.sent[-1][1].header)

    def test_quiet_end(self):
        at = int(datetime(2026, 11, 15, 4, 0, tzinfo=VN).timestamp() * 1000)
        end = nl.quiet_end("23:00-05:00", at, VN)
        self.assertEqual(datetime.fromtimestamp(end / 1000, VN).strftime("%d %H:%M"), "15 05:00")
        self.assertIsNone(nl.quiet_end("23:00-05:00", T0, VN))
        self.assertIsNotNone(nl.quiet_end("09:00-10:00", T0, VN))
        with self.assertRaises(ValueError):
            nl.parse_quiet("25:00-01:00")


class FailureTest(HubBase):
    def setUp(self):
        super().setUp()
        self.default = self.channel("Default")
        self.system = self.channel("TG System", thread=9)
        self.eth = self.channel("DC ETH", "discord")
        self.cfg = {"default_channel_id": [self.default["id"]]}
        self.store.set_cell("ETHUSDT", "*", [self.eth["id"]])
        self.store.set_cell("*", "*", [self.system["id"]])

    # 12.7 - webhook ETH sai: retry 3 lan, roi FALLBACK vao kenh mac dinh; 5 lan loi -> 1 SYSTEM_ERROR
    def test_retry_fallback_and_system_error(self):
        self.behavior["DC ETH"] = "down"
        out = self.send("ETHUSDT", "OFF_PLAN", body="than tin goc")
        self.assertEqual(out["status"], "FALLBACK")
        self.assertEqual([w for w in self.waits if w >= 2], [2.0, 5.0, 15.0])
        name, msg = self.sent[0]
        self.assertEqual(name, "Default")
        self.assertEqual(msg.prefix, "⚠️ FALLBACK (kênh DC ETH lỗi)")
        self.assertTrue(msg.plain().startswith("⚠️ FALLBACK (kênh DC ETH lỗi)\n🔷 ETH · ⚠️ OFF-PLAN"))
        self.assertEqual(len(self.statuses("FALLBACK")), 1)
        self.assertNotIn("SECRET-DISCORD-HOOK", json.dumps(self.store.logs(limit=50)))
        self.assertIn("****HOOK", self.store.channel(self.eth["id"])["last_error"])
        self.assertEqual([n for n in self.names() if n == "TG System"], [])
        self.send("ETHUSDT", "OFF_PLAN")                       # loi thu 5 -> SYSTEM_ERROR o System
        system = [m for n, m in self.sent if n == "TG System"]
        self.assertEqual(len(system), 1)
        self.assertIn("NOTIFY ERROR", system[0].header)
        self.send("ETHUSDT", "OFF_PLAN")                       # toi da 1 tin / kenh loi / gio
        self.assertEqual(len([n for n in self.names() if n == "TG System"]), 1)

    def test_default_channel_failing_is_failed(self):
        self.behavior["Default"] = "down"
        out = self.send("BTCUSDT", "ENTRY_TRIGGERED")
        self.assertEqual(out["status"], "FAILED")
        self.assertNotIn("SECRET-TELEGRAM-TOKEN", out["error"])
        self.assertIn("****OKEN", out["error"])

    # 12.8 - Telegram 429: doi dung retry_after, khong mat tin high
    def test_telegram_429_waits_retry_after(self):
        btc = self.channel("TG BTC", thread=3)
        self.store.set_cell("BTCUSDT", "*", [btc["id"]])
        self.behavior["TG BTC"] = [notifier.RetryAfter(7, "telegram 429, retry_after 7s")]
        out = self.send("BTCUSDT", "ENTRY_TRIGGERED", "high")
        self.assertEqual(out["status"], "SENT")
        self.assertIn(7.0, self.waits)
        self.assertEqual(self.names(), ["TG BTC"])

    def test_rate_limits(self):
        btc = self.channel("TG BTC", thread=3)
        self.store.set_cell("BTCUSDT", "*", [btc["id"]])
        start = self.now
        for _ in range(3):
            self.send("BTCUSDT", "ENTRY_TRIGGERED")
        self.assertGreaterEqual(self.now - start, 2000)          # 1 tin/giay/kenh
        # 20 tin/phut/nhom: cac topic cung chat_id dung chung han muc
        topics = [self.channel(f"T{i}", thread=100 + i) for i in range(4)]
        for i, t in enumerate(topics):
            self.store.set_cell(f"S{i}USDT", "*", [t["id"]])
        self.hub._group_sent.clear()
        start = self.now
        for n in range(21):
            self.send(f"S{n % 4}USDT", "ENTRY_TRIGGERED")
        self.assertGreaterEqual(self.now - start, 60_000 - 1000)

    def test_send_never_raises(self):
        with mock.patch.object(self.store, "routes", side_effect=RuntimeError("db hong")):
            out = self.send("BTCUSDT", "ENTRY_TRIGGERED")
        self.assertEqual(out["status"], "FAILED")
        self.assertIn("db hong", out["error"])


class ProviderTest(unittest.TestCase):
    def test_telegram_topic_payload(self):
        tg = notifier.TelegramBotNotifier(TOKEN, "-1001", thread_id=12)
        body = tg.payload(notifier.Outgoing("🟠 BTC · 🎯 ENTRY TRIGGERED · 09:15", "a < b", prefix="⚠️ FALLBACK"))
        self.assertEqual(body["message_thread_id"], 12)
        self.assertEqual(body["text"], "⚠️ FALLBACK\n<b>🟠 BTC · 🎯 ENTRY TRIGGERED · 09:15</b>\na &lt; b")
        self.assertNotIn("message_thread_id", notifier.TelegramBotNotifier(TOKEN, "-1001").payload(
            notifier.Outgoing("h")))

    def test_discord_embed_payload(self):
        dc = notifier.DiscordWebhookNotifier(HOOK, avatar_url="https://x/a.png")
        body = dc.payload(notifier.Outgoing("🟠 BTC · 🎯 ENTRY TRIGGERED · 09:15", "than", color=0xF7931A,
                                            fields=[{"name": "Entry", "value": 1}], sender_name="BAMCP · BTC"))
        self.assertEqual(body["content"], "🟠 BTC · 🎯 ENTRY TRIGGERED · 09:15")
        self.assertEqual((body["username"], body["avatar_url"]), ("BAMCP · BTC", "https://x/a.png"))
        self.assertEqual(body["embeds"][0]["color"], 0xF7931A)
        self.assertEqual(body["embeds"][0]["fields"][0], {"name": "Entry", "value": "1", "inline": True})

    def test_clean_channel_and_settings(self):
        with self.assertRaises(ValueError):
            nl.clean_channel({"name": "x", "provider": "telegram", "telegram_bot_token_ref": "BAMCP_PASSWORD",
                              "telegram_chat_id": "-1"})
        ok = nl.clean_channel({"name": "x", "provider": "discord", "discord_webhook_ref": "dc_webhook_btc"})
        self.assertEqual(ok["discord_webhook_ref"], "DC_WEBHOOK_BTC")
        with self.assertRaises(ValueError):
            nl.clean_settings({"dedupe_window_seconds": 300, "digest_minutes": 15, "log_retention_days": 30,
                               "default_channel_id": ["nope"]}, set())
        self.assertEqual(nl.settings({"default_channel_id": "a, b"})["default_channel_id"], ["a", "b"])

    def test_alert_routing_fields(self):
        alert = notifier.system_alert("UNPLANNED_POSITION", {"symbol": "ETHUSDT", "position_key": "k",
                                                             "position": {"side": "long"}}, VN)
        self.assertEqual((alert.symbol, alert.category, alert.dedupe_ref), ("ETHUSDT", "OFF_PLAN", "k"))
        self.assertEqual(nl.off_plan_severity("COUNTER_PLAN", []), "critical")
        self.assertEqual(nl.off_plan_severity("CHASE", ["REVENGE"]), "critical")
        self.assertEqual(nl.off_plan_severity("CHASE", ["NO_STOP"]), "high")


class ServerTest(unittest.TestCase):
    """Migration, trang admin, tool MCP, watcher khi moi kenh loi."""

    def setUp(self):
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            for table in ("notify_routes", "notify_channels", "notify_log"):
                conn.execute(f"DELETE FROM {table}")
        server._alerts_save("notify", {"display_timezone": "Asia/Ho_Chi_Minh"})
        server._alerts_save("notify_secrets", {})
        server._alerts_save("off_plan", {})
        hub = server.NOTIFY_HUB
        for state in (hub._dedupe, hub._digest, hub._failures, hub._alerted, hub._last_sent, hub._group_sent,
                      hub._blocked):
            state.clear()
        self.sent: list = []
        self.down: set = set()
        test = self

        class Sender:
            def __init__(self, channel, secret):
                self.channel = channel
                self.value = secret(channel.get("telegram_bot_token_ref") or channel.get("discord_webhook_ref"))

            async def send(self, msg):
                if self.channel["name"] in test.down:
                    raise RuntimeError(f"HTTP 401 {self.value}")
                test.sent.append((self.channel["name"], msg.plain(), self.value))

        async def no_wait(seconds):
            return None

        for name, value in (("sender", Sender), ("sleep", no_wait)):
            patcher = mock.patch.object(hub, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        server._alerts_save("notify", {"display_timezone": "Asia/Ho_Chi_Minh"})

    def act(self, **payload):
        return asyncio.run(server._notify_action(payload))

    # 12.1 + muc 8 - migration: kenh cu thanh kenh mac dinh, off_plan.channel rieng -> route * + OFF_PLAN
    def test_migration_keeps_old_channel(self):
        server._alerts_save("notify", {"channel": "discord", "discord": {"webhook_url": HOOK},
                                       "telegram": {"bot_token": TOKEN, "chat_id": "-1009"}})
        server._alerts_save("off_plan", {"channel": "telegram"})
        out = server._notify_migrate()
        self.assertEqual(out["channels"], ["Discord (kênh cũ)", "Telegram (kênh cũ)"])
        chans = {c["name"]: c for c in server.NOTIFY_STORE.channels()}
        self.assertEqual(server._alerts_cfg()["notify"]["default_channel_id"], [chans["Discord (kênh cũ)"]["id"]])
        self.assertEqual(chans["Discord (kênh cũ)"]["discord_webhook_ref"], "DC_WEBHOOK_MAIN")
        self.assertEqual({(r["symbol"], r["category"]) for r in server.NOTIFY_STORE.routes()},
                         {("*", "OFF_PLAN"), ("*", "TRADE_RESULT")})
        with sqlite_db.connect(server.SQLITE_PATH) as conn:
            dump = json.dumps([dict(r) for r in conn.execute("SELECT * FROM notify_channels")])
        self.assertNotIn("SECRET", dump)                       # DB chi giu ten bien
        self.assertFalse(server._notify_migrate()["migrated"])  # chi chay mot lan
        alert = notifier.Alert("than tin", symbol="BTCUSDT", category="ENTRY_TRIGGERED", label="ENTRY TRIGGERED",
                               icon="🎯")
        self.assertIsNone(asyncio.run(notifier.dispatch(alert)))
        name, text, value = self.sent[0]
        self.assertEqual((name, value), ("Discord (kênh cũ)", HOOK))
        self.assertRegex(text, r"^🟠 BTC · 🎯 ENTRY TRIGGERED · \d\d:\d\d\nthan tin$")

    def test_migration_without_old_config(self):
        out = server._notify_migrate()
        self.assertEqual((out["channels"], out["default_channel_id"]), ([], []))
        self.assertTrue(server._alerts_cfg()["notify"]["channels_migrated"])

    # 12.10 - Gui thu va test_notification bao dung kenh; token khong lo o admin, log, MCP
    def test_test_buttons_and_no_secret_leak(self):
        code, out = self.act(action="channel_save", name="TG BTC topic", provider="telegram",
                             telegram_bot_token_ref="TG_BOT_MAIN", telegram_chat_id="-1001", telegram_thread_id="4",
                             secret_values={"telegram_bot_token_ref": TOKEN})
        self.assertEqual(code, 200, out)
        btc = out["channel"]
        self.assertEqual((btc["telegram_bot_token_mask"], btc["telegram_bot_token_source"]), ("****OKEN", "admin"))
        code, out = self.act(action="channel_save", name="System", provider="discord",
                             discord_webhook_ref="DC_WEBHOOK_SYS", secret_values={"discord_webhook_ref": HOOK})
        system = out["channel"]
        self.act(action="route_save", symbol="BTCUSDT", category="*", channel_ids=[btc["id"]])
        self.act(action="route_save", symbol="*", category="*", channel_ids=[system["id"]])
        self.act(action="settings_save", default_channel_id=[system["id"]], dedupe_window_seconds=300,
                 digest_minutes=15, log_retention_days=30, display_timezone="Asia/Ho_Chi_Minh")
        code, out = self.act(action="channel_test", id=btc["id"])
        self.assertEqual((out["ok"], out["channel"]["name"]), (True, "TG BTC topic"))
        self.assertTrue(self.sent[-1][1].startswith("🧪 TEST · TG BTC topic · "))
        self.assertEqual(self.sent[-1][2], TOKEN)
        out = asyncio.run(server.test_notification("BTCUSDT"))
        self.assertEqual((out["status"], out["channels_used"], out["route"]), ("SENT", ["TG BTC topic"], "symbol+*"))
        out = asyncio.run(server.test_notification(""))
        self.assertEqual(out["channels_used"], ["System"])
        self.down.add("TG BTC topic")
        code, failed = self.act(action="channel_test", id=btc["id"])
        self.assertFalse(failed["ok"])
        page = admin_notify.render(server._notify_view("", ""), action_path="/a", admin_path="/admin",
                                   setup_required=False)
        everything = json.dumps([server.get_notify_config(), server.get_notify_log(limit=500), failed, out],
                                ensure_ascii=False, default=str) + page
        for secret in (TOKEN, HOOK, "SECRET-TELEGRAM-TOKEN", "SECRET-DISCORD-HOOK"):
            self.assertNotIn(secret, everything)
        self.assertIn("HTTP 401 ****OKEN", failed["error"])
        self.assertNotIn("__", page.replace("__init__", ""))
        status = asyncio.run(server.get_today_status(symbol="BTCUSDT"))["notifications"]
        self.assertGreaterEqual(status["by_symbol"]["BTCUSDT"]["sent"], 1)
        self.assertGreaterEqual(status["totals"]["failed"], 1)

    def test_route_validation_and_delete(self):
        code, out = self.act(action="channel_save", name="x", provider="discord", discord_webhook_ref="DC_X",
                             secret_values={"discord_webhook_ref": "https://evil.example/x"})
        self.assertEqual(code, 400)
        code, out = self.act(action="channel_save", name="x", provider="discord", discord_webhook_ref="DC_X")
        cid = out["channel"]["id"]
        self.assertEqual(out["channel"]["discord_webhook_source"], "missing")
        self.assertEqual(self.act(action="route_save", symbol="BTCUSDT", category="NOPE", channel_ids=[cid])[0], 400)
        self.assertEqual(self.act(action="route_save", symbol="BTCUSDT", category="OFF_PLAN", channel_ids=["x"])[0], 400)
        self.act(action="route_save", symbol="BTCUSDT", category="OFF_PLAN", channel_ids=[cid], min_severity="high")
        self.act(action="settings_save", default_channel_id=[cid], dedupe_window_seconds=300, digest_minutes=15,
                 log_retention_days=30, display_timezone="Asia/Ho_Chi_Minh")
        code, out = self.act(action="channel_delete", id=cid)
        self.assertEqual(out["routes_removed"], 1)
        self.assertEqual(server._alerts_cfg()["notify"]["default_channel_id"], [])

    # 12.11 - watcher M5 van chay khi moi kenh thong bao loi
    def test_watcher_survives_when_all_channels_fail(self):
        code, out = self.act(action="channel_save", name="Dead", provider="telegram",
                             telegram_bot_token_ref="TG_BOT_DEAD", telegram_chat_id="-1")
        self.down.add("Dead")
        self.act(action="settings_save", default_channel_id=[out["channel"]["id"]], dedupe_window_seconds=300,
                 digest_minutes=15, log_retention_days=30, display_timezone="Asia/Ho_Chi_Minh")
        store = entry_store.SetupStore(server.SQLITE_PATH)
        eid = store.system_event("FETCHER_STALE", {"symbols": {"BTCUSDT": {"last_open_time": None}}})

        async def boom(alert):
            raise RuntimeError("provider no tung")

        for deliver in (notifier.dispatch, boom):
            watcher = entry_watcher.EntryWatcher(store, klines=lambda: None, cfg=lambda: {"notify": {}},
                                                 min_rr=lambda s: 1.5, check=lambda *a: {}, positions=None, tz=VN,
                                                 span_ms=server._tf_ms, deliver=deliver)
            error = asyncio.run(watcher.send_event(eid))
            self.assertTrue(error)
            self.assertEqual(store.event(eid)["notified"], False)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
