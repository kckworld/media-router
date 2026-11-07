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
    # 11-07(W-14): 우리 파일 /static/pub.js 하나만, 인라인 스크립트는 없음·CSP로도 막힘
    import re as _re
    assert _re.findall(r"<script[^>]*>", h) == ['<script src="/static/pub.js?v=1" defer>']
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
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
    data = {"name": "가짜이름", "service": "plex", "email": "fake@example.com", "code": "1234"}
    data.update(form)
    return c.post("/pub/account", data=data, headers={"Host": HOST})


def test_account_request_flow(acc):
    c, tmp, sent = acc
    h = g(c, "/pub/account").get_data(as_text=True)
    assert 'name="code"' in h and 'name="website"' in h
    assert post_acc(c, code="0000").status_code == 403
    assert post_acc(c, service="").status_code == 400
    assert post_acc(c, email="not-an-email").status_code == 400
    r = post_acc(c, contact="카톡 가짜", memo="TV로 봐요")
    assert r.status_code == 200 and "요청을 보냈어요" in r.get_data(as_text=True) and r.headers["Cache-Control"] == "no-store"
    it = json.loads((tmp / "acc.json").read_text(encoding="utf-8"))["items"][0]
    assert it["status"] == "대기" and it["service"] == "plex" and it["email"] == "fake@example.com" and it["name"] == "가짜이름"
    import os, stat
    assert stat.S_IMODE(os.stat(tmp / "acc.json").st_mode) == 0o600
    text, markup = sent[-1]
    assert "가짜이름" in text and "Plex 공유 요청" in text and "fake@example.com" in text
    btns = [b["callback_data"] for b in markup["inline_keyboard"][0]]
    assert btns[0].startswith(f"ar_ok_{it['id']}:") and btns[1].startswith(f"ar_no_{it['id']}:")
    web_admin._tg_account_decide(btns[0].split(":")[0])                               # [승인] 버튼 처리
    assert json.loads((tmp / "acc.json").read_text(encoding="utf-8"))["items"][0]["status"] == "승인"
    assert "Plex에서 이 이메일로 라이브러리를 공유해 주세요: fake@example.com" in sent[-1][0]


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


import guides


@pytest.mark.parametrize("src", [
    "<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "[눌러](javascript:alert(1))", "![x](javascript:alert(1))",
    "[a](https://ok.com\" onmouseover=\"alert(1))", "![x](https://evil.com/a.png)", "<iframe src=//evil>", "**<b onclick=x>굵게</b>**",
    "[a](data:text/html;base64,PHNjcmlwdD4=)", "```\n</code></pre><script>x</script>\n```"])
def test_render_blocks_dangerous(src):
    from html.parser import HTMLParser
    found = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.append((tag, dict(attrs)))
    P().feed(guides.render(src))
    allowed = {"p", "br", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "pre", "code", "strong", "em", "hr", "a", "img"}
    for tag, attrs in found:
        assert tag in allowed, tag
        assert not any(k.startswith("on") for k in attrs), attrs
        assert set(attrs) <= {"href", "src", "alt", "rel", "target", "loading"}, attrs
        for k in ("href", "src"):
            v = attrs.get(k)
            if v is not None:
                assert v.startswith(("https://", "http://", "/pub/")) and not v.startswith("https://evil"), v


def test_render_basics():
    h = guides.render("# 제목\n\n**굵게** *기울임* `코드`\n- 하나\n- 둘\n\n1. 첫째\n> 인용\n---\n[링크](https://example.com/a?b=1&c=2) [안](/pub/guide/x)\n"
                      "![그림](/pub/guide/img/" + "a" * 20 + ".png)")
    assert "<h2>제목</h2>" in h and "<strong>굵게</strong>" in h and "<em>기울임</em>" in h and "<code>코드</code>" in h
    assert "<ul>\n<li>하나</li>\n<li>둘</li>\n</ul>" in h and "<ol>" in h and "<blockquote>인용</blockquote>" in h and "<hr>" in h
    assert 'href="https://example.com/a?b=1&amp;c=2" rel="noopener noreferrer nofollow" target="_blank"' in h
    assert 'href="/pub/guide/x">' in h and 'src="/pub/guide/img/' + "a" * 20 + '.png"' in h


