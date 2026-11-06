# -*- coding: utf-8 -*-
"""요청 완료 안내를 카톡 방에도 (2026-11-01) - MR 쪽 설정·전송·실패 대체"""
import json
import time

import web_admin
from test_media_requests import GOOD, rq, saved  # noqa: F401  (rq는 fixture)


def _done(c, sent, tmp_path, monkeypatch):
    c.post("/request/submit", json=GOOD)
    sent.clear()                              # 새 요청 알림(사진)은 빼고 완료 안내만 본다
    rid = saved(tmp_path)[0]["id"]
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    c.post(f"/api/requests/{rid}", json={"status": "done"})
    for _ in range(100):                      # 웹 버튼은 안내를 백그라운드 스레드로 보낸다
        if saved(tmp_path)[0].get("announced"):
            break
        time.sleep(0.05)


def test_kakao_off_by_default(rq, monkeypatch):
    c, sent, tmp_path = rq
    calls = []
    monkeypatch.setattr(web_admin, "_kakao_send", lambda *a: calls.append(a) or {"ok": True})
    _done(c, sent, tmp_path, monkeypatch)
    assert not calls and [m for m, _ in sent].count("sendPhoto") == 1


def test_kakao_on_sends_same_poster_and_caption(rq, monkeypatch):
    c, sent, tmp_path = rq
    calls = []
    monkeypatch.setattr(web_admin, "_kakao_send", lambda *a: calls.append(a) or {"ok": True, "image": True})
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    assert c.post("/api/requests/kakao", json={"on": True, "room": ""}).status_code == 400   # 켜려면 방 이름
    assert c.post("/api/requests/kakao", json={"on": True, "room": " 김창기 "}).get_json()["ok"]
    st = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert st == {"code": "1234", "kakao_on": True, "kakao_room": "김창기"}            # 코드는 그대로
    r = c.post("/api/requests/code", json={"code": "1234"})                          # 11-07: 새로 저장은 6자 이상
    assert r.status_code == 400 and "6자 이상" in r.get_json()["error"]
    assert web_admin._request_code() == "1234"                                        # 이미 있던 4자리는 그대로 동작
    assert web_admin._request_settings()["kakao_room"] == "김창기"                      # 코드 저장이 카톡 설정을 안 지움
    _done(c, sent, tmp_path, monkeypatch)
    photo = [p for m, p in sent if m == "sendPhoto"][0]
    assert calls == [("김창기", photo["photo"], photo["caption"])] and photo["photo"].endswith("/w500/a.jpg")
    assert not [p for m, p in sent if m == "text" and "카톡" in p["text"]]               # 성공이면 경고 없음


def test_kakao_failure_falls_back_to_telegram_alert(rq, monkeypatch):
    c, sent, tmp_path = rq
    (tmp_path / "settings.json").write_text(
        json.dumps({"code": "1234", "kakao_on": True, "kakao_room": "김창기"}), encoding="utf-8")
    monkeypatch.setattr(web_admin, "_kakao_send", lambda *a: {"ok": False, "message": "카톡이 실행 중이 아님"})
    _done(c, sent, tmp_path, monkeypatch)
    assert [m for m, _ in sent].count("sendPhoto") == 1                                 # 텔레그램 안내는 그대로
    warn = [p["text"] for m, p in sent if m == "text" and "카톡" in p["text"]]
    assert len(warn) == 1 and "못 보냈어요" in warn[0] and "카톡이 실행 중이 아님" in warn[0]
    assert saved(tmp_path)[0]["announced"]                                               # 텔레그램으로 갔으니 한 번만


def test_kakao_test_button_and_page(rq, monkeypatch):
    c, sent, tmp_path = rq
    calls = []
    monkeypatch.setattr(web_admin, "_kakao_send",
                        lambda *a: calls.append(a) or {"ok": False, "message": "창이 열려 있지 않음"})
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    r = c.post("/api/requests/kakao", json={"test": True, "room": "김창기"}).get_json()
    assert r == {"ok": False, "error": "창이 열려 있지 않음"} and calls[0][:2] == ("김창기", "")
    assert "kakao_room" not in web_admin._request_settings()                            # 시험은 설정을 안 바꿈
    html = c.get("/requests").get_data(as_text=True)
    assert 'id="kkRoom"' in html and "꺼짐 (텔레그램만)" in html
