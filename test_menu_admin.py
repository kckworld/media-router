# -*- coding: utf-8 -*-
"""계정 3단계 (2026-11-04): 메뉴 관리(보이기·순서·처음으로) + 계정별 권한 표"""
import json
import re

import pytest

import accounts
import web_admin


@pytest.fixture
def mm(monkeypatch, tmp_path):
    p = tmp_path / "accounts.json"
    monkeypatch.setattr(web_admin, "ACCOUNTS_PATH", p)
    monkeypatch.setattr(web_admin, "MENU_LAYOUT_PATH", tmp_path / "menu.json")
    monkeypatch.setattr(web_admin, "PASSWORD", "owner-pw-123")
    monkeypatch.setattr(web_admin, "VIEWER_PASSWORD", "")
    monkeypatch.setattr(web_admin, "_login_failures", {})
    monkeypatch.setattr(web_admin, "_login_global", {"fails": [], "locked_until": 0.0})
    accounts._cache.update(path=None, mtime=None, data=None)
    web_admin.app.config["TESTING"] = True
    owner = web_admin.app.test_client()
    owner.post("/login", data={"pw": "owner-pw-123"})
    accounts.add(p, "test", "시험", "test-pass-1")
    user = web_admin.app.test_client()
    user.post("/login", data={"uid": "test", "pw": "test-pass-1"})
    return owner, user, tmp_path


def order(html):
    return re.findall(r'href="([^"]+)"', re.search(r'<nav id="pageOrder".*?</nav>', html, re.S).group(0))


def test_page_and_table(mm):
    owner, user, tmp = mm
    html = owner.get("/menu").get_data(as_text=True)
    assert "메뉴 보이기" in html and "계정별 권한" in html and '"test"' in html
    assert user.get("/menu", headers={"Accept": "text/html"}).status_code == 302
    assert user.post("/api/menu", json={"reset": True}).status_code == 403


def test_hide_and_reorder_reflected_everywhere(mm):
    owner, user, tmp = mm
    r = owner.post("/api/menu", json={"order": ["youtube", "downloads"], "hidden": ["movies", "people", "menu", "accounts"]}).get_json()
    assert r["ok"]
    saved = json.loads((tmp / "menu.json").read_text(encoding="utf-8"))
    assert saved["hidden"] == ["movies", "people"]                           # 11-07: 소유자 전용(검색직)도 숨김 됨, 계정·메뉴 관리만 못 숨김
    o = order(owner.get("/downloads").get_data(as_text=True))
    assert "/movies" not in o and o.index("/youtube") < o.index("/downloads") and "/people" not in o and "/menu" in o
    assert owner.get("/people").status_code == 200                           # 숨겨도 주소로는 소유자가 들어감
    assert user.get("/people", headers={"Accept": "text/html"}).status_code in (302, 403)   # 권한은 그대로 소유자 전용
    u = order(user.get("/downloads").get_data(as_text=True))
    assert "/movies" not in u and u.index("/youtube") < u.index("/downloads")
    assert user.get("/movies").status_code == 200                             # 숨김은 메뉴만(권한은 그대로)
    owner.post("/api/menu", json={"reset": True})
    assert "/movies" in order(owner.get("/downloads").get_data(as_text=True))
    audit = (tmp / "accounts_audit.jsonl").read_text(encoding="utf-8")
    assert '"menu_layout"' in audit


def test_perms_saved_from_table_apply(mm):
    owner, user, tmp = mm
    assert owner.post("/api/accounts", json={"action": "perms", "id": "test",
                                             "perms": {"downloads": "none", "surveys": "edit"}}).get_json()["ok"]
    data = owner.get("/menu").get_data(as_text=True)
    assert '"surveys": "edit"' in data
    assert user.get("/downloads/data").status_code == 403
    assert "/downloads" not in order(user.get("/surveys").get_data(as_text=True))


def test_menu_admin_fixed_rows(mm):
    owner, user, tmp = mm
    data = web_admin._menu_admin_data()
    fixed = {p["key"] for g in data["groups"] for p in g["pages"] if p.get("fixed_show")}
    assert fixed == {"accounts", "menu"}                                      # 검색직 등 소유자 전용은 숨김 칸이 열림


def test_who_label_and_diary_link(mm):
    owner, user, tmp = mm
    h = owner.get("/downloads").get_data(as_text=True)
    assert 'href="/journal?tab=memo"' in h                                   # ✎ 일기 → 메모 탭
    assert 'class="who-chip"' in h and ">소유자<" in h
    u = user.get("/downloads").get_data(as_text=True)
    assert 'class="who-text">시험<' in u