def test_image_upload_strips_metadata(tmp_path):
    from PIL import Image
    import io
    im = Image.new("RGB", (3000, 2000), (10, 200, 10))
    exif = Image.Exif()
    exif[0x010F] = "FakeCam"                                                  # 카메라·위치 같은 메타데이터
    exif[0x0131] = "secret-app"
    b = io.BytesIO()
    im.save(b, "JPEG", exif=exif)
    name, err = guides.save_image(tmp_path, b.getvalue())
    assert not err and guides.IMG_RE.match(name)
    out = Image.open(tmp_path / "img" / name)
    assert max(out.size) == 1600 and not out.getexif()
    assert guides.save_image(tmp_path, b"not an image")[1]
    assert guides.save_image(tmp_path, b"x" * (guides.MAX_UPLOAD + 1))[1]
    assert guides.image_path(tmp_path, "../" + name) is None and guides.image_path(tmp_path, name)


@pytest.fixture
def gd(pub, monkeypatch):
    c, tmp = pub
    monkeypatch.setattr(web_admin, "GUIDE_DIR", tmp / "guide")
    return c, tmp


def test_guide_admin_and_public(gd, monkeypatch):
    c, tmp = gd
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    assert c.post("/api/guides", json={"action": "save", "slug": "../x", "text": "a"}).status_code == 400
    r = c.post("/api/guides", json={"action": "save", "slug": "plex-install", "text": "# Plex 설치\n\n앱을 받아요. <script>x</script>"})
    assert r.get_json()["ok"] and r.get_json()["docs"][0]["title"] == "Plex 설치"
    assert "<h2>" in c.post("/api/guides", json={"action": "preview", "text": "# a"}).get_json()["html"]
    h = g(c, "/pub/guide").get_data(as_text=True)
    assert 'href="/pub/guide/plex-install"' in h and "Plex 설치" in h
    h = g(c, "/pub/guide/plex-install").get_data(as_text=True)
    assert "앱을 받아요. &lt;script&gt;" in h and "<script>x" not in h
    assert g(c, "/pub/guide/없는문서").status_code == 404
    assert c.post("/api/guides", json={"action": "save"}, headers={"Host": HOST}).status_code == 404   # 공개 주소에서 편집 불가
    assert c.post("/api/guides", json={"action": "delete", "slug": "plex-install"}).get_json()["ok"]
    assert list((tmp / "guide" / ".trash").glob("plex-install.*.md"))                                 # 지우면 .trash로
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")
    assert c.post("/api/guides", json={"action": "preview", "text": "a"}).status_code == 403
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    monkeypatch.setattr(web_admin, "current_user", lambda req: web_admin.accounts.OWNER)
    assert c.post("/api/guides", json={"action": "preview", "text": "a"}).status_code == 403


def test_guide_image_route(gd, monkeypatch):
    c, tmp = gd
    from PIL import Image
    import io
    b = io.BytesIO()
    Image.new("RGB", (50, 50)).save(b, "PNG")
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    r = c.post("/api/guides/image", data={"file": (io.BytesIO(b.getvalue()), "a.png")}, content_type="multipart/form-data").get_json()
    assert r["ok"] and r["markdown"].startswith("![그림](/pub/guide/img/")
    resp = g(c, r["url"])
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert g(c, "/pub/guide/img/" + "0" * 20 + ".png").status_code == 404


# ── 11-07 인사팀 점검 ①: Host 끝 점·대문자·포트·모르는 Host로 공개 막기가 풀리면 안 됨
EVIL_HOSTS = ["request.kckworld.com.", "REQUEST.KCKWORLD.COM", "request.kckworld.com:443", "Request.Kckworld.Com.:443",
              "request.kckworld.com..", "unknown.example", "evil.com", "media.kckworld.com.evil.com", "", "  ",
              "192.168.50.153.nip.io", "[2001:db8::1]:443", "media.kckworld.com@evil"]


@pytest.mark.parametrize("host", EVIL_HOSTS)
def test_non_admin_hosts_are_public(pub, host):
    c, tmp = pub
    for path in ("/login", "/downloads", "/settings", "/api/people/search", "/guides", "/users", "/logs", "/"):
        r = c.get(path, headers={"Host": host})
        assert r.status_code in (404, 302), (host, path, r.status_code)
        if r.status_code == 302:
            assert path == "/" and r.headers["Location"].endswith("/pub/")
    assert c.post("/login", data={"pw": "x"}, headers={"Host": host}).status_code == 404
    r = c.get("/pub/", headers={"Host": host})
    assert r.status_code in (200, 400) and "미리 보기" not in r.get_data(as_text=True)   # 400 = werkzeug가 이상한 Host 거절


