# -*- coding: utf-8 -*-
"""체크 목록 기준 날짜 (2026-11-03): 00:00~11:59엔 어제 방송분, 12시부터 오늘, ‹ ›로 날짜 넘기기"""
from datetime import date, datetime

import pytest

import web_admin

KST = web_admin.APP_TZ


def at(h, m=0):
    return datetime(2026, 11, 3, h, m, tzinfo=KST)        # 화요일


def test_base_date_cutoff_and_override():
    f = web_admin.check_base_date
    assert f("", at(0, 5)) == date(2026, 11, 2) and f("", at(11, 59)) == date(2026, 11, 2)
    assert f("", at(12, 0)) == date(2026, 11, 3)
    assert f("2026-11-03", at(1)) == date(2026, 11, 3)                 # ›로 오늘 보기
    assert f("2026-11-04", at(13)) == date(2026, 11, 3)                # 미래는 안 됨 → 기본
    assert f("2026-10-01", at(13)) == date(2026, 11, 3)                # 너무 옛날도 기본
    assert f("bad", at(1)) == date(2026, 11, 2)
    assert web_admin.CHECK_DAY_CUTOFF_HOUR == 12


class FakeDT(datetime):
    fixed = None

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


@pytest.fixture
def page(monkeypatch):
    rules = [{"id": 1, "category": "예능", "pattern": "a", "subfolder": "월요예능", "days": ["월"], "updated_map": {}},
             {"id": 2, "category": "예능", "pattern": "b", "subfolder": "화요예능", "days": ["화"], "updated_map": {}},
             {"id": 3, "category": "예능", "pattern": "c", "subfolder": "토요예능", "days": ["토"], "updated_map": {}}]
    monkeypatch.setattr(web_admin, "load_cfg", lambda: {"rules": rules})
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "datetime", FakeDT)
    monkeypatch.setattr(web_admin, "current_weekday", lambda: FakeDT.fixed.weekday())
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client()


def test_early_morning_shows_yesterday(page):
    FakeDT.fixed = at(1, 30)
    html = page.get("/?mode=check").get_data(as_text=True)
    assert "11월 2일 월요일 (어제 방송분)" in html
    assert "월요예능" in html and "토요예능" in html and "화요예능" not in html     # 토~월(3일 전~어제)
    assert "3일 전부터 어제까지" in html and 'date=2026-11-03' in html             # › 로 오늘
    assert "어제(월) 체크 초기화" in html                                          # 초기화는 실제 어제 기준


def test_afternoon_and_next_button(page):
    FakeDT.fixed = at(14)
    html = page.get("/?mode=check").get_data(as_text=True)
    assert "11월 3일 화요일 ·" in html and "방송분)" not in html and "화요예능" in html
    assert "그저께부터 오늘까지" in html and "date=2026-11-04" not in html          # 오늘이면 › 없음
    FakeDT.fixed = at(1)
    html = page.get("/?mode=check&date=2026-11-03").get_data(as_text=True)
    assert "화요예능" in html and "date=2026-11-02" in html                         # ‹ 로 어제


def test_gulbang_uses_same_base(page, monkeypatch):
    import gulbang
    seen = {}
    monkeypatch.setattr(gulbang, "check_items", lambda items, today, cache_file: seen.update(today=today, items=items) or {})
    FakeDT.fixed = at(2)
    page.get("/api/gulbang")
    assert seen["today"] == date(2026, 11, 2) and {i["day"] for i in seen["items"]} == {"토", "월"}
