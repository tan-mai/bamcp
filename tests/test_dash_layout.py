# TM - #DASH-LAYOUT - Bố cục dashboard & full page
"""Tieu chi nghiem thu #DASH-LAYOUT phia server: route full page, dang nhap xong quay lai dung URL, tai
khoan view-only, o chon TR (/api/trs va tr_id cua /api/chart) va khung trang moi. DB tam, khong goi mang.

Bo cuc, resize, phim tat va tu lam moi chay o trinh duyet: kiem tra theo muc 6 cua ticket.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_dashboard as td  # noqa: E402  dat BAMCP_DATA_ROOT ve thu muc tam truoc khi import server
from test_dashboard import BASE, VIEWER_PASS, HttpBase  # noqa: E402
from test_wyckoff_tr import LEGACY_EVENTS, TrBase  # noqa: E402

import server  # noqa: E402

FULL = f"{BASE}/full"
# Dung dang URL vi du cua ticket, dau phay trong layers de nguyen
QUERY = "symbol=BTCUSDT&date=2026-11-13&tr_id=abc&layers=tr,phase,event,zone,levels,trades"


def boot(page: str) -> dict:
    return json.loads(page.split('id="boot">', 1)[1].split("</script>", 1)[0])


class FullPageTest(HttpBase):
    def test_login_returns_to_full_url_with_query(self):
        """Nghiem thu 5: URL full page o trinh duyet chua dang nhap -> form login, xong quay lai dung URL do."""
        async def main(c):
            res = await c.get(f"{FULL}?{QUERY}")
            self.assertEqual(res.status_code, 302)
            location = res.headers["location"]
            self.assertTrue(location.startswith(f"{BASE}/login?next="), location)
            form = await c.get(location)
            self.assertEqual(form.status_code, 200)
            self.assertIn(f'name="next" value="{FULL}?{QUERY.replace("&", "&amp;")}"', form.text)
            res = await self.login(c, next=f"{FULL}?{QUERY}")
            self.assertEqual(res.status_code, 303)
            self.assertEqual(res.headers["location"], f"{FULL}?{QUERY}")
            page = await c.get(f"{FULL}?{QUERY}")
            self.assertEqual(page.status_code, 200)
            self.assertEqual(boot(page.text)["view"], "full")
        self.session(main)

    def test_shares_session_with_main_page(self):
        """Tab moi khong phai dang nhap lai: cookie cua trang chinh mo duoc full page va nguoc lai."""
        async def main(c):
            await self.login(c)
            self.assertEqual((await c.get(BASE)).status_code, 200)
            self.assertEqual((await c.get(FULL)).status_code, 200)
            self.assertEqual((await c.get(f"{BASE}/api/trs")).status_code, 200)
        self.session(main)

    def test_viewer_opens_full_page_without_write_actions(self):
        """Nghiem thu 6: tai khoan view-only mo duoc full page, trang khong co form hay thao tac ghi."""
        async def main(c):
            await self.login(c, "guest", VIEWER_PASS)
            page = await c.get(f"{FULL}?symbol=BTCUSDT")
            self.assertEqual(page.status_code, 200)
            data = boot(page.text)
            self.assertEqual((data["view"], data["role"]), ("full", "viewer"))
            self.assertNotIn("<form", page.text)
            for marker in ('id="chart"', 'id="fPanel"', 'id="fsBtn"', 'id="layerMenu"', 'id="trSel"',
                           'id="resetZoom"', 'id="refresh"'):
                self.assertIn(marker, page.text)
        self.session(main)

    def test_full_page_headers_and_no_inline_script(self):
        async def main(c):
            await self.login(c)
            page = await c.get(FULL)
            self.assertEqual(page.headers["x-frame-options"], "DENY")
            self.assertIn("script-src 'self'", page.headers["content-security-policy"])
            self.assertEqual(page.headers["cache-control"], "no-store")
            for piece in page.text.split("<script")[1:]:
                head = piece.split(">", 1)[0]
                self.assertTrue("src=" in head or 'type="application/json"' in head, head)
        self.session(main)

    def test_main_page_layout(self):
        async def main(c):
            await self.login(c)
            page = (await c.get(BASE)).text
            self.assertEqual(boot(page)["view"], "main")
            # Nghiem thu 4: nut full page la the <a target=_blank> nen chuot giua / Ctrl+click mo tab nen
            link = re.search(r'<a [^>]*id="fullLink"[^>]*>', page, re.S).group(0)
            for attr in (f'href="{FULL}"', 'target="_blank"', 'rel="noopener"'):
                self.assertIn(attr, link)
            for tab in ("setups", "events", "plan", "offplan", "stats"):
                self.assertIn(f'data-tab="{tab}"', page)
            for marker in ('id="sideToggle"', 'id="refresh"', 'id="trSel"', 'id="sideSetups"', 'id="trCard"',
                           'id="menuBtn"'):
                self.assertIn(marker, page)
        self.session(main)

    def test_new_routes_need_session_and_404_when_disabled(self):
        async def main(c):
            self.assertEqual((await c.get(f"{BASE}/api/trs")).status_code, 401)
        self.session(main)
        server._write_json(server.DASH_FILE, {"enabled": False})
        server._DASH_CFG_CACHE.update(key=None, cfg=None)

        async def off(c):
            for path in (FULL, f"{BASE}/api/trs"):
                self.assertEqual((await c.get(path)).status_code, 404, path)
        self.session(off)


class TrPickerTest(HttpBase):
    def setUp(self):
        super().setUp()
        self.klines.upsert(self.symbol, "15m", td.real_m15_rows())
        TrBase.insert_legacy(self, tid="old", events=LEGACY_EVENTS[:2], created=td.vn_ms("2026-11-06 08:00"),
                             superseded="new")
        TrBase.insert_legacy(self, tid="new", events=LEGACY_EVENTS, created=td.vn_ms("2026-11-07 08:00"),
                             high=76976.71)
        server._migrate_tr_events()

    def test_trs_newest_first_with_active(self):
        async def main(c):
            await self.login(c, "guest", VIEWER_PASS)
            out = (await c.get(f"{BASE}/api/trs", params={"symbol": self.symbol})).json()
            self.assertEqual(out["active_id"], "new")
            self.assertEqual([t["id"] for t in out["trs"]], ["new", "old"])
            self.assertEqual(out["trs"][1]["superseded_by"], "new")
            self.assertNotIn("events", out["trs"][0])
        self.session(main)

    def test_chart_tr_id_swaps_only_tr_and_wyckoff_markers(self):
        def wyckoff(data):
            return [m for m in data["markers"] if m["kind"] == "wyckoff"]

        async def main(c):
            await self.login(c)
            day = {"symbol": self.symbol, "date": "2026-11-07", "tf": "15m", "bars": 300}
            plain = (await c.get(f"{BASE}/api/chart", params=day)).json()
            self.assertEqual(plain["tr"]["id"], "new")
            self.assertEqual(len(wyckoff(plain)), 6)
            picked = (await c.get(f"{BASE}/api/chart", params={**day, "tr_id": "old"})).json()
            self.assertEqual(picked["tr"]["id"], "old")
            self.assertEqual(len(wyckoff(picked)), 2)
            self.assertEqual(picked["candles"], plain["candles"])
            self.assertEqual(picked["setups"], plain["setups"])
            # Chon dung TR cua ke hoach cung nhu khong chon
            same = (await c.get(f"{BASE}/api/chart", params={**day, "tr_id": "new"})).json()
            self.assertEqual(same["markers"], plain["markers"])
            # Ban trong cache chart khong bi sua: goi lai khong tr_id van ra TR cua ke hoach
            again = (await c.get(f"{BASE}/api/chart", params=day)).json()
            self.assertEqual((again["tr"]["id"], len(wyckoff(again))), ("new", 6))
            bad = await c.get(f"{BASE}/api/chart", params={**day, "tr_id": "khong-co"})
            self.assertEqual(bad.status_code, 400)
        self.session(main)

    def test_chart_tr_id_of_other_symbol_rejected(self):
        core = {"symbol": self.symbol, "candles": [], "markers": [], "timeframe": "15m", "date": "2026-11-07",
                "tr": None}
        with self.assertRaisesRegex(ValueError, "la cua"):
            server._dash_pick_tr({**core, "symbol": "ETHUSDT"}, "old")
        self.assertEqual(server._dash_pick_tr(core, "old")["tr"]["id"], "old")
        self.assertIs(server._dash_pick_tr(core, ""), core)


if __name__ == "__main__":
    unittest.main()
