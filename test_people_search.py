# -*- coding: utf-8 -*-
"""생활 > 검색직 (2026-11-04): 관리자만, POST 검색, 분당 제한, 기록 없음 — 가짜 직원 데이터로"""
import json
import logging

import pytest

import employee_search as es
import media_requests
import web_admin

FAKE = [
    {"name": "홍길동", "orignOrgNm": "영업1팀", "jikweeNm": "과장", "jikchakNm": "팀원", "jikgubNm": "사무", "statusNm": "재직",
     "sabun": "A001", "empYmd": "20150302", "birYmd": "19880315", "handPhone": "010-0000-0001", "officeTel": "1001", "mailId": "hong@ex.com"},
    {"name": "홍길순", "orignOrgNm": "영업2팀", "prevOrgNm": "영업1팀", "jikweeNm": "대리", "jikgubNm": "사무", "sabun": "A002",
     "empYmd": "20190101", "birYmd": "19920310"},
] + [{"name": f"김가짜{i}", "orignOrgNm": "생산팀", "jikgubNm": "기술", "sabun": f"B{i:03d}", "empYmd": "20200101",
      "birYmd": f"1990{(i % 12) + 1:02d}01"} for i in range(15)]


@pytest.fixture
def pp(monkeypatch, tmp_path):
    f = tmp_path / "employees.json"
    f.write_text(json.dumps(FAKE, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(web_admin, "EMPLOYEE_JSON", f)
    monkeypatch.setattr(web_admin, "PEOPLE_LOG_PATH", tmp_path / "people_search_log.jsonl")
    monkeypatch.setattr(web_admin, "BOT_SEARCH_LOG", tmp_path / "bot" / "search_log.jsonl")
    monkeypatch.setattr(web_admin, "_people_limit", media_requests.RateLimiter(30, 60))
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "_people_fresh", lambda req: True)
    alerts = []
    monkeypatch.setattr(web_admin, "_people_note_lookup", lambda req: alerts.append(1))
    es._cache.update(mtime=None, data=None)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), f


def test_stale_session_needs_password_again(pp, monkeypatch):
    """11-04 사용자 결정: 로그인한 지 24시간 넘은 세션은 검색직에서 비밀번호 다시"""
    c, f = pp
    monkeypatch.setattr(web_admin, "_people_fresh", lambda req: False)
    r = c.post("/api/people/search", json={"q": "홍"})
    assert r.status_code == 401 and r.get_json()["reauth"] is True
    html = c.get("/people").get_data(as_text=True)
    assert "비밀번호 다시 입력" in html and 'id="ppForm"' not in html


def test_session_age_from_token(monkeypatch):
    from werkzeug.test import EnvironBuilder
    import time as _t
    monkeypatch.setattr(web_admin, "PASSWORD", "pw")
    tok = web_admin._session_serializer.dumps({"a": True})
    req = web_admin.app.request_class(EnvironBuilder(headers={"Cookie": f"media_admin={tok}"}).get_environ())
    assert web_admin._session_login_age(req) < 5 and web_admin._people_fresh(req)
    later = _t.time() + 25 * 3600
    monkeypatch.setattr(web_admin.time, "time", lambda: later)
    assert not web_admin._people_fresh(req)                                    # 25시간 뒤
    bad = web_admin.app.request_class(EnvironBuilder(headers={"Cookie": "media_admin=x"}).get_environ())
    assert web_admin._session_login_age(bad) is None and not web_admin._people_fresh(bad)


def test_reauth_flow(pp, monkeypatch):
    c, f = pp
    monkeypatch.setattr(web_admin, "PASSWORD", "right-pw")
    monkeypatch.setattr(web_admin, "_login_failures", {})
    monkeypatch.setattr(web_admin, "_login_global", {"fails": [], "locked_until": 0.0})
    r = c.post("/people/reauth", data={"pw": "wrong"})
    assert r.headers["Location"].endswith("/people?e=wrong")
    for _ in range(5):
        r = c.post("/people/reauth", data={"pw": "wrong"})
    assert r.headers["Location"].endswith("/people?e=locked")                  # 로그인과 같은 잠금
    monkeypatch.setattr(web_admin, "_login_failures", {})
    r = c.post("/people/reauth", data={"pw": "right-pw"})
    assert r.headers["Location"].endswith("/people") and "media_admin=" in r.headers.get("Set-Cookie", "")


def test_lookup_alert_batched_without_query(monkeypatch):
    """조회 알림: 10분 묶음의 첫 번째만 바로, 나머지는 세었다가 한 번에. 검색어·결과는 안 넣음"""
    from werkzeug.test import EnvironBuilder
    sent, timers = [], []
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, *a, **k: sent.append(text) or True)
    monkeypatch.setattr(web_admin.threading, "Thread", lambda target, daemon=None: type("T", (), {"start": lambda s: target()})())

    class FakeTimer:
        def __init__(self, sec, fn):
            self.fn = fn
            self.daemon = False
            timers.append(self)

        def start(self):
            pass
    monkeypatch.setattr(web_admin.threading, "Timer", FakeTimer)
    monkeypatch.setattr(web_admin, "_people_alert", {"until": 0.0, "count": 0, "ip": "", "timer": None})
    req = web_admin.app.request_class(EnvironBuilder(environ_base={"REMOTE_ADDR": "192.168.50.123"}).get_environ())
    for _ in range(4):
        web_admin._people_note_lookup(req)
    assert len(sent) == 1 and sent[0].startswith("🔎 검색직 조회됨") and "…123" in sent[0] and "192.168" not in sent[0]
    timers[0].fn()
    assert len(sent) == 2 and "3번 더 조회됨" in sent[1]


