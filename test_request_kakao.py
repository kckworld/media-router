# -*- coding: utf-8 -*-
"""요청 완료 안내 (2026-11-01 카톡, 11-10부터 알림 허브 'mr.request_done'으로) - 채널·실패 알림"""
import time

import notify_hub as nh
import web_admin
from test_media_requests import GOOD, rq, saved  # noqa: F401  (rq는 fixture)


def _done(c, sent, tmp_path, monkeypatch):
    c.post("/request/submit", json=GOOD)
    sent.clear()                              # 새 요청 알림(사진)은 빼고 완료 안내만 본다
    rid = saved(tmp_path)[0]["id"]
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    c.post(f"/api/requests/{rid}", json={"status": "done"})
    for _ in range(600):                      # 웹 버튼은 안내를 백그라운드 스레드로 보낸다(NAS가 바쁘면 느려서 30초까지)
        if saved(tmp_path)[0].get("announced"):
            break
        time.sleep(0.05)


def _set(tmp_path, **kw):
    d = web_admin._hub()
    assert nh.update_item(d, "mr.request_done", kw) is None
    nh.save(web_admin.NOTIFY_HUB_PATH, d)


def test_telegram_only_by_default(rq, monkeypatch):
    c, sent, tmp_path = rq
    calls = []
    monkeypatch.setattr(web_admin, "_kakao_send", lambda *a: calls.append(a) or {"ok": True})
    _done(c, sent, tmp_path, monkeypatch)
    assert not calls and [m for m, _ in sent].count("sendPhoto") == 1
    assert nh.query_log(web_admin.NOTIFY_LOG_PATH, key="mr.request_done")[0]["status"] == "ok"


def test_both_sends_same_poster_and_caption(rq, monkeypatch):
    c, sent, tmp_path = rq
    calls = []
    monkeypatch.setattr(web_admin, "_kakao_send", lambda *a: calls.append(a) or {"ok": True, "image": True})
    _set(tmp_path, channel="both", kakao_room="김창기")
    _done(c, sent, tmp_path, monkeypatch)
    photo = [p for m, p in sent if m == "sendPhoto"][0]
    assert calls == [("김창기", photo["photo"], photo["caption"])] and photo["photo"].endswith("/w500/a.jpg")
    assert not [p for m, p in sent if m == "text" and "카톡" in p["text"]]               # 성공이면 경고 없음


def test_kakao_failure_alerts_on_telegram(rq, monkeypatch):
    c, sent, tmp_path = rq
    monkeypatch.setattr(web_admin, "_kakao_send", lambda *a: {"ok": False, "message": "카톡이 실행 중이 아님"})
    _set(tmp_path, channel="both", kakao_room="김창기")
    _done(c, sent, tmp_path, monkeypatch)
    assert [m for m, _ in sent].count("sendPhoto") == 1                                 # 텔레그램 안내는 그대로
    warn = [p["text"] for m, p in sent if m == "text" and "카톡" in p["text"]]
    assert len(warn) == 1 and "못 보냈어요" in warn[0] and "카톡이 실행 중이 아님" in warn[0]
    assert saved(tmp_path)[0]["announced"]


def test_turned_off_sends_nothing(rq, monkeypatch):
    c, sent, tmp_path = rq
    _set(tmp_path, enabled=False)
    _done(c, sent, tmp_path, monkeypatch)
    assert not [m for m, _ in sent if m == "sendPhoto"]
    assert saved(tmp_path)[0]["announced"]                                               # 꺼 둔 건 다시 시도 안 함
    assert nh.query_log(web_admin.NOTIFY_LOG_PATH, key="mr.request_done")[0]["status"] == "off"


def test_request_page_points_to_notify(rq, monkeypatch):
    c, sent, tmp_path = rq
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    html = c.get("/requests").get_data(as_text=True)
    assert 'href="/notify"' in html and 'id="kkRoom"' not in html
    assert c.post("/api/requests/kakao", json={"on": True, "room": "x"}).status_code != 200      # 옛 설정 주소는 없어짐
