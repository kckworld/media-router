# -*- coding: utf-8 -*-
"""공개 사이트 (2026-11-07, W-20261107-09): 공개 Host에선 /pub/·/request·/survey·/static만, 나머지 404 — 가짜 목록으로"""
import json
import time

import pytest

import public_catalog as pc
import web_admin

HOST = sorted(web_admin.REQUEST_PUBLIC_HOSTS)[0]
A, B, C = "a" * 32, "b" * 32, "c" * 32
CAT = {"made": int(time.time()), "items": [
    {"id": A, "name": "런닝맨", "kind": "예능", "type": "series", "year": 2010, "seasons": 1, "episodes": 45, "status": "방영 중",
     "air_days": ["Sunday"], "added": "2026-11-01", "poster": True},
    {"id": B, "name": "가짜 영화", "kind": "영화", "type": "movie", "year": 2020, "seasons": None, "episodes": None, "status": "",
     "air_days": [], "added": "2026-11-05", "poster": False},
    {"id": C, "name": "런닝 하이", "kind": "드라마", "type": "series", "year": 2022, "seasons": 2, "episodes": 16, "status": "완결",
     "air_days": [], "added": "2026-10-01", "poster": True}],
    "recent": [{"id": B, "date": "2026-11-05", "count": 1}, {"id": A, "date": "2026-11-01", "count": 3}]}


