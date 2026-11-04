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
    r = owner.post("/api/menu", json={"order": ["youtube", "downloads"], "hidden": ["movies", "people", "menu"]}).get_json()
    assert r["ok"]
    saved = json.loads((tmp / "menu.json").read_text(encoding="utf-8"))
    assert saved["hidden"] == ["movies"]                                     # 소유자 전용·메뉴 관리는 숨길 수 없음
    o = order(owner.get("/downloads").get_data(as_text=True))
    assert "/movies" not in o and o.index("/youtube") < o.index("/downloads") and "/people" in o and "/menu" in o
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
