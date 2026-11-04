# -*- coding: utf-8 -*-
"""내부 카톡 보내기 API (2026-11-03): NAS 안에서만, 키 헤더, 허용 방만"""
import os

import pytest

import web_admin


def test_blocker_alert_once_a_day_with_photo(monkeypatch, tmp_path):
    """카톡에 인증 창이 떠 있으면 하루 한 번 텔레그램(캡처 사진, 없으면 글) (11-04)"""
    monkeypatch.setattr(web_admin, "KAKAO_BLOCKER_STATE", tmp_path / "state.json")
    monkeypatch.setattr(web_admin, "KAKAO_BLOCKER_PNG", tmp_path / "shot.png")
    (tmp_path / "shot.png").write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(web_admin, "_tg_cfg", lambda: {"enabled": True, "bot_token": "t", "chat_id": "1"})
    photos, texts = [], []

    class R:
        def json(self):
            return {"ok": True}
    monkeypatch.setattr(web_admin.requests, "post", lambda url, data=None, files=None, timeout=None: photos.append(data["caption"]) or R())
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, *a, **k: texts.append(text) or True)
    res = {"ok": False, "blocker": "카카오계정 인증", "shot": "/x.png"}
    assert web_admin._kakao_blocker_alert(res) is True
    assert web_admin._kakao_blocker_alert(res) is False                       # 같은 날 두 번째는 안 보냄
    assert len(photos) == 1 and "'카카오계정 인증' 창이 떠 있어요" in photos[0] and not texts
    (tmp_path / "state.json").unlink()
    assert web_admin._kakao_blocker_alert({"ok": False, "blocker": "로그인", "shot": ""})   # 캡처 없으면 글로
    assert texts and "'로그인'" in texts[0]

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


def test_prepare_at_0530_once_and_alerts_on_failure(monkeypatch):
    """05:30에 하루 한 번 '건이네가족' 창 미리 열기, 실패면 텔레그램(11-04)"""
    from datetime import datetime
    runs, sent = [], []
    monkeypatch.setitem(web_admin._kakao_prepare_state, "day", "")
    monkeypatch.setattr(web_admin, "_tg_safe", lambda fn, *a: fn(*a))
    monkeypatch.setattr(web_admin.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda s: target(*args)})())
    monkeypatch.setattr(web_admin, "_kakao_prepare_run", lambda room: runs.append(room))
    t = lambda h, m: datetime(2026, 11, 4, h, m, tzinfo=web_admin.APP_TZ)
    web_admin._kakao_prepare_due(t(5, 29))
    web_admin._kakao_prepare_due(t(5, 30))
    web_admin._kakao_prepare_due(t(5, 31))
    assert runs == ["건이네가족"]                                               # 하루 한 번
    web_admin._kakao_prepare_due(t(6, 10))
    assert runs == ["건이네가족"]
    monkeypatch.undo()
    monkeypatch.setattr(web_admin, "RUN_BATCH_SSH_KEY", "/없는/키")
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, *a, **k: sent.append(text) or True)
    res = web_admin._kakao_prepare_run("건이네가족")
    assert not res["ok"] and sent and "미리 열지 못했어요" in sent[0]


def test_non_ascii_inputs_do_not_crash(ik, monkeypatch, tmp_path):
    """한글 키·코드를 넣어도 500이 아니라 거절(11-03, hmac.compare_digest는 한글 str을 못 비교)"""
    c, calls, key, _ = ik
    assert post(c, "한글키", {"room": "김창기", "text": "x"}).status_code == 403
    monkeypatch.setattr(web_admin, "MEDIA_REQUEST_SETTINGS", tmp_path / "rq.json")
    (tmp_path / "rq.json").write_text('{"code": "1234"}', encoding="utf-8")
    assert c.post("/request/submit", json={"code": "일이삼사", "name": "a"}).status_code == 403
    assert c.post("/survey/submit", json={"code": "일이삼사", "name": "a"}).status_code == 403
