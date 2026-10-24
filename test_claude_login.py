"""설정 > Claude 로그인 라우트 (/settings/claude_login) 테스트."""
import json
import subprocess

import pytest

import web_admin


@pytest.fixture
def client(monkeypatch, tmp_path):
    key = tmp_path / "claude_login_key"
    key.write_text("dummy")
    monkeypatch.setattr(web_admin, "CLAUDE_LOGIN_SSH_KEY", str(key))
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd[-1])
        out = {"status": {"ok": True, "auth": {"loggedIn": True}, "pending": False, "url": ""},
               "start": {"ok": True, "url": "https://claude.com/cai/oauth/authorize?x=1"}}.get(cmd[-1].split()[0],
                                                                                               {"ok": True})
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out) + "\n", stderr="")

    monkeypatch.setattr(web_admin.subprocess, "run", fake_run)
    web_admin.app.config["TESTING"] = True
    c = web_admin.app.test_client()
    c.calls = calls
    return c


def test_status(client):
    r = client.get("/settings/claude_login")
    assert r.status_code == 200 and r.get_json()["auth"]["loggedIn"] is True
    assert client.calls == ["status"]


def test_start_returns_url(client):
    r = client.post("/settings/claude_login", json={"action": "start"})
    assert r.get_json()["url"].startswith("https://claude.com/")
    assert client.calls == ["start"]


def test_code_is_passed(client):
    r = client.post("/settings/claude_login", json={"action": "code", "code": " abcDEF123_-#state456 "})
    assert r.status_code == 200
    assert client.calls == ["code abcDEF123_-#state456"]


@pytest.mark.parametrize("code", ["", "short", "abc def 1234567", "abc;rm -rf /;x", "a" * 600])
def test_bad_code_rejected_before_ssh(client, code):
    r = client.post("/settings/claude_login", json={"action": "code", "code": code})
    assert r.status_code == 400
    assert client.calls == []


def test_unknown_action(client):
    assert client.post("/settings/claude_login", json={"action": "logout"}).status_code == 400
    assert client.calls == []


def test_missing_key(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "CLAUDE_LOGIN_SSH_KEY", str(tmp_path / "none"))
    d = client.get("/settings/claude_login").get_json()
    assert d["ok"] is False and "claude_login_key" in d["message"]


def test_viewer_blocked(client, monkeypatch):
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert client.post("/settings/claude_login", json={"action": "start"}).status_code == 403
    assert client.get("/settings/claude_login").status_code == 403
    assert client.calls == []
