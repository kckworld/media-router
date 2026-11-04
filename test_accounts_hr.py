# -*- coding: utf-8 -*-
"""인사팀 점검 반영 (2026-11-04): LAN은 전체 잠금 예외·[잠금 풀기] 버튼, 모든 기기 로그아웃, 삭제 세대 번호, 로그인 기록"""
import json
import os

import pytest

import accounts
import web_admin

NGINX = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def hr(monkeypatch, tmp_path):
    p = tmp_path / "accounts.json"
    monkeypatch.setattr(web_admin, "ACCOUNTS_PATH", p)
    monkeypatch.setattr(web_admin, "MENU_LAYOUT_PATH", tmp_path / "menu.json")
    monkeypatch.setattr(web_admin, "PASSWORD", "owner-pw-123")
    monkeypatch.setattr(web_admin, "VIEWER_PASSWORD", "")
    monkeypatch.setattr(web_admin, "_login_failures", {})
    monkeypatch.setattr(web_admin, "_login_global", {"fails": [], "locked_until": 0.0})
    monkeypatch.setattr(web_admin, "_login_unlock_tokens", {})
    sent = []
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, markup=None, *a, **k: sent.append((text, markup)) or True)
    monkeypatch.setattr(web_admin.threading, "Thread", lambda target, daemon=None: type("T", (), {"start": lambda s: target()})())
    accounts._cache.update(path=None, mtime=None, data=None)
    web_admin.app.config["TESTING"] = True
    return p, sent


def login(c, pw, real="203.0.113.5", uid="", ua="Mozilla/5.0 (iPhone) Safari/605"):
    return c.post("/login", data={"pw": pw, "uid": uid}, headers={"X-Real-IP": real, "User-Agent": ua}, environ_base=NGINX)


def ok(r):
    return r.headers["Location"].endswith("/")


def test_global_lock_spares_lan_and_unlock_button(hr):
    p, sent = hr
    c = web_admin.app.test_client()
    for i in range(21):
        login(c, "wrong", real=f"203.0.113.{i + 10}")
    assert "error=2" in login(c, "owner-pw-123", real="198.51.100.9").headers["Location"]    # 바깥은 막힘
    assert ok(login(web_admin.app.test_client(), "owner-pw-123", real="192.168.50.20"))       # 집 LAN은 들어감
    text, markup = sent[0]
    assert "잠금 풀기" in markup["inline_keyboard"][0][0]["text"]
    data = markup["inline_keyboard"][0][0]["callback_data"]
    action, _, ts = data.partition(":")
    assert web_admin._login_unlock(action[3:]) is True
    assert web_admin._login_unlock(action[3:]) is False                       # 한 번만
    assert ok(login(web_admin.app.test_client(), "owner-pw-123", real="198.51.100.9"))       # 풀린 뒤 바깥도 됨


def test_unlock_button_via_callback_handler(hr, monkeypatch):
    p, sent = hr
    monkeypatch.setattr(web_admin, "_tg_call", lambda *a, **k: {"ok": True})
    web_admin._login_global["locked_until"] = 9e18
    web_admin._login_unlock_tokens["abc123"] = 9e18
    import time
    web_admin._handle_telegram_callback({"id": "q", "data": f"lu_abc123:{int(time.time())}",
                                         "message": {"chat": {"id": 1}, "message_id": 77}})
    assert web_admin._login_global["locked_until"] == 0.0 and sent[-1][0].startswith("🔓")
    web_admin._login_global["locked_until"] = 9e18
    web_admin._login_unlock_tokens["old"] = 9e18
    web_admin._handle_telegram_callback({"id": "q", "data": f"lu_old:{int(time.time()) - 601}",       # 10분 지남
                                         "message": {"chat": {"id": 1}, "message_id": 78}})
    assert web_admin._login_global["locked_until"] == 9e18


def test_logout_all_devices_including_owner(hr):
    p, sent = hr
    owner, other_owner, user = (web_admin.app.test_client() for _ in range(3))
    assert ok(login(owner, "owner-pw-123", real="192.168.50.20"))
    assert ok(login(other_owner, "owner-pw-123", real="192.168.50.21"))
    accounts.add(p, "wife", "아내", "wife-pass-1")
    assert ok(login(user, "wife-pass-1", uid="wife", real="192.168.50.22"))
    r = owner.post("/api/accounts", json={"action": "logout_all"}).get_json()
    assert r["ok"] and r["relogin"]
    for c in (owner, other_owner, user):
        assert c.get("/downloads").status_code == 302                       # 모두 끊김
    assert ok(login(owner, "owner-pw-123", real="192.168.50.20")) and owner.get("/downloads").status_code == 200


def test_recreated_same_id_does_not_revive_old_session(hr):
    p, sent = hr
    owner, user = web_admin.app.test_client(), web_admin.app.test_client()
    login(owner, "owner-pw-123", real="192.168.50.20")
    accounts.add(p, "kid", "아이", "kid-pass-11")
    assert ok(login(user, "kid-pass-11", uid="kid", real="192.168.50.23"))
    owner.post("/api/accounts", json={"action": "delete", "id": "kid"})
    owner.post("/api/accounts", json={"action": "add", "id": "kid", "name": "아이", "password": "kid-pass-22"})
    assert user.get("/downloads").status_code == 302                          # 옛 세션은 계속 무효
    assert ok(login(user, "kid-pass-22", uid="kid", real="192.168.50.23"))


def test_login_log_recent_200_masked(hr):
    p, sent = hr
    c = web_admin.app.test_client()
    login(c, "wrong", uid="nobody", real="198.51.100.77")
    login(c, "owner-pw-123", real="192.168.50.20", ua="Mozilla/5.0 (Windows NT 10.0) Chrome/120 Safari/537")
    log = p.parent / "login_log.json"
    items = json.loads(log.read_text(encoding="utf-8"))
    assert oct(os.stat(log).st_mode & 0o777) == "0o600"
    assert items[0]["ok"] is False and items[0]["user"] == "nobody" and items[0]["ip"] == "…77" and "198.51" not in log.read_text()
    assert items[1]["ok"] is True and items[1]["user"] == "owner" and items[1]["ua"] == "Windows · Chrome"
    assert "wrong" not in log.read_text() and "owner-pw" not in log.read_text()                 # 비밀번호는 안 남김
    html = c.get("/accounts").get_data(as_text=True)
    assert "로그인 기록" in html and "…77" in html and "모든 기기 로그아웃" in html
    for i in range(210):
        accounts.log_login(p, False, "x", "1.2.3.4", "")
    assert len(json.loads(log.read_text(encoding="utf-8"))) == 200