@pytest.mark.parametrize("host", ["media.kckworld.com", "MEDIA.kckworld.com.", "media.kckworld.com:443", "192.168.50.153:5080",
                                  "localhost", "127.0.0.1:5080", "172.17.0.1:5080", "[::1]:5080", "kckworld"])
def test_admin_hosts_still_admin(pub, host):
    c, tmp = pub
    assert c.get("/login", headers={"Host": host}).status_code == 200, host


def test_norm_host_and_env(monkeypatch):
    n = web_admin._norm_host
    assert n("Request.KCKWorld.com.:443") == "request.kckworld.com" and n("[::1]:5080") == "::1" and n("a b") == ""
    assert n("media.kckworld.com@evil") == ""


def test_public_body_limit(pub):
    c, tmp = pub
    r = c.post("/request/submit", data=b"x" * (web_admin.PUBLIC_MAX_BODY + 1), headers={"Host": HOST, "Content-Type": "application/json"})
    assert r.status_code == 413
    assert web_admin.app.config["MAX_CONTENT_LENGTH"] == 10 * 1024 * 1024


def test_find_title_prefers_kind_and_contains():
    d = {"items": [
        {"id": "1" * 32, "name": "원피스", "kind": "드라마", "type": "series", "episodes": 8},
        {"id": "2" * 32, "name": "원피스", "kind": "애니", "type": "series", "episodes": 1189},
        {"id": "3" * 32, "name": "미래소년 코난", "kind": "애니", "type": "series", "episodes": 26},
        {"id": "4" * 32, "name": "명탐정 코난", "kind": "애니", "type": "series", "episodes": 1218},
        {"id": "5" * 32, "name": "명탐정 코난: 베이커가의 망령", "kind": "애니", "type": "movie", "episodes": None}]}
    assert pc.find_title(d, "원피스", "애니")["episodes"] == 1189                      # 전: 드라마(8화)
    assert pc.find_title(d, "원피스", "드라마")["episodes"] == 8
    assert pc.find_title(d, "코난", "애니")["name"] == "명탐정 코난"                    # 전: 못 찾음
    assert pc.find_title(d, "원피스")["kind"] == "드라마"                               # 종류 안 주면 예전처럼


def test_auto_order_drama_anime_variety(pub, monkeypatch):
    c, tmp = pub
    v, d = tmp / "v.txt", tmp / "d.txt"
    v.write_text("런닝맨\n", encoding="utf-8")
    d.write_text("가짜드라마\n", encoding="utf-8")
    monkeypatch.setattr(web_admin, "BT4G_KEYWORDS_PATH", v)
    monkeypatch.setattr(web_admin, "DRAMA_KEYWORDS_PATH", d)
    monkeypatch.setattr(web_admin, "PUB_TMDB_CACHE", tmp / "tmdb.json")
    monkeypatch.setattr(web_admin, "TMDB_API_KEY", "")
    monkeypatch.setitem(web_admin._pub_rules_cache, "at", time.time())
    monkeypatch.setitem(web_admin._pub_rules_cache, "rules", {"먼작귀": {"enabled": True}})
    h = g(c, "/pub/auto").get_data(as_text=True)
    assert h.index("<h2>드라마") < h.index("<h2>애니") < h.index("<h2>예능")


def test_plex_recent_uses_added_at(monkeypatch):
    import xml.etree.ElementTree as ET
    now = int(time.time())
    secs = '<MediaContainer><Directory key="2" type="show" title="2.드라마"/><Directory key="1" type="movie" title="1.영화"/></MediaContainer>'
    shows = (f'<MediaContainer><Video grandparentTitle="런닝 하이" grandparentRatingKey="g1" addedAt="{now - 100}" parentIndex="2" index="16"/>'
             f'<Video grandparentTitle="런닝 하이" grandparentRatingKey="g1" addedAt="{now - 200}" parentIndex="2" index="15"/>'
             f'<Video grandparentTitle="모르는 작품" grandparentRatingKey="g2" addedAt="{now - 300}" parentIndex="1" index="1"/>'
             f'<Video grandparentTitle="옛 드라마" grandparentRatingKey="g3" addedAt="{now - 40 * 86400}" parentIndex="1" index="1"/></MediaContainer>')
    movies = f'<MediaContainer><Video title="가짜 영화" ratingKey="m1" addedAt="{now - 50}"/></MediaContainer>'
    monkeypatch.setenv("PLEX_URL", "http://px:32400")
    monkeypatch.setenv("PLEX_TOKEN", "T")

    class R:
        def __init__(self, body): self.b = body.encode()
        def read(self): return self.b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def fake_open(req, timeout=None):
        u = req.full_url
        return R(secs if u.split("?")[0].endswith("/library/sections") else shows if "/sections/2/" in u else movies)
    monkeypatch.setattr(pc.urllib.request, "urlopen", fake_open)
    r = pc.plex_recent(CAT)
    assert [(x["id"], x["count"], x["last"]) for x in r] == [(B, 1, ""), (C, 2, "16화")]   # 모르는 작품·14일 지난 것 빠짐


