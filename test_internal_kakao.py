# -*- coding: utf-8 -*-
"""내부 카톡 보내기 API (2026-11-03): NAS 안에서만, 키 헤더, 허용 방만"""
import os

import pytest

import web_admin

LAN = {"REMOTE_ADDR": "172.17.0.5"}       # n8n 도커 브리지에서 온 것처럼


@pytest.fixture
def ik(monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "INTERNAL_KEY_PATH", tmp_path / "internal_api_key")
    calls = []
    monkeypatch.setattr(web_admin, "_kakao_send", lambda room, poster, text: calls.append((room, poster, text)) or {"ok": True})
    web_admin.app.config["TESTING"] = True
    c = web_admin.app.test_client()
    key = web_admin._internal_key()
    return c, calls, key, tmp_path


def post(c, key, body, host="192.168.50.153:5080", env=LAN, headers=None):
    h = {"Host": host, "X-Internal-Key": key}
    h.update(headers or {})
    return c.post("/api/internal/kakao-send", json=body, headers=h, environ_base=env)


def test_key_file_made_once_600(ik):
    c, calls, key, tmp_path = ik
    f = tmp_path / "internal_api_key"
    assert len(key) >= 40 and oct(os.stat(f).st_mode & 0o777) == "0o600"
    assert web_admin._internal_key() == key                                     # 다시 만들지 않음


def test_send_allowed_room(ik):
    c, calls, key, _ = ik
    r = post(c, key, {"room": "김창기", "text": "06시 날씨\n맑음"})
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert calls == [("김창기", "", "06시 날씨\n맑음")]


def test_rejects(ik, monkeypatch):
    c, calls, key, _ = ik
    assert post(c, "틀린키", {"room": "김창기", "text": "x"}).status_code == 403
    assert post(c, key, {"room": "김창기NAS 고객방", "text": "x"}).status_code == 400      # 허용 목록 밖
    assert post(c, key, {"room": "건이네가족", "text": ""}).status_code == 400
    # 공개 주소·터널·프록시로 온 건 아예 404
    assert post(c, key, {"room": "김창기", "text": "x"}, host="media.kckworld.com").status_code == 404
    assert post(c, key, {"room": "김창기", "text": "x"}, headers={"CF-Connecting-IP": "1.2.3.4"}).status_code == 404
    assert post(c, key, {"room": "김창기", "text": "x"}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 404
    assert post(c, key, {"room": "김창기", "text": "x"}, env={"REMOTE_ADDR": "8.8.8.8"}).status_code == 404
    assert post(c, key, {"room": "김창기", "text": "x"}, host="request.kckworld.com").status_code == 404
    assert calls == []


def test_failure_message_passed(ik, monkeypatch):
    c, calls, key, _ = ik
    monkeypatch.setattr(web_admin, "_kakao_send", lambda *a: {"ok": False, "message": "'건이네가족' 채팅방 창이 열려 있지 않음"})
    r = post(c, key, {"room": "건이네가족", "text": "x"})
    assert r.status_code == 502 and r.get_json() == {"ok": False, "message": "'건이네가족' 채팅방 창이 열려 있지 않음"}


def test_non_ascii_inputs_do_not_crash(ik, monkeypatch, tmp_path):
    """한글 키·코드를 넣어도 500이 아니라 거절(11-03, hmac.compare_digest는 한글 str을 못 비교)"""
    c, calls, key, _ = ik
    assert post(c, "한글키", {"room": "김창기", "text": "x"}).status_code == 403
    monkeypatch.setattr(web_admin, "MEDIA_REQUEST_SETTINGS", tmp_path / "rq.json")
    (tmp_path / "rq.json").write_text('{"code": "1234"}', encoding="utf-8")
    assert c.post("/request/submit", json={"code": "일이삼사", "name": "a"}).status_code == 403
    assert c.post("/survey/submit", json={"code": "일이삼사", "name": "a"}).status_code == 403
