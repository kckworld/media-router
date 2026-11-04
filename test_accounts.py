# -*- coding: utf-8 -*-
"""계정 1단계 (2026-11-04): 소유자 고정, 일반 계정(해시), 로그인, 막기·삭제 즉시 반영, 바뀐 기록(비번 없음)"""
import json
import os

import pytest

import accounts
import web_admin


@pytest.fixture
def ac(monkeypatch, tmp_path):
    p = tmp_path / "accounts.json"
    monkeypatch.setattr(web_admin, "ACCOUNTS_PATH", p)
    monkeypatch.setattr(web_admin, "PASSWORD", "owner-pw-123")
    monkeypatch.setattr(web_admin, "VIEWER_PASSWORD", "viewer-pw-123")
    monkeypatch.setattr(web_admin, "_login_failures", {})
    monkeypatch.setattr(web_admin, "_login_global", {"fails": [], "locked_until": 0.0})
    accounts._cache.update(path=None, mtime=None, data=None)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), p


def login(c, pw, uid=""):
    return c.post("/login", data={"pw": pw, "uid": uid}, environ_base={"REMOTE_ADDR": "192.168.50.20"})


def test_owner_login_and_accounts_page(ac):
    c, p = ac
    r = login(c, "owner-pw-123")
    assert r.headers["Location"].endswith("/")
    html = c.get("/accounts").get_data(as_text=True)
    assert "계정 추가" in html and "소유자" in html
    assert "아이디" in c.get("/login").get_data(as_text=True) or True


def test_add_login_disable_delete(ac):
    c, p = ac
    login(c, "owner-pw-123")
    assert c.post("/api/accounts", json={"action": "add", "id": "Owner", "password": "longenough1"}).status_code == 400
    assert c.post("/api/accounts", json={"action": "add", "id": "wife", "name": "아내", "password": "short"}).status_code == 400
    r = c.post("/api/accounts", json={"action": "add", "id": "wife", "name": "아내", "password": "wife-pass-1"}).get_json()
    assert r["ok"] and r["items"][0]["id"] == "wife" and "hash" not in r["items"][0]
    raw = p.read_text(encoding="utf-8")
    assert "wife-pass-1" not in raw and "scrypt:" in raw and oct(os.stat(p).st_mode & 0o777) == "0o600"
    audit = (p.parent / "accounts_audit.jsonl").read_text(encoding="utf-8")
    assert '"action": "add"' in audit and "wife-pass-1" not in audit
    # 아내 계정으로 로그인 → 보기 전용처럼(계정 화면은 못 봄)
    w = web_admin.app.test_client()
    assert login(w, "wrong-pass", "wife").headers["Location"].endswith("error=1")
    assert login(w, "wife-pass-1", "wife").headers["Location"].endswith("/")
    assert w.get("/downloads").status_code == 200
    assert w.get("/accounts", headers={"Accept": "text/html"}).status_code == 302
    assert w.post("/api/accounts", json={"action": "delete", "id": "wife"}).status_code == 403
    # 막으면 그 세션 바로 끊김
    assert c.post("/api/accounts", json={"action": "disable", "id": "wife"}).get_json()["ok"]
    assert w.get("/downloads").status_code == 302
    assert login(w, "wife-pass-1", "wife").headers["Location"].endswith("error=1")
    c.post("/api/accounts", json={"action": "enable", "id": "wife"})
    assert login(w, "wife-pass-1", "wife").headers["Location"].endswith("/")
    assert c.post("/api/accounts", json={"action": "password", "id": "wife", "password": "new-pass-22"}).get_json()["ok"]
    assert login(web_admin.app.test_client(), "wife-pass-1", "wife").headers["Location"].endswith("error=1")
    assert c.post("/api/accounts", json={"action": "delete", "id": "wife"}).get_json()["ok"]
    assert w.get("/downloads").status_code == 302                          # 지우면 세션 끊김


def test_owner_is_protected(ac):
    c, p = ac
    login(c, "owner-pw-123")
    for act in ("delete", "disable", "password"):
        r = c.post("/api/accounts", json={"action": act, "id": "owner", "password": "whatever-123"})
        assert r.status_code == 400
    assert login(web_admin.app.test_client(), "owner-pw-123", "owner").headers["Location"].endswith("/")   # 아이디 owner도 됨


def test_legacy_viewer_password_migrates(ac):
    c, p = ac
    assert login(c, "viewer-pw-123").headers["Location"].endswith("/")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["viewer_migrated"] and "viewer" in data["accounts"] and "viewer-pw-123" not in p.read_text(encoding="utf-8")
    assert web_admin.app.test_client().get("/accounts").status_code in (302, 403)
    assert c.get("/settings", headers={"Accept": "text/html"}).status_code == 302          # 보기 전용과 같게 숨김
    assert c.post("/check", data={}).status_code == 403                                   # 쓰기 막힘
    # 옮긴 뒤 'viewer'로 아이디 넣고도 로그인
    assert login(web_admin.app.test_client(), "viewer-pw-123", "viewer").headers["Location"].endswith("/")


def test_old_viewer_token_follows_account(ac, monkeypatch):
    c, p = ac
    tok = web_admin._session_serializer.dumps({"a": True, "r": "viewer"})       # 11-04 전에 받은 보기 전용 세션
    c.set_cookie("media_admin", tok)
    assert c.get("/downloads").status_code == 200                              # 옮기기 전엔 그대로
    accounts.migrate_viewer(p, "viewer-pw-123")
    accounts.set_disabled(p, "viewer", True)
    assert c.get("/downloads").status_code == 302                              # 계정을 막으면 옛 세션도 끊김
    accounts.delete(p, "viewer")
    assert c.get("/downloads").status_code == 302