def test_request_page_new_frame(pub):
    c, tmp = pub
    h = g(c, "/request").get_data(as_text=True)
    assert 'aria-current="page">영상 요청<' in h and "🎬 NAS 미디어" in h


# ── 11-07 W-14: 가로 줄 + 설명 창
import public_tmdb


@pytest.fixture
def tm(pub, monkeypatch):
    c, tmp = pub
    calls = []

    def fake_get(api_key, path, **params):
        calls.append((path, params.get("query")))
        assert api_key == "TMDBKEY"
        if path.startswith("/search/tv"):
            return {"results": [{"id": 77, "name": params["query"]}]}
        if path.startswith("/search/movie"):
            return {"results": []}
        if path == "/tv/77":
            return {"id": 77, "name": "런닝맨", "original_name": "Running Man", "overview": "가짜 줄거리 <b>굵게</b>",
                    "first_air_date": "2010-07-11", "last_air_date": "2026-11-02", "status": "Returning Series",
                    "genres": [{"name": "리얼리티"}, {"name": "코미디"}], "networks": [{"name": "SBS"}],
                    "number_of_seasons": 1, "number_of_episodes": 780, "poster_path": "/abc.jpg"}
        return None
    monkeypatch.setattr(public_tmdb, "_get", fake_get)
    monkeypatch.setattr(web_admin, "TMDB_API_KEY", "TMDBKEY")
    monkeypatch.setattr(web_admin, "PUB_TMDB_DETAIL", tmp / "tmdb_detail.json")
    return c, tmp, calls


def test_title_modal_fragment_and_page(tm):
    c, tmp, calls = tm
    r = g(c, f"/pub/title?id={A}&frag=1")
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and "<html" not in h and 'id="tiTitle"' in h
    assert "가짜 줄거리 &lt;b&gt;굵게&lt;/b&gt;" in h and "리얼리티" in h and "SBS" in h and "2010.07.11 ~ 2026.11.02" in h
    assert "시즌 1개 · 45화 있음" in h and "780화 (TMDB)" in h                        # NAS 회차 + TMDB 전체
    assert 'href="https://www.themoviedb.org/tv/77?language=ko-KR"' in h and 'target="_blank"' in h
    assert "TMDBKEY" not in h and "api_key" not in h and "/volume" not in h
    full = g(c, f"/pub/title?id={A}").get_data(as_text=True)
    assert "<html" in full and "가짜 줄거리" in full
    g(c, f"/pub/title?id={A}&frag=1")
    assert len(calls) == 2                                                             # 두 번째는 캐시(검색+상세 한 번씩)
    h = g(c, f"/pub/title?id={B}&frag=1").get_data(as_text=True)                     # 영화: TMDB 없음 → NAS 정보만
    assert "TMDB 자료를 찾지 못했어요" in h and "있음" in h


def test_title_only_listed(tm, monkeypatch):
    c, tmp, calls = tm
    assert g(c, "/pub/title?id=" + "f" * 32).status_code == 404                        # 목록에 없는 id
    assert g(c, "/pub/title?id=../x").status_code == 404
    assert g(c, "/pub/title?t=아무거나&k=예능").status_code == 404                     # 자동 목록에 없는 제목 → TMDB 대신 찾기 안 함
    assert g(c, "/pub/title").status_code == 404
    v, d = tmp / "v.txt", tmp / "d.txt"
    v.write_text("가짜 새예능\n", encoding="utf-8")
    d.write_text("", encoding="utf-8")
    monkeypatch.setattr(web_admin, "BT4G_KEYWORDS_PATH", v)
    monkeypatch.setattr(web_admin, "DRAMA_KEYWORDS_PATH", d)
    monkeypatch.setitem(web_admin._pub_rules_cache, "at", time.time())
    monkeypatch.setitem(web_admin._pub_rules_cache, "rules", {})
    h = g(c, "/pub/title?t=가짜 새예능&k=예능&frag=1").get_data(as_text=True)
    assert "아직 없음" in h and calls[0] == ("/search/tv", "가짜 새예능")
    assert calls and not any(q == "아무거나" for _, q in calls)


