# -*- coding: utf-8 -*-
"""핫딜 (2026-11-13): 파싱·중복·검색(FTS trigram + 짧은 말은 LIKE)·보관 기간, 화면·API·소유자만·바로가기"""
import json
import time

import pytest

import accounts
import hotdeal
import web_admin


def test_parse_basic_and_hidden_links():
    t = "[쿠팡] 삼다수 2L 12개\n정가 19,900원 → 12,900원 (무료배송)\n#생수"
    p = hotdeal.parse(t, ["https://www.coupang.com/vp/products/12345?itemId=9&vendorItemId=8&utm_source=tg"])
    assert p["title"] == "쿠팡 삼다수 2L 12개" and p["price"] == 12900 and p["shop"] == "쿠팡"
    assert p["url_key"] == "coupang:12345"
    # 텔레그램 링크는 상품 링크가 아님, 본문 안 URL도 찾음
    p = hotdeal.parse("에어팟 프로 2 199000원 https://smartstore.naver.com/abc/products/777?NaPm=x", ["https://t.me/somechan"])
    assert p["shop"] == "네이버" and p["url_key"] == "naver:777" and p["price"] == 199000 and "t.me" not in p["url"]
    # 가격·링크 없어도 제목은
    p = hotdeal.parse("오늘의 특가 모음\n자세한 건 원문")
    assert p["title"] == "오늘의 특가 모음" and p["price"] is None and p["url"] == ""


def test_url_key_strips_tracking():
    a = hotdeal.url_key("https://shop.example.com/item/1?utm_source=a&color=red&fbclid=zz")
    b = hotdeal.url_key("https://www.shop.example.com/item/1/?color=red")
    assert a == b == "shop.example.com/item/1?color=red"


@pytest.fixture
def st(tmp_path):
    return hotdeal.Store(tmp_path / "hd.db")


def test_store_dup_search_purge(st):
    assert st.fts                                                  # SQLite 3.34+ trigram
    st.add_channel("-1001", "dealch", "딜채널")
    ch = st.channels()[0]
    now = int(time.time())
    u = ["https://www.coupang.com/vp/products/555"]
    r1 = st.add_deal(ch, 1, now - 3600, "삼다수 2L 12개 12,900원", u, "https://t.me/dealch/1")
    r2 = st.add_deal(ch, 2, now, "삼다수 다시 올라옴 12,500원", u, "https://t.me/dealch/2")
    assert r1["is_dup"] == 0 and r2["is_dup"] == 1                # 48시간 안 같은 상품
    assert st.add_deal(ch, 1, now, "x") is None                     # 같은 메시지는 한 번만
    st.add_deal(ch, 3, now, "에어팟 프로2 199,000원 https://smartstore.naver.com/a/products/9")
    st.add_deal(ch, 4, now - 100 * 86400, "아주 오래된 딜 1,000원")
    assert st.search()["total"] == 3                                # 중복 숨김(기본)
    assert st.search(hide_dup=False)["total"] == 4
    assert [x["msg_id"] if "msg_id" in x else x["id"] for x in st.search(q="삼다수")["items"]] == ["-1001_1"]
    assert st.search(q="에어", hide_dup=False)["total"] == 1      # 2글자는 LIKE
    assert st.search(q="에어팟 199")["total"] == 1                  # 여러 단어 AND
    assert st.search(shop="네이버")["total"] == 1 and st.search(days=1)["total"] == 2
    assert [x["price"] for x in st.search(sort="price", hide_dup=False)["items"]][:2] == [1000, 12500]
    assert st.purge(90) == 1 and st.search(hide_dup=False)["total"] == 3
    st.delete_channel("-1001", with_deals=True)
    assert st.search(hide_dup=False)["total"] == 0 and st.channels() == []


def test_keywords_and_feed_text():
    row = {"title": "에어팟 프로2", "raw": "에어팟 프로2 특가", "price": 199000, "shop": "네이버", "source": "딜채널",
           "url": "https://x/1", "source_link": "https://t.me/a/1"}
    assert hotdeal.match_keywords(row, ["에어팟", "갤럭시"]) == ["에어팟"]
    t = hotdeal.feed_text(row, ["에어팟"])
    assert t.startswith("🔔 [에어팟] 에어팟 프로2") and "199,000원 · 네이버 · 딜채널" in t and "원문: https://t.me/a/1" in t


@pytest.fixture
def hd(monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "HOTDEAL_DIR", tmp_path / "hotdeal")
    monkeypatch.setitem(web_admin._hd, "store", None)
    monkeypatch.setitem(web_admin._hd, "tg", None)
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "current_user", lambda req: accounts.OWNER)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client()