def test_list_max_100_and_detail_by_mail(pp):
    c, f = pp
    many = [{"name": f"박가짜{i}", "orignOrgNm": "본사", "birYmd": "19900101", "sabun": f"P{i}"} for i in range(150)]
    many.append({"name": "사번없음", "orignOrgNm": "본사", "mailId": "nosabun@example.com"})
    f.write_text(json.dumps(FAKE + many, ensure_ascii=False), encoding="utf-8")
    import os, time
    os.utime(f, (time.time() + 9, time.time() + 9))
    r = c.post("/api/people/search", json={"mode": "search_org", "q": "본사"}).get_json()
    assert r["total"] == 151 and len(r["list"]) == 100
    r2 = c.post("/api/people/search", json={"mode": "search_name", "q": "사번없음"}).get_json()
    assert r2["detail"][0]["mailId"] == "nosabun@example.com"
    lst = c.post("/api/people/search", json={"mode": "search_org", "q": "본"}).get_json()["list"]
    assert all(row["key"] for row in lst)
    d = c.post("/api/people/detail", json={"key": "nosabun@example.com"}).get_json()
    assert d["ok"] and d["detail"]["name"] == "사번없음"                       # 사번 없는 사람도 상세 열림


def test_search_like_bot(pp):
    c, f = pp
    html = c.get("/people").get_data(as_text=True)
    assert "검색직" in html and 'id="ppForm"' in html and "생월" in html
    r = c.post("/api/people/search", json={"mode": "search_name", "q": "홍길"}).get_json()
    assert r["ok"] and r["total"] == 2 and r["detail"][0]["handPhone"] == "010-0000-0001" and r["detail"][1]["prevOrgNm"] == "영업1팀"
    many = c.post("/api/people/search", json={"mode": "search_name", "q": "김가짜"}).get_json()
    assert many["total"] == 15 and "detail" not in many and "handPhone" not in many["list"][0]    # 10명 넘으면 목록만
    assert c.post("/api/people/search", json={"mode": "search_birthMonth", "q": "3"}).get_json()["total"] == 4
    assert c.post("/api/people/search", json={"mode": "search_org", "q": "영업"}).get_json()["total"] == 2
    assert c.post("/api/people/search", json={"mode": "x", "q": "이름 홍길동"}).get_json()["detail"][0]["sabun"] == "A001"
    d = c.post("/api/people/detail", json={"sabun": "B003"}).get_json()
    assert d["ok"] and d["detail"]["name"] == "김가짜3"
    assert c.post("/api/people/detail", json={"sabun": "없음"}).status_code == 404
    assert c.post("/api/people/search", json={"q": "홍"}).headers["Cache-Control"] == "no-store"


def test_reload_when_file_changes(pp):
    import os, time
    c, f = pp
    assert c.post("/api/people/search", json={"q": "새사람"}).get_json()["total"] == 0
    f.write_text(json.dumps(FAKE + [{"name": "새사람", "sabun": "N1"}], ensure_ascii=False), encoding="utf-8")
    os.utime(f, (time.time() + 5, time.time() + 5))
    assert c.post("/api/people/search", json={"q": "새사람"}).get_json()["total"] == 1


def test_access_control(pp, monkeypatch):
    c, f = pp
    assert c.get("/api/people/search").status_code == 405                   # GET 검색 없음(검색어가 접근 로그에 안 남게)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.post("/api/people/search", json={"q": "홍"}).status_code == 403
    assert c.post("/api/people/detail", json={"sabun": "A001"}).status_code == 403
    assert c.get("/people", headers={"Accept": "text/html"}).status_code == 302
    assert "/people" not in c.get("/downloads").get_data(as_text=True)          # 보기 전용 메뉴엔 없음
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    host = sorted(web_admin.REQUEST_PUBLIC_HOSTS)[0]
    assert c.post("/api/people/search", json={"q": "홍"}, headers={"Host": host}).status_code == 404
    monkeypatch.setattr(web_admin, "authed", lambda req: ("login", 302))
    assert c.post("/api/people/search", json={"q": "홍"}).status_code == 401


def test_rate_limit_30_per_minute(pp):
    c, f = pp
    for _ in range(30):
        assert c.post("/api/people/search", json={"q": "홍"}).status_code == 200
    assert c.post("/api/people/search", json={"q": "홍"}).status_code == 429
    assert c.post("/api/people/detail", json={"sabun": "A001"}).status_code == 429


def test_nothing_logged(pp, caplog):
    # 11-07부터 검색어는 검색 로그 파일에만(앱 로그엔 여전히 아무것도), 결과 내용은 어디에도 없음
    c, f = pp
    caplog.set_level(logging.DEBUG)
    c.post("/api/people/search", json={"q": "홍길동"})
    c.post("/api/people/detail", json={"sabun": "A001"})
    text = caplog.text
    assert "홍길동" not in text and "010-0000-0001" not in text and "A001" not in text
    log = web_admin.PEOPLE_LOG_PATH.read_text(encoding="utf-8")
    assert "010-0000-0001" not in log and "hong@ex.com" not in log and "A001" not in log and "영업1팀" not in log
    assert log.count("\n") == 1                                               # 상세 보기는 검색이 아니라 안 남김


def test_sort_controls_in_page(pp):
    # 11-07: 목록은 표 머리(이름·소속·생년월일) 눌러 정렬, 자세히 카드는 정렬 고르기 — 화면 안 JS로만(서버에 정렬값 안 보냄)
    c, f = pp
    h = c.get("/people").get_data(as_text=True)
    assert 'data-sort="\' + key' in h or "headBtn(\"name\"" in h
    for k in ('"name"', '"org"', '"bir"', '"emp"', '"jikwee"'):
        assert k in h
    assert "aria-sort" in h and 'id="ppSort"' in h and "Intl.Collator(\"ko\"" in h