def test_rows_and_tile_links(tm, monkeypatch):
    c, tmp, calls = tm
    h = g(c, "/pub/recent").get_data(as_text=True)
    assert h.count('class="row" data-row') == 2 and 'class="strip"' in h                # 날짜마다 한 줄
    assert f'href="/pub/title?id={B}" data-title' in h
    h = g(c, "/pub/").get_data(as_text=True)
    assert 'data-row' in h and 'id="tiDlg"' in h
    js = g(c, "/static/pub.js")
    assert js.status_code == 200 and "showModal" in js.get_data(as_text=True)



def test_account_service_split(acc):
    c, tmp, sent = acc
    h = g(c, "/pub/account").get_data(as_text=True)                                   # 기본 Plex
    assert 'value="plex"' in h and 'name="email"' in h and 'name="username"' not in h and "/pub/guide/00-plex-signup" in h
    assert 'href="/pub/account?svc=plex" class="on"' in h
    h = g(c, "/pub/account?svc=jellyfin").get_data(as_text=True)
    assert 'value="jellyfin"' in h and 'name="username"' in h and 'name="email"' not in h
    assert post_acc(c, service="jellyfin", username="ab").status_code == 400             # 3자 미만
    assert post_acc(c, service="jellyfin", username="bad id!").status_code == 400
    r = post_acc(c, service="jellyfin", username="new_user-1", email="")
    assert r.status_code == 200
    it = json.loads((tmp / "acc.json").read_text(encoding="utf-8"))["items"][0]
    assert it["service"] == "jellyfin" and it["username"] == "new_user-1" and "email" not in it
    assert "Jellyfin 계정 생성 요청" in sent[-1][0] and "new_user-1" in sent[-1][0]
    web_admin._tg_account_decide("ar_ok_" + it["id"])
    assert "Jellyfin에서 계정을 만들어 주세요: new_user-1" in sent[-1][0]


def test_email_not_echoed_on_error(acc):
    c, tmp, sent = acc
    h = post_acc(c, email="secret.person@example.com", code="0000").get_data(as_text=True)
    assert "secret.person@example.com" not in h                                            # 개인정보는 응답에 다시 안 넣음
    h = post_acc(c, email="secret.person@example.com", name="").get_data(as_text=True)
    assert "secret.person@example.com" not in h


def test_genres_korean():
    assert public_tmdb.genres_ko(["Sci-Fi & Fantasy", "Drama", "Action & Adventure", "Kids", "Reality", "드라마", "Unknown"]) == \
        ["SF·판타지", "드라마", "액션·모험", "어린이", "리얼리티", "Unknown"]


def test_genre_same_as_kind_hidden(tm, monkeypatch):
    c, tmp, calls = tm
    h = g(c, f"/pub/title?id={A}&frag=1").get_data(as_text=True)
    assert h.count(">예능<") == 1 and "리얼리티" in h


def test_guide_old_quality_redirects_and_done_link(gd, acc):
    c, tmp = gd
    r = g(c, "/pub/guide/04-quality")
    assert r.status_code == 301 and r.headers["Location"].endswith("/pub/guide/00-essential")   # 11-07 W-16
    (tmp / "guide").mkdir(exist_ok=True)
    (tmp / "guide" / "04-quality.md").write_text("# 남아 있으면 그대로", encoding="utf-8")
    assert g(c, "/pub/guide/04-quality").status_code == 200
    c2, tmp2, sent = acc
    h = post_acc(c2).get_data(as_text=True)
    assert "요청을 보냈어요" in h and 'href="/pub/guide/00-essential"' in h and "필수 설정 먼저" in h


# ── 11-07 W-17: 공개 통계
import public_stats

