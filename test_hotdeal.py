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
    assert p["title"] == "삼다수 2L 12개" and p["price"] == 12900 and p["shop"] == "쿠팡"
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


def test_parse_channel_formats():
    # 11-13 실제 채널: 10초 핫딜(머리말 '🛍 쇼핑 특가' + 🏬 쇼핑몰 + 💰 가격, 중계 링크), 뽐뿌([뽐뿌] [지마켓] … (가격/무료), 그림 링크)
    a = ("🛍 쇼핑 특가\n\n레노버 Legion Y700 8.4인치 (822,940원) (무료)\n\n💰 822,940원\n\n🏬 지마켓\n\n가격과 옵션을 확인해 보세요.\n\n"
         "🔗 할인가 확인\nhttps://shop.hongzi.us/d/92QoVxYIGY?p=telegram\n\n📢 핫딜 알림 채널 @tenseco")
    p = hotdeal.parse(a)
    assert p["title"] == "레노버 Legion Y700 8.4인치" and p["price"] == 822940 and p["shop"] == "G마켓"
    b = " [뽐뿌] [토스] 강원도 영월 절임배추 10kg (30,305원/무료) \nhttps://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=1"
    p = hotdeal.parse(b, ["https://cdn4.ppomppu.co.kr/zboard/data3/x.jpg"])
    assert p["title"] == "강원도 영월 절임배추 10kg" and p["shop"] == "토스쇼핑" and p["price"] == 30305 and "view.php" in p["url"]


def test_parse_price_slash_shipping():
    # 11-14 뽐뿌 '(가격/배송비)': 배송비가 더 싸도 슬래시 앞이 가격
    p = hotdeal.parse(" [뽐뿌] [겜우리] PS5 마블 울버린 (34,700원/2,500원) \nhttps://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=748766")
    assert p["price"] == 34700 and p["title"] == "PS5 마블 울버린" and p["shop"] == "겜우리"
    assert hotdeal.parse("[뽐뿌] [쿠팡] 사탕 (900원/3,500원)")["price"] == 900
    assert hotdeal.parse("[뽐뿌] [네이버] 김 (3,990원/네멤무료)")["price"] == 3990


def test_parse_jirum_format():
    # 11-13 뽐질: 첫 줄 '👍대박 16,660원 (37.1% 할인)', 둘째 줄 상품명, '- 할인: 9,826원'은 판매가 아님
    t = ("👍대박 16,660원 (37.1% 할인)\n양푼이 우거지 감자탕, 5kg, 1팩\n\n📌 가격 현황\n- 정상가: 26,486원\n- 할인: 9,826원\n\n"
         "📦 특가 바로가기\nhttps://toss.shopping/_m/pdWrSfVu\n\n✱ 이 포스팅은 토스쇼핑 쉐어링크 활동의 일환으로")
    p = hotdeal.parse(t)
    assert p["title"] == "양푼이 우거지 감자탕, 5kg, 1팩" and p["price"] == 16660 and p["shop"] == "토스쇼핑"
    p = hotdeal.parse("🔥🔥🔥무지성 15,470원 (45.5% 할인)🔥🔥🔥\n풀무원 아임리얼100 푸룬, 120ml, 24개\n\n🔥선구매후고민\n📌 가격 현황\n- 정상가: 28,385원")
    assert p["title"] == "풀무원 아임리얼100 푸룬, 120ml, 24개" and p["price"] == 15470
    p = hotdeal.parse("☆특가 3,680원\n가격더다운* 240g\n\n📦 특가 바로가기\nhttps://link.coupang.com/a/iAAGqJXJpQ")
    assert p["title"] == "가격더다운 240g" and p["price"] == 3680 and p["shop"] == "쿠팡"
    p = hotdeal.parse("에어팟 프로2\n정가 359,000원\n할인: 160,000원\n판매가 199,000원")
    assert p["price"] == 199000


def test_reparse_all(st):
    st.add_channel("-1", "a", "a")
    ch = st.channels()[0]
    st.add_deal(ch, 1, int(time.time()), "🛍 쇼핑 특가\n\n폰타나 파스타소스 1세트\n\n💰 13,900원\n\n🏬 토스쇼핑")
    with st._db() as c:
        c.execute("update hotdeals set title='🛍 쇼핑 특가', shop='x'")
    assert st.reparse_all() == 1
    r = st.search(q="파스타")["items"][0]
    assert r["title"] == "폰타나 파스타소스 1세트" and r["shop"] == "토스쇼핑"


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
    sent = []
    web_admin._hd_tg().send_code = lambda phone: sent.append(phone)
    assert hd.post("/api/hotdeal/login", json={"action": "send", "phone": "010-1234-5678"}).get_json()["ok"]
    assert sent == ["+821012345678"]                                                  # 010 → +82(11-13)
    def boom(phone):
        raise TimeoutError()
    web_admin._hd_tg().send_code = boom
    r = hd.post("/api/hotdeal/login", json={"action": "send", "phone": "+821012345678"})
    assert r.status_code == 400 and "늦어요" in r.get_json()["error"]
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
    monkeypatch.setattr(web_admin, "hub_notify", lambda key, text, *a, **k: sent.append((key, text)) or {"ok": True})

    class FakeTG:
        def api(self):
            return (1, "h")

        def collect(self, chans, on_channel, log=None):
            now = int(time.time())
            on_channel(chans[0], [(5, now - 5 * 86400, "옛날 딜 에어팟", [], "https://t.me/a/5"),      # 백필은 알림 안 함
                                  (10, now, "에어팟 프로2 199,000원", ["https://smartstore.naver.com/a/products/9"], "https://t.me/a/10"),
                                  (11, now, "삼다수 12,900원", ["https://www.coupang.com/vp/products/5"], "https://t.me/a/11")], 11, "")
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


def test_notify_retry_after_failure(hd, monkeypatch):
    # 11-14: 텔레그램 전송 실패(ConnectionError)한 알림은 다음 수집 때 다시, 3번까지
    st = web_admin._hd_store()
    st.add_channel("-1001", "a", "딜")
    calls, ok = [], {"v": False}
    monkeypatch.setattr(web_admin, "hub_notify", lambda key, text, *a, **k: calls.append(key) or {"ok": ok["v"]})
    n = {"i": 0}

    class FakeTG:
        def api(self):
            return (1, "h")

        def collect(self, chans, on_channel, log=None):
            n["i"] += 1
            if n["i"] == 1:
                on_channel(chans[0], [(10, int(time.time()), "당근패드 토스 40% 딜", [], "https://t.me/a/10")], 10, "")
            else:
                on_channel(chans[0], [], 10, "")
            return {}
    monkeypatch.setitem(web_admin._hd, "tg", FakeTG())
    (web_admin.HOTDEAL_DIR / "tg.session").write_text("x")
    st.set_meta("settings", {"kw_on": False, "feed_on": True, "keywords": [], "keep_days": 90})
    web_admin._hd_collect()
    assert calls == ["mr.hotdeal_feed"] and len(st.meta("notify_retry")) == 1
    web_admin._hd_collect()                                  # 또 실패 → tries 2
    assert st.meta("notify_retry")[0]["tries"] == 2
    ok["v"] = True
    web_admin._hd_collect()                                  # 이번엔 성공 → 비움
    assert len(calls) == 3 and st.meta("notify_retry") == []
