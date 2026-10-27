# -*- coding: utf-8 -*-
"""예능 일괄 재인코딩 연결: 상태·일시정지·비우기·새벽 자동 인코딩·아침 요약"""
import json
import time
from datetime import datetime

import pytest

import reencode_ctl
import web_admin


@pytest.fixture
def re_env(monkeypatch, tmp_path):
    base = tmp_path / "reencode"
    base.mkdir()
    (base / "queue.json").write_text(json.dumps({"items": [
        {"id": "0001", "src": "/v/유희열의 스케치북.E509.mp4", "status": "done", "saved": 6 * 1024 ** 3, "expect_save": 6 * 1024 ** 3},
        {"id": "0002", "src": "/v/BTS.mkv", "status": "fed", "expect_save": 4 * 1024 ** 3},
        {"id": "0003", "src": "/v/무한도전.avi", "status": "failed", "msg": "원본 없음(넣기 전)", "expect_save": 0},
        {"id": "0004", "src": "/v/런닝맨.mp4", "status": "pending", "expect_save": 1024 ** 3}]}), encoding="utf-8")
    (base / "reencode.log").write_text("[x] #0001 넣음\n[y] 넣기 끝\n", encoding="utf-8")
    monkeypatch.setattr(reencode_ctl, "QUEUE", base / "queue.json")
    monkeypatch.setattr(reencode_ctl, "STOP", base / "STOP")
    monkeypatch.setattr(reencode_ctl, "LOG", base / "reencode.log")
    enc = tmp_path / "인코딩"
    enc.mkdir()
    monkeypatch.setattr(web_admin, "ENCODING_HOLD_PATH", enc)
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    sent, popen = [], []
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, markup=None, token=None: sent.append((text, markup)) or True)
    monkeypatch.setattr(web_admin, "_tg_call", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(web_admin.subprocess, "Popen", lambda args, **kw: popen.append(args))
    key = tmp_path / "run_batch_key"
    key.write_text("k")
    monkeypatch.setattr(web_admin, "RUN_BATCH_SSH_KEY", str(key))
    web_admin.app.config["TESTING"] = True
    return {"base": base, "enc": enc, "sent": sent, "popen": popen, "client": web_admin.app.test_client()}


def test_state_summary(re_env):
    st = reencode_ctl.state()
    assert st["summary"] == ("완료 1/4 · 넣어 둠 1 · 대기 1 · 실패 1\n줄인 용량 6.0GB / 예상 11.0GB"
                             "\nDrive 휴지통에 쌓인 재인코딩 파일 약 0.0GB")
    assert st["failed"] == [{"id": "0003", "name": "무한도전.avi", "error": "원본 없음(넣기 전)"}]


def _set_trash(re_env, n, since="2026-10-27 16:49"):
    q = json.loads((re_env["base"] / "queue.json").read_text(encoding="utf-8"))
    q.update(trash_bytes=n, trash_emptied_at=since)
    (re_env["base"] / "queue.json").write_text(json.dumps(q), encoding="utf-8")


def test_trash_warning_and_button(re_env):
    _set_trash(re_env, 50 * 2 ** 30)
    st = reencode_ctl.state()
    assert "약 50.0GB (2026-10-27 16:49 비운 뒤)" in st["summary"] and not st["trash_warn"]
    assert len(web_admin._reencode_buttons()["inline_keyboard"]) == 1          # 100GB 아래면 버튼 없음
    _set_trash(re_env, 120 * 2 ** 30)
    assert "휴지통을 비워 주세요" in reencode_ctl.state()["summary"]
    kb = web_admin._reencode_buttons()["inline_keyboard"]
    assert kb[1][0]["text"] == "🧹 휴지통 비웠음" and kb[1][0]["callback_data"].startswith("re_trash:")
    # 누르면 호스트로 (볼륨 여유 공간을 재야 해서)
    now = int(time.time())
    web_admin._handle_telegram_callback({"id": "q", "data": f"re_trash:{now}", "message": {"chat": {"id": 1}, "message_id": 7790}})
    assert re_env["popen"][-1][-1] == "reencode-trash-emptied"
    assert re_env["client"].post("/api/reencode/trash-emptied").get_json()["ok"]
    assert re_env["client"].get("/api/reencode").get_json()["trash_bytes"] == 120 * 2 ** 30


def test_api_and_pause(re_env):
    c = re_env["client"]
    d = c.get("/api/reencode").get_json()
    assert d["available"] and "넣기 끝" in d["log"] and d["paused"] is False
    assert c.post("/api/reencode/pause", json={"paused": True}).get_json()["paused"] is True
    assert (re_env["base"] / "STOP").exists() and "일시정지 중" in reencode_ctl.state()["summary"]
    c.post("/api/reencode/pause", json={"paused": False})
    assert not (re_env["base"] / "STOP").exists()


def test_clear_goes_to_host_via_ssh(re_env, monkeypatch):
    r = re_env["client"].post("/api/reencode/clear").get_json()
    assert r["ok"] and re_env["popen"][-1][-1] == "reencode-clear"
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert re_env["client"].post("/api/reencode/clear").status_code == 403


def test_telegram_buttons(re_env):
    now = int(time.time())
    # 메시지 번호는 다른 테스트와 겹치지 않게 (같은 메시지의 버튼은 한 번만 처리됨)
    cb = lambda a, mid: {"id": "q", "data": f"{a}:{now - 3600 * 5}", "message": {"chat": {"id": 1}, "message_id": 7700 + mid}}
    web_admin._handle_telegram_callback(cb("re_stop", 1))
    assert (re_env["base"] / "STOP").exists()
    assert re_env["sent"][-1][1]["inline_keyboard"][0][0]["callback_data"].startswith("re_resume:")
    web_admin._handle_telegram_callback(cb("re_resume", 2))
    assert not (re_env["base"] / "STOP").exists()
    web_admin._handle_telegram_callback(cb("re_status", 3))
    assert re_env["sent"][-1][0].startswith("♻️ 예능 재인코딩\n완료 1/4")
    web_admin._handle_telegram_callback(cb("re_clear", 4))
    assert re_env["popen"][-1][-1] == "reencode-clear"


def test_night_encode_asks_when_pc_on(re_env, monkeypatch):
    (re_env["enc"] / "[재인코딩] 0002 BTS.mkv").write_text("v")
    (re_env["enc"] / "[재인코딩] 0004 런닝맨.mp4.part").write_text("v")
    started = []
    monkeypatch.setattr(web_admin, "_ensure_pc_on_and_trigger_encode",
                        lambda progress=None, sync_timeout=None: started.append(sync_timeout) or (True, "인코딩 시작"))
    monkeypatch.setattr(web_admin, "_is_pc_online", lambda host: True)
    web_admin._run_reencode_encode()
    text, markup = re_env["sent"][-1]
    assert "1개" in text and "PC가 켜져 있어요" in text and not started
    assert markup["inline_keyboard"][0][0]["callback_data"].startswith("re_start:")
    # [지금 시작] → 2시간 동기화 대기로 시작
    web_admin._handle_telegram_callback({"id": "q", "data": markup["inline_keyboard"][0][0]["callback_data"],
                                         "message": {"chat": {"id": 1}, "message_id": 7709}})
    assert started == [2 * 3600]


def test_night_encode_turns_pc_on_when_off(re_env, monkeypatch):
    (re_env["enc"] / "[재인코딩] 0002 BTS.mkv").write_text("v")
    started = []
    monkeypatch.setattr(web_admin, "_ensure_pc_on_and_trigger_encode",
                        lambda progress=None, sync_timeout=None: started.append(sync_timeout) or (True, "인코딩 시작"))
    monkeypatch.setattr(web_admin, "_is_pc_online", lambda host: False)
    web_admin._run_reencode_encode()
    assert started == [2 * 3600] and re_env["sent"][-1][0].startswith("✅ 인코딩 시작")
    # 일시정지(STOP)는 새로 넣기만 멈춤 → 넣어 둔 파일은 그래도 인코딩
    started.clear()
    reencode_ctl.set_paused(True)
    web_admin._run_reencode_encode()
    reencode_ctl.set_paused(False)
    assert started == [2 * 3600]
    # 기다리는 파일이 없으면 아무것도 안 함
    started.clear()
    (re_env["enc"] / "[재인코딩] 0002 BTS.mkv").unlink()
    web_admin._run_reencode_encode()
    assert started == []


def test_schedule_due_once_per_day():
    tz = web_admin.APP_TZ
    at = lambda h, m: datetime(2026, 10, 28, h, m, tzinfo=tz)
    assert web_admin._reencode_due("01:00", at(1, 0), {})
    assert web_admin._reencode_due("01:00", at(1, 40), {})
    assert not web_admin._reencode_due("01:00", at(2, 5), {})            # 한 시간 넘게 지났으면 건너뜀(재시작 등)
    assert not web_admin._reencode_due("01:00", at(0, 59), {})
    assert not web_admin._reencode_due("01:00", at(1, 10), {"01:00": "2026-10-28"})
