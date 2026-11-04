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
    monkeypatch.setattr(web_admin, "_people_limit", media_requests.RateLimiter(30, 60))
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    es._cache.update(mtime=None, data=None)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), f


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
    c, f = pp
    caplog.set_level(logging.DEBUG)
    c.post("/api/people/search", json={"q": "홍길동"})
    c.post("/api/people/detail", json={"sabun": "A001"})
    text = caplog.text
    assert "홍길동" not in text and "010-0000-0001" not in text and "A001" not in text