FULL = {"days": 30, "popular7": [{"content": "가짜 드라마 S01", "plays": 12}], "popular30": [{"content": "가짜 드라마 S01", "plays": 30}],
        "daily": [{"day": "2026-11-01", "plays": 5}, {"day": "2026-11-02", "plays": 9}],
        "types": [{"kind": "드라마", "plays": 40, "pct": 80}, {"kind": "기타", "plays": 10, "pct": 20}],
        "hourly": [0] * 23 + [4], "playback": {"direct_pct": 70, "transcode_pct": 30, "since": "2026-10-28"},
        "completion": {"avg_pct": 77, "finish_pct": 90, "best": [{"content": "가짜 영화", "avg_pct": 99}]}}


@pytest.fixture
def st(pub, monkeypatch):
    c, tmp = pub
    monkeypatch.setattr(web_admin, "PUB_STATS_CARDS", tmp / "cards.json")
    built = []

    def fake(key, fn, ttl=None):
        cards = list(key[1])
        built.append(cards)
        return {k: v for k, v in FULL.items() if k == "days" or any(k.startswith(x) for x in cards)}
    monkeypatch.setattr(web_admin, "_watch_stats", fake)
    return c, tmp, built


def test_stats_default_cards_only(st):
    c, tmp, built = st
    r = g(c, "/pub/stats")
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and built[-1] == ["popular", "daily", "types"]           # 처음엔 세 장만
    assert "인기 작품" in h and "일별 재생" in h and "종류별 비율" in h and "가짜 드라마 S01" in h
    assert "시간대별" not in h and "재생 방식" not in h and "끝까지 본" not in h
    assert 'href="/pub/stats" class="on"' in h


def test_stats_toggle_owner_only_and_off_cards_not_computed(st, monkeypatch):
    c, tmp, built = st
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    assert c.post("/api/guides/stats", json={"enabled": ["playback", "nope"]}).get_json()["enabled"] == ["playback"]
    h = g(c, "/pub/stats").get_data(as_text=True)
    assert built[-1] == ["playback"] and "재생 방식" in h and "인기 작품" not in h and "/pub/guide/00-essential" in h
    assert c.post("/api/guides/stats", json={"enabled": []}, headers={"Host": HOST}).status_code == 404
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")
    assert c.post("/api/guides/stats", json={"enabled": ["popular"]}).status_code == 403
    c.post("/api/guides/stats", json={"enabled": []})                                  # (막혔으니 그대로 playback)
    monkeypatch.setattr(web_admin, "current_user", lambda req: web_admin.accounts.OWNER)
    c.post("/api/guides/stats", json={"enabled": []})
    built.clear()
    h = g(c, "/pub/stats").get_data(as_text=True)
    assert built == [] and "공개한 통계가 없어요" in h                                 # 다 끄면 계산도 안 함


class _Cur:
    """SQL을 기록하고 정해진 결과를 돌려주는 가짜 커서"""
    def __init__(self): self.sql = []; self.res = []
    def execute(self, sql, args=None):
        self.sql.append(sql)
        s = " ".join(sql.split())
        if "COUNT(DISTINCT person_id) AS viewers" in s:
            self.res = [("인기 A", 10, 3, 1.0), ("적은 B", 2, 2, 0.1)]
            self.description = [("content",), ("plays",), ("viewers",), ("hours",)]
        elif "GROUP BY 1, 2" in s and "media_type" in s:
            self.res = [("episode", "가짜드라마", 50), ("movie", "가짜 영화", 8), ("episode", "희귀", 1)]
        elif "AS day" in s:
            self.res = [("2026-11-01", 4)]
        else:
            self.res = []
    def fetchall(self): return self.res


def test_build_no_personal_fields_and_small_counts():
    cur = _Cur()
    d = public_stats.build(cur, ["popular", "daily", "types"], kind_of=lambda n, mt: "드라마" if n == "가짜드라마" else "")
    assert [r["content"] for r in d["popular7"]] == ["인기 A"] and set(d["popular7"][0]) == {"content", "plays"}   # viewers·hours 안 냄
    assert {r["kind"] for r in d["types"]} == {"드라마", "영화", "기타"}               # 1회짜리는 기타로
    blob = json.dumps(d, ensure_ascii=False)
    for bad in ("person", "username", "user", "ip", "device", "region", "viewer"):
        assert bad not in blob
    sql = " ".join(cur.sql).lower()
    for bad in ("person_name", "username", "public_ip", "viewer_ip", "region", "device"):
        assert bad not in sql                                                          # 계산할 때도 안 씀
    assert "hourly" not in d and "playback" not in d and "completion" not in d
