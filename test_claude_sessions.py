"""설정 > Claude 세션 라우트 (/settings/claude_sessions) 테스트."""
import base64
import json
import subprocess

import pytest

import web_admin

SID = "52ea0a0d-d75c-483e-922f-ffdc53806c16"


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
        out = {"sessions": {"ok": True, "sessions": [
                   {"sessionId": SID, "name": "옛 세션", "running": False, "lastActive": 1}]},
               "resume": {"ok": True, "message": "다시 열었어요."},
               "new": {"ok": True, "message": "새 세션을 띄웠어요."}}[cmd[-1].split()[0]]
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out) + "\n", stderr="")

    monkeypatch.setattr(web_admin.subprocess, "run", fake_run)
    web_admin.app.config["TESTING"] = True
    c = web_admin.app.test_client()
    c.calls = calls
    return c


def test_list(client):
    r = client.get("/settings/claude_sessions")
    assert r.status_code == 200 and r.get_json()["sessions"][0]["sessionId"] == SID
    assert client.calls == ["sessions"]


def test_resume_is_passed(client):
    r = client.post("/settings/claude_sessions", json={"action": "resume", "session_id": f" {SID} "})
    assert r.status_code == 200
    assert client.calls == [f"resume {SID}"]


@pytest.mark.parametrize("sid", ["", "52ea0a0d", SID.upper(), SID + ";rm -rf /", "../" + SID])
def test_bad_session_id_rejected_before_ssh(client, sid):
    r = client.post("/settings/claude_sessions", json={"action": "resume", "session_id": sid})
    assert r.status_code == 400
    assert client.calls == []


def test_new_name_is_base64(client):
    r = client.post("/settings/claude_sessions", json={"action": "new", "name": "  media-router  작업; rm -rf / "})
    assert r.status_code == 200
    verb, arg = client.calls[0].split(" ")
    assert verb == "new"
    assert base64.b64decode(arg).decode() == "media-router 작업; rm -rf /"


def test_new_without_name_uses_date(client):
    client.post("/settings/claude_sessions", json={"action": "new", "name": ""})
    assert base64.b64decode(client.calls[0].split(" ")[1]).decode().startswith("새 세션 ")


def test_new_name_too_long(client):
    r = client.post("/settings/claude_sessions", json={"action": "new", "name": "가" * 41})
    assert r.status_code == 400
    assert client.calls == []


def test_unknown_action(client):
    assert client.post("/settings/claude_sessions", json={"action": "stop"}).status_code == 400
    assert client.calls == []


def test_viewer_blocked(client, monkeypatch):
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert client.post("/settings/claude_sessions", json={"action": "new", "name": "x"}).status_code == 403
    assert client.get("/settings/claude_sessions").status_code == 403
    assert client.calls == []