@pytest.fixture
def pub(monkeypatch, tmp_path):
    p = tmp_path / "pub_catalog.json"
    p.write_text(json.dumps(CAT, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(web_admin, "PUB_CATALOG_PATH", p)
    monkeypatch.setattr(web_admin, "PUB_POSTER_DIR", tmp_path / "posters")
    monkeypatch.setitem(pc._state, "data", None)
    monkeypatch.setattr(web_admin, "_pub_limit", web_admin.media_requests.RateLimiter(120, 60))
    monkeypatch.setattr(web_admin, "_pub_total", web_admin.media_requests.RateLimiter(3000, 60))
    monkeypatch.setattr(web_admin, "_pub_poster_limit", web_admin.media_requests.RateLimiter(600, 60))
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), tmp_path


def g(c, path, **kw):
    return c.get(path, headers={"Host": HOST}, **kw)


ADMIN_PATHS = ["/login", "/downloads", "/settings", "/api/users/persons", "/users", "/people", "/api/people/search",
               "/skills", "/api/skills/work", "/recon", "/journal", "/requests", "/accounts", "/menu", "/stats",
               "/downloads/pending", "/api/recon", "/logs", "/health"]


def test_public_host_admin_404(pub):
    c, tmp = pub
    for path in ADMIN_PATHS:
        assert g(c, path).status_code == 404, path
    assert c.post("/api/people/search", json={}, headers={"Host": HOST}).status_code == 404
    r = g(c, "/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/pub/")
    assert g(c, "/request").status_code == 200


def test_home_search_and_headers(pub):
    c, tmp = pub
    r = g(c, "/pub/")
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and "최근 추가" in h and "가짜 영화" in h and "미리 보기" not in h
    assert "default-src 'none'" in r.headers["Content-Security-Policy"] and r.headers["X-Frame-Options"] == "DENY"
    assert "<script" not in h
    h = g(c, "/pub/?q=런닝").get_data(as_text=True)
    assert h.index("런닝맨") < h.index("런닝 하이") and "45화" in h and "시즌 2" in h
    h = g(c, "/pub/?q=없는제목").get_data(as_text=True)
    assert "NAS에 없어요" in h and 'href="/request"' in h
    h = g(c, "/pub/?q=<script>alert(1)</script>").get_data(as_text=True)
    assert "<script>alert" not in h


def test_no_internal_info(pub):
    c, tmp = pub
    h = g(c, "/pub/?q=런닝").get_data(as_text=True)
    for leak in ("/volume", "api_key", "JELLYFIN", "192.168.", "8096", "X-Plex-Token"):
        assert leak not in h


def test_mr_host_preview_admin_only(pub, monkeypatch):
    c, tmp = pub
    monkeypatch.setattr(web_admin, "authed", lambda req: ("login", 302))
    assert c.get("/pub/").status_code == 404
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.get("/pub/").status_code == 404
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    assert "미리 보기" in c.get("/pub/").get_data(as_text=True)


def test_poster_only_catalog_ids(pub, monkeypatch):
    c, tmp = pub
    calls = []

    def fake_get(path, raw=False, **q):
        calls.append(path)
        from PIL import Image
        import io
        b = io.BytesIO()
        Image.new("RGB", (300, 450), (200, 10, 10)).save(b, "PNG")
        return b.getvalue()
    monkeypatch.setattr(pc, "_get", fake_get)
    r = g(c, f"/pub/poster/{A}")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and "max-age=86400" in r.headers["Cache-Control"]
    g(c, f"/pub/poster/{A}")
    assert calls == [f"/Items/{A}/Images/Primary"]                                     # 두 번째는 저장한 파일
    assert g(c, f"/pub/poster/{B}").status_code == 404                                 # 포스터 없음
    assert g(c, "/pub/poster/" + "d" * 32).status_code == 404                          # 목록에 없는 id
    assert g(c, "/pub/poster/..%2f..%2fetc").status_code == 404


def test_rate_limit(pub, monkeypatch):
    c, tmp = pub
    monkeypatch.setattr(web_admin, "_pub_limit", web_admin.media_requests.RateLimiter(3, 60))
    codes = [g(c, "/pub/").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_catalog_search_and_kinds():
    assert pc.kind_of("1.movie") == "영화" and pc.kind_of("4-1.Animation-Serise") == "애니" and pc.kind_of("x") == "기타"
    assert [i["name"] for i in pc.search(CAT, "런닝 맨")] == ["런닝맨"]
    assert pc.find_title(CAT, "런닝맨")["id"] == A and pc.find_title(CAT, "없음") is None


def test_auto_list(pub, monkeypatch):
    c, tmp = pub
    v, d = tmp / "v.txt", tmp / "d.txt"
    v.write_text("런닝맨\n# 주석\n나혼자산다 720p\n", encoding="utf-8")
    d.write_text("가짜드라마 | Sniper\n", encoding="utf-8")
    monkeypatch.setattr(web_admin, "BT4G_KEYWORDS_PATH", v)
    monkeypatch.setattr(web_admin, "DRAMA_KEYWORDS_PATH", d)
    monkeypatch.setattr(web_admin, "PUB_TMDB_CACHE", tmp / "tmdb.json")
    monkeypatch.setattr(web_admin, "TMDB_API_KEY", "k")
    monkeypatch.setitem(web_admin._pub_rules_cache, "at", time.time())
    monkeypatch.setitem(web_admin._pub_rules_cache, "rules", {
        "예능": {"enabled": True, "mustContain": "720p-NEXT"}, "먼작귀": {"enabled": True, "mustContain": "^\\[ToonsHub\\] Chiikawa"},
        "꺼진것": {"enabled": False}})
    monkeypatch.setattr(web_admin, "_pub_tmdb_lookup",
                        lambda n: {"poster": "https://image.tmdb.org/t/p/w342/x.jpg"} if n == "먼작귀" else {"poster": "http://evil/x.jpg"})
    h = g(c, "/pub/auto").get_data(as_text=True)
    assert "런닝맨" in h and "나혼자산다" in h and "720p" not in h and "가짜드라마" in h and "Sniper" not in h
    assert "먼작귀" in h and "꺼진것" not in h and "ToonsHub" not in h and "NEXT" not in h
    assert f"/pub/poster/{A}" in h                                                       # NAS에 있는 건 NAS 포스터
    assert "https://image.tmdb.org/t/p/w342/x.jpg" in h and "evil" not in h             # TMDB 주소만
    assert "아직 NAS에 없음" in h


def test_recent_page(pub):
    c, tmp = pub
    h = g(c, "/pub/recent").get_data(as_text=True)
    assert h.index("11월 5일") < h.index("11월 1일") and "3편 추가" in h and "가짜 영화" in h


@pytest.fixture
def acc(pub, monkeypatch, tmp_path):
    c, tmp = pub
    monkeypatch.setattr(web_admin, "ACCOUNT_REQUESTS_PATH", tmp / "acc.json")
    monkeypatch.setattr(web_admin, "_request_code", lambda: "1234")
    monkeypatch.setattr(web_admin, "_acc_ip_limit", web_admin.media_requests.RateLimiter(3, 86400))
    monkeypatch.setattr(web_admin, "_acc_total_limit", web_admin.media_requests.RateLimiter(10, 86400))
    monkeypatch.setattr(web_admin, "_rq_code_fail_limit", web_admin.media_requests.RateLimiter(10, 3600))
    sent = []
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, markup=None, token=None: sent.append((text, markup)) or True)

    class T:                                                 # 알림 스레드를 바로 돌림
        def __init__(self, target, args=(), daemon=None): self.f, self.a = target, args
        def start(self): self.f(*self.a)
    monkeypatch.setattr(web_admin.threading, "Thread", T)
    return c, tmp, sent


def post_acc(c, **form):
    data = {"name": "가짜이름", "services": ["plex"], "code": "1234"}
    data.update(form)
    return c.post("/pub/account", data=data, headers={"Host": HOST})


def test_account_request_flow(acc):
    c, tmp, sent = acc
    h = g(c, "/pub/account").get_data(as_text=True)
    assert 'name="code"' in h and 'name="website"' in h
    assert post_acc(c, code="0000").status_code == 403
    assert post_acc(c, services=[]).status_code == 400
    r = post_acc(c, contact="카톡 가짜", memo="TV로 봐요", services=["plex", "jellyfin"])
    assert r.status_code == 200 and "요청을 보냈어요" in r.get_data(as_text=True) and r.headers["Cache-Control"] == "no-store"
    it = json.loads((tmp / "acc.json").read_text(encoding="utf-8"))["items"][0]
    assert it["status"] == "대기" and it["services"] == ["plex", "jellyfin"] and it["name"] == "가짜이름"
    import os, stat
    assert stat.S_IMODE(os.stat(tmp / "acc.json").st_mode) == 0o600
    text, markup = sent[-1]
    assert "가짜이름" in text and "Plex·Jellyfin" in text
    btns = [b["callback_data"] for b in markup["inline_keyboard"][0]]
    assert btns[0].startswith(f"ar_ok_{it['id']}:") and btns[1].startswith(f"ar_no_{it['id']}:")
    web_admin._tg_account_decide(btns[0].split(":")[0])                               # [승인] 버튼 처리
    assert json.loads((tmp / "acc.json").read_text(encoding="utf-8"))["items"][0]["status"] == "승인"
    assert "직접 만들어" in sent[-1][0]


def test_account_request_limits_and_honeypot(acc):
    c, tmp, sent = acc
    assert "요청을 보냈어요" in post_acc(c, website="bot").get_data(as_text=True)
    assert not (tmp / "acc.json").exists()                                            # 봇 칸 채우면 저장 안 함
    for _ in range(3):
        assert post_acc(c).status_code == 200
    assert post_acc(c).status_code == 429                                              # 접속지당 하루 3건


def test_account_admin_api(acc, monkeypatch):
    c, tmp, sent = acc
    post_acc(c)
    iid = json.loads((tmp / "acc.json").read_text(encoding="utf-8"))["items"][0]["id"]
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    assert c.get("/api/account_requests").get_json()["items"][0]["id"] == iid
    assert c.post("/api/account_requests", json={"id": iid, "status": "거절"}).get_json()["item"]["status"] == "거절"
    assert c.get("/api/account_requests", headers={"Host": HOST}).status_code == 404         # 공개 주소에선 없음
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.post("/api/account_requests", json={"id": iid, "status": "승인"}).status_code == 403