def test_pages_and_shortcut(hd):
    html = hd.get("/hotdeal").get_data(as_text=True)
    assert 'id="hdList"' in html and 'href="/hotdeal?app=1"' in html and "hotdeal.webmanifest" in html
    app_html = hd.get("/hotdeal?app=1").get_data(as_text=True)
    assert "hotdeal-icon-180.png" in app_html and "<title>핫딜</title>" in app_html and 'id="hdList"' in app_html
    m = json.loads(open("static/hotdeal.webmanifest", encoding="utf-8").read())
    assert m["start_url"] == "/hotdeal?app=1" and m["name"] == "핫딜"


def test_channels_settings_search_api(hd):
    assert hd.post("/api/hotdeal/channels", json={"action": "add", "chat_id": "-1001", "username": "a", "title": "딜"}).get_json()["ok"]
    assert hd.post("/api/hotdeal/channels", json={"action": "update", "chat_id": "-1001", "display": "내딜", "enabled": False}).get_json()["ok"]
    d = hd.get("/api/hotdeal/channels").get_json()
    assert d["channels"][0]["display"] == "내딜" and d["channels"][0]["enabled"] == 0 and d["settings"]["keep_days"] == 90
    assert hd.post("/api/hotdeal/channels", json={"action": "add", "chat_id": "abc"}).status_code == 400
    assert hd.post("/api/hotdeal/channels", json={"action": "update", "chat_id": "-9", "display": "x"}).status_code == 404
    assert hd.post("/api/hotdeal/settings", json={"kw_on": True, "keywords": ["에어팟", " "], "keep_days": 3}).get_json()["ok"]
    s = hd.get("/api/hotdeal/channels").get_json()["settings"]
    assert s["kw_on"] and s["keywords"] == ["에어팟"] and s["keep_days"] == 7 and not s["feed_on"]
    r = hd.get("/api/hotdeal/search?q=&days=0").get_json()
    assert r["ok"] and r["total"] == 0 and r["channels"] == 1
    assert hd.post("/api/hotdeal/channels", json={"action": "delete", "chat_id": "-1001"}).get_json()["ok"]


def test_login_api_validation_and_no_secrets(hd):
    s = hd.get("/api/hotdeal/login").get_json()
    assert s["ok"] and "session" in s and not any("hash" in k.lower() for k in s)       # 값은 안 내보냄
    assert hd.post("/api/hotdeal/login", json={"action": "api", "api_id": "x", "api_hash": "y"}).status_code == 400
    assert hd.post("/api/hotdeal/login", json={"action": "api", "api_id": "123", "api_hash": "a" * 32}).get_json()["ok"]
    assert (web_admin.HOTDEAL_DIR / "api.json").stat().st_mode & 0o777 == 0o600
    assert hd.post("/api/hotdeal/login", json={"action": "send", "phone": "010-1234"}).status_code == 400
    assert hd.post("/api/hotdeal/login", json={"action": "code", "code": ""}).status_code == 400


def test_owner_only(hd, monkeypatch):
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")
    monkeypatch.setattr(web_admin, "_req_user_perms", lambda req: ("someone", {}))
    for url in ("/api/hotdeal/search", "/api/hotdeal/channels", "/api/hotdeal/login"):
        assert hd.get(url).status_code in (302, 403)
    assert hd.post("/api/hotdeal/run").status_code in (302, 403)


def test_collect_saves_and_notifies(hd, monkeypatch):
    st = web_admin._hd_store()
    st.add_channel("-1001", "a", "딜")
    sent = []
    monkeypatch.setattr(web_admin, "hub_notify", lambda key, text, *a, **k: sent.append((key, text)))

    class FakeTG:
        def api(self):
            return (1, "h")

        def collect(self, chans, on_msg, log=None):
            now = int(time.time())
            on_msg(chans[0], 10, now, "에어팟 프로2 199,000원", ["https://smartstore.naver.com/a/products/9"], "https://t.me/a/10")
            on_msg(chans[0], 11, now, "삼다수 12,900원", ["https://www.coupang.com/vp/products/5"], "https://t.me/a/11")
            on_msg(chans[0], 5, now - 5 * 86400, "옛날 딜 에어팟", [], "https://t.me/a/5")      # 백필은 알림 안 함
            return {"-1001": {"new": 3, "last": 11, "error": ""}}
    monkeypatch.setitem(web_admin._hd, "tg", FakeTG())
    (web_admin.HOTDEAL_DIR / "tg.session").write_text("x")
    st.set_meta("settings", {"kw_on": True, "feed_on": True, "keywords": ["에어팟"], "keep_days": 90})
    web_admin._hd_collect()
    assert web_admin._hd_run["new"] == 3 and st.channels()[0]["last_msg_id"] == 11
    assert [k for k, _ in sent] == ["mr.hotdeal_keyword", "mr.hotdeal_feed"]
    assert "에어팟" in sent[0][1] and "삼다수" in sent[1][1]


def test_hub_items_registered():
    import notify_hub
    assert "mr.hotdeal_keyword" in notify_hub.DEFAULT_ITEMS and "mr.hotdeal_feed" in notify_hub.DEFAULT_ITEMS
