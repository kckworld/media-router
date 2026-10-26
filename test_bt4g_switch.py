# -*- coding: utf-8 -*-
"""예능 키워드 화면의 BT4G 스위치: 플래그 파일 + qBittorrent '예능' 규칙의 BT4G 피드"""
import json

import pytest

import web_admin

TFREECA = "http://192.168.50.153/Python/bt4g/xml/tfreeca_rss.xml"


@pytest.fixture
def qbit(monkeypatch, tmp_path):
    rules = {"예능": {"enabled": True, "mustContain": "720p-NEXT", "affectedFeeds": [TFREECA]}}
    calls = []

    def fake_get(path, params=None):
        assert path == "/api/v2/rss/rules"
        return json.loads(json.dumps(rules))

    def fake_post(path, data):
        calls.append((path, dict(data)))
        if path == "/api/v2/rss/setRule":
            rules[data["ruleName"]] = json.loads(data["ruleDef"])

    monkeypatch.setattr(web_admin, "_qbit_get", fake_get)
    monkeypatch.setattr(web_admin, "_qbit_post", fake_post)
    monkeypatch.setattr(web_admin, "BT4G_ENABLED_PATH", tmp_path / "bt4g_enabled")
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    web_admin.app.config["TESTING"] = True
    return {"rules": rules, "calls": calls, "flag": tmp_path / "bt4g_enabled",
            "client": web_admin.app.test_client()}


def test_enable_marks_read_before_adding_feed(qbit):
    r = qbit["client"].post("/api/bt4g_enabled", json={"enabled": True})
    assert r.status_code == 200 and r.get_json()["enabled"] is True
    assert qbit["flag"].exists()
    assert qbit["rules"]["예능"]["affectedFeeds"] == [TFREECA, web_admin.QBIT_BT4G_FEED_URL]
    # 읽음 처리가 규칙 변경보다 먼저여야 쌓인 기사를 한꺼번에 받지 않는다
    assert [c[0] for c in qbit["calls"]] == ["/api/v2/rss/markAsRead", "/api/v2/rss/setRule"]
    assert qbit["rules"]["예능"]["mustContain"] == "720p-NEXT"


def test_disable_removes_feed_and_flag(qbit):
    qbit["client"].post("/api/bt4g_enabled", json={"enabled": True})
    r = qbit["client"].post("/api/bt4g_enabled", json={"enabled": False})
    assert r.get_json()["enabled"] is False and r.get_json()["in_rule"] is False
    assert not qbit["flag"].exists()
    assert qbit["rules"]["예능"]["affectedFeeds"] == [TFREECA]


def test_qbit_failure_keeps_flag_unchanged(qbit, monkeypatch):
    def boom(path, data):
        raise RuntimeError("연결 안 됨")
    monkeypatch.setattr(web_admin, "_qbit_post", boom)
    r = qbit["client"].post("/api/bt4g_enabled", json={"enabled": True})
    assert r.status_code == 502
    assert not qbit["flag"].exists()


def test_viewer_cannot_toggle(qbit, monkeypatch):
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    r = qbit["client"].post("/api/bt4g_enabled", json={"enabled": True})
    assert r.status_code == 403
    assert not qbit["flag"].exists()


def test_page_shows_switch_state(qbit):
    qbit["client"].post("/api/bt4g_enabled", json={"enabled": True})
    html = qbit["client"].get("/bt4g_keywords").get_data(as_text=True)
    assert 'id="bt4gToggle" checked' in html
    assert "어긋나 있어요" not in html
