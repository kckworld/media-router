# -*- coding: utf-8 -*-
"""검색직 검색 로그 (2026-11-07): MR 검색 기록(600·결과 없음), 봇 로그 합쳐 보기, 거르기, CSV, 소유자만 — 가짜 데이터로"""
import json
import os
import stat
from datetime import datetime, timedelta

import pytest

import accounts
import employee_search as es
import media_requests
import people_log
import web_admin

FAKE = [{"name": "홍길동", "orignOrgNm": "영업1팀", "jikgubNm": "사무", "sabun": "A001", "empYmd": "20150302",
         "birYmd": "19880315", "handPhone": "010-0000-0001", "mailId": "hong@ex.com"},
        {"name": "홍길순", "orignOrgNm": "영업2팀", "jikgubNm": "사무", "sabun": "A002", "empYmd": "20190101", "birYmd": "19920310"}]
BOT = [{"time": "2026-11-06 09:00:00", "source": "bot", "user_id": "111", "name": "김봇", "username": "kimbot", "mode": "이름",
        "query": "이순신", "result_count": 0, "allowed": True},
       {"time": "2026-11-06 10:00:00", "source": "bot", "user_id": "222", "name": "낯선이", "mode": "이름",
        "query": "=cmd|' /C calc'!A0", "result_count": None, "allowed": False, "note": "not_approved"}]


@pytest.fixture
def lg(monkeypatch, tmp_path):
    f = tmp_path / "employees.json"
    f.write_text(json.dumps(FAKE, ensure_ascii=False), encoding="utf-8")
    bot = tmp_path / "search_log" / "search_log.jsonl"
    bot.parent.mkdir()
    bot.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in BOT) + "깨진 줄\n", encoding="utf-8")
    monkeypatch.setattr(web_admin, "EMPLOYEE_JSON", f)
    monkeypatch.setattr(web_admin, "PEOPLE_LOG_PATH", tmp_path / "people_search_log.jsonl")
    monkeypatch.setattr(web_admin, "BOT_SEARCH_LOG", bot)
    monkeypatch.setattr(web_admin, "_people_limit", media_requests.RateLimiter(30, 60))
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "_people_fresh", lambda req: True)
    alerts = []
    monkeypatch.setattr(web_admin, "_people_alert_search", lambda *a: alerts.append(a))
    es._cache.update(mtime=None, data=None)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), tmp_path, alerts


def test_mr_search_recorded_600_without_results(lg):
    c, tmp, alerts = lg
    assert c.post("/api/people/search", json={"mode": "search_name", "q": "홍길"}).get_json()["total"] == 2
    p = web_admin.PEOPLE_LOG_PATH
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert rec["source"] == "MR" and rec["name"] == "소유자" and rec["mode"] == "이름" and rec["query"] == "홍길"
    assert rec["result_count"] == 2 and rec["allowed"] is True and len(rec["time"]) == 19
    raw = p.read_text(encoding="utf-8")
    for leak in ("010-0000-0001", "hong@ex.com", "A001", "영업1팀", "19880315"):
        assert leak not in raw
    c.post("/api/people/search", json={"mode": "search_name", "q": "   "})        # 빈 검색어는 안 남김
    assert p.read_text(encoding="utf-8").count("\n") == 1


def test_log_view_merged_newest_first_and_filters(lg):
    c, tmp, alerts = lg
    c.post("/api/people/search", json={"mode": "search_org", "q": "영업"})
    d = c.post("/api/people/log", json={}).get_json()
    assert d["ok"] and d["total"] == 3 and d["files"] == {"mr": True, "bot": True}
    assert [r["source"] for r in d["rows"]] == ["MR", "bot", "bot"]                  # 최근 순(MR이 오늘)
    assert d["rows"][1]["allowed"] is False and d["rows"][1]["note"] == "not_approved"
    assert alerts                                                                     # 로그 보기도 조회 알림 규칙 그대로
    f = lambda body: [r["query"] for r in c.post("/api/people/log", json=body).get_json()["rows"]]
    assert f({"person": "kimbot"}) == ["이순신"]
    assert f({"q": "이순"}) == ["이순신"]
    assert f({"source": "MR"}) == ["영업"]
    assert f({"from": "2026-11-06", "to": "2026-11-06"}) == ["=cmd|' /C calc'!A0", "이순신"]
    assert f({"to": "2026-11-05"}) == []


def test_csv_download(lg):
    c, tmp, alerts = lg
    r = c.post("/api/people/log.csv", data={"source": "bot"})
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("text/csv")
    assert "attachment" in r.headers["Content-Disposition"] and r.headers["Cache-Control"] == "no-store"
    body = r.get_data(as_text=True)
    assert body.startswith("﻿시각,출처,사람") and "김봇" in body and "승인 안 됨" in body
    assert "'=cmd" in body                                                            # 엑셀 수식 끼워넣기 막음


def test_owner_only_and_public_blocked(lg, monkeypatch):
    c, tmp, alerts = lg
    host = sorted(web_admin.REQUEST_PUBLIC_HOSTS)[0]
    assert c.post("/api/people/log", json={}, headers={"Host": host}).status_code == 404
    assert c.post("/api/people/log.csv", data={}, headers={"Host": host}).status_code == 404
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.post("/api/people/log", json={}).status_code == 403
    assert c.post("/api/people/log.csv", data={}).status_code == 403
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")              # 일반 계정
    assert c.post("/api/people/log", json={}).status_code == 403
    monkeypatch.setattr(web_admin, "_people_fresh", lambda req: False)
    monkeypatch.setattr(web_admin, "current_user", lambda req: accounts.OWNER)
    assert c.post("/api/people/log", json={}).get_json().get("reauth") is True       # 24시간 지나면 비밀번호 다시


def test_log_tab_page(lg, monkeypatch):
    c, tmp, alerts = lg
    h = c.get("/people?tab=log").get_data(as_text=True)
    assert 'id="lgForm"' in h and 'action="/api/people/log.csv"' in h and 'aria-current="page">로그<' in h
    assert 'id="ppForm"' not in h
    h = c.get("/people").get_data(as_text=True)
    assert 'id="ppForm"' in h and 'href="/people?tab=log"' in h
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")
    h = c.get("/people?tab=log").get_data(as_text=True)
    assert 'id="lgForm"' not in h and 'href="/people?tab=log"' not in h               # 소유자 말고는 탭도 없음


def test_prune_180_days_and_size(tmp_path, monkeypatch):
    p = tmp_path / "log.jsonl"
    now = datetime(2026, 11, 7, 12, 0, 0)
    old = (now - timedelta(days=181)).strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(json.dumps({"time": old, "query": "옛"}) + "\n" + json.dumps({"time": "2026-11-01 00:00:00", "query": "새"}) + "\n",
                 encoding="utf-8")
    monkeypatch.setitem(people_log._pruned_day, "day", "")
    people_log.append(p, {"source": "MR", "query": "오늘", "result_count": 1, "allowed": True, "handPhone": "010"}, now)
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    assert [x["query"] for x in lines] == ["새", "오늘"] and "handPhone" not in lines[-1]   # 모르는 필드는 버림
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    monkeypatch.setattr(people_log, "MAX_BYTES", 300)
    monkeypatch.setattr(people_log, "TRIM_TO", 200)
    for i in range(10):
        people_log.append(p, {"source": "MR", "query": f"q{i}", "allowed": True}, now)
    assert p.stat().st_size <= 300 + 120 and "q9" in p.read_text(encoding="utf-8")
