# -*- coding: utf-8 -*-
"""예능 일괄 재인코딩 v2 연결: 상태, 시작(호스트 resume → PC 켜기 → ReencodeWorker), 종료, 새벽·아침, PC 종료 예고"""
import json
import time
from datetime import datetime

import pytest

import reencode_ctl
import web_admin


@pytest.fixture
def re_env(monkeypatch, tmp_path):
    base, work = tmp_path / "reencode", tmp_path / "재인코딩"
    base.mkdir()
    for d in ("todo", "doing", "out", "done"):
        (work / d).mkdir(parents=True)
    (base / "queue.json").write_text(json.dumps({"trash_bytes": 0, "items": [
        {"id": "0001", "src": "/v/유희열의 스케치북.E509.mp4", "tier": 1, "status": "done", "size": 8 * 2 ** 30,
         "new_size": 2 * 2 ** 30, "saved": 6 * 2 ** 30, "expect_save": 6 * 2 ** 30, "mbps": 11.8, "dur": 5600, "done_at": "2026-10-27 16:20:25"},
        {"id": "0004", "src": "/v/도레미 마켓.E435.mp4", "tier": 1, "status": "fed", "size": 5 * 2 ** 30, "expect_save": 4 * 2 ** 30,
         "mbps": 8.3, "res": "1920x1080"},
        {"id": "0003", "src": "/v/무한도전.avi", "tier": 2, "status": "failed", "msg": "길이가 다름", "expect_save": 0},
        {"id": "0007", "src": "/v/런닝맨.mp4", "tier": 3, "status": "pending", "size": 2 ** 30, "expect_save": 2 ** 29}]}),
        encoding="utf-8")
    (base / "reencode.log").write_text("[x] collect\n", encoding="utf-8")
    (work / "worker.log").write_text("[pc] #0004 인코딩 시작\n", encoding="utf-8")
    monkeypatch.setattr(reencode_ctl, "QUEUE", base / "queue.json")
    monkeypatch.setattr(reencode_ctl, "STOP", base / "STOP")
    monkeypatch.setattr(reencode_ctl, "LOG", base / "reencode.log")
    monkeypatch.setattr(reencode_ctl, "WORK", work)
    monkeypatch.setattr(web_admin, "REENCODE_SCHED_PATH", tmp_path / "sched.json")
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    sent, host, tasks = [], [], []
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, markup=None, token=None: sent.append((text, markup)) or True)
    monkeypatch.setattr(web_admin, "_tg_call", lambda *a, **k: {"ok": True})

    def fake_host(cmd, by="", wait=True):
        host.append((cmd, by))
        if cmd in ("resume", "stop"):   # 호스트의 reencode.py가 control.json을 쓰는 것을 흉내
            (work / "control.json").write_text(json.dumps({"run": cmd == "resume"}), encoding="utf-8")
        return True
    monkeypatch.setattr(web_admin, "_reencode_host", fake_host)
    monkeypatch.setattr(web_admin, "_wake_pc", lambda progress=None, then="": (True, ""))
    monkeypatch.setattr(web_admin, "_trigger_remote_task", lambda task, msg: tasks.append(task) or (True, msg))
    web_admin.app.config["TESTING"] = True
    return {"work": work, "sent": sent, "host": host, "tasks": tasks, "client": web_admin.app.test_client()}


def write_status(re_env, **w):
    (re_env["work"] / "status.json").write_text(json.dumps(w), encoding="utf-8")


def test_state(re_env):
    write_status(re_env, state="encoding", current="0004", updated="2026-10-27 17:50:01")
    st = reencode_ctl.state()
    assert st["summary"].startswith("완료 1/4 · 진행 1 · 대기 1 · 실패 1\n줄인 용량 6.0GB / 예상 10.5GB\n⏹ 멈춤\nPC: encoding — #0004")
    assert st["current"]["name"] == "도레미 마켓.E435.mp4"
    assert st["tiers"]["1"] == {"done": 1, "todo": 1, "failed": 0, "saved": 6 * 2 ** 30, "expect": 10 * 2 ** 30}
    assert [r["id"] for r in st["waiting_list"]] == ["0004", "0007"] and st["done_list"][0]["new_size"] == 2 * 2 ** 30
    assert st["failed"][0]["msg"] == "길이가 다름" and not st["trash_warn"]


def test_start_order_resume_then_pc_then_worker(re_env):
    r = re_env["client"].post("/api/reencode/start").get_json()
    assert r["ok"]
    # 워커는 run=false면 바로 PC를 끄므로: 호스트 resume → todo 채우기 → PC 켜기 → 작업 스케줄러
    assert re_env["host"][:2] == [("resume", "web"), ("run", "")] and re_env["tasks"] == ["ReencodeWorker"]
    assert reencode_ctl.running()


def test_start_fails_if_resume_not_recorded(re_env, monkeypatch):
    monkeypatch.setattr(web_admin, "_reencode_host", lambda cmd, by="", wait=True: False)
    r = re_env["client"].post("/api/reencode/start")
    assert r.status_code == 500 and not re_env["tasks"]


def test_stop_and_viewer(re_env, monkeypatch):
    assert re_env["client"].post("/api/reencode/stop").get_json()["ok"]
    assert re_env["host"][-1] == ("stop", "web")
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert re_env["client"].post("/api/reencode/start").status_code == 403


def test_api_includes_logs(re_env):
    d = re_env["client"].get("/api/reencode").get_json()
    assert "collect" in d["log"] and "인코딩 시작" in d["worker_log"]


def test_telegram_buttons(re_env):
    now = int(time.time())
    cb = lambda a, mid: {"id": "q", "data": f"{a}:{now - 3600 * 5}", "message": {"chat": {"id": 1}, "message_id": 7800 + mid}}
    web_admin._handle_telegram_callback(cb("re_resume", 1))
    assert re_env["tasks"] == ["ReencodeWorker"] and ("resume", "telegram") in re_env["host"]
    assert re_env["sent"][-1][1]["inline_keyboard"][0][0]["callback_data"].startswith("re_stop:")
    web_admin._handle_telegram_callback(cb("re_stop", 2))
    assert re_env["host"][-1] == ("stop", "telegram")
    web_admin._handle_telegram_callback(cb("re_status", 3))
    assert re_env["sent"][-1][0].startswith("♻️ 예능 재인코딩\n완료 1/4")


def test_night_asks_when_pc_on_and_starts_when_off(re_env, monkeypatch):
    monkeypatch.setattr(web_admin, "_is_pc_online", lambda host: True)
    web_admin._run_reencode_night()
    text, markup = re_env["sent"][-1]
    assert "PC가 켜져 있어요" in text and not re_env["tasks"]
    web_admin._handle_telegram_callback({"id": "q", "data": markup["inline_keyboard"][0][0]["callback_data"],
                                         "message": {"chat": {"id": 1}, "message_id": 7810}})
    assert re_env["tasks"] == ["ReencodeWorker"] and ("resume", "auto") in re_env["host"]
    re_env["tasks"].clear()
    monkeypatch.setattr(web_admin, "_is_pc_online", lambda host: False)
    web_admin._run_reencode_night()
    assert re_env["tasks"] == ["ReencodeWorker"]


def test_night_does_nothing_without_work(re_env, monkeypatch):
    monkeypatch.setattr(reencode_ctl, "has_work", lambda: False)
    web_admin._run_reencode_night()
    assert not re_env["tasks"] and not re_env["sent"]


def test_shutting_down_notified_once(re_env):
    state = {}
    write_status(re_env, state="encoding", updated="2026-10-27 18:00:00")
    assert not web_admin._reencode_watch_worker(state)
    write_status(re_env, state="shutting_down", updated="2026-10-27 19:00:00")
    assert web_admin._reencode_watch_worker(state)
    text, markup = re_env["sent"][-1]
    assert text.startswith("♻️ 재인코딩 끝 — 5분 후 PC가 꺼져요.") and markup["inline_keyboard"][0][0]["callback_data"].startswith("cancel_off:")
    assert not web_admin._reencode_watch_worker(state)              # 같은 하트비트는 한 번만


def test_needs_collect(re_env):
    assert not reencode_ctl.needs_collect()
    (re_env["work"] / "done" / "0004.json").write_text("{}", encoding="utf-8")
    assert reencode_ctl.needs_collect()


def test_trash_button_only_for_leftover(re_env):
    q = json.loads(reencode_ctl.QUEUE.read_text(encoding="utf-8"))
    q["trash_bytes"] = 101 * 2 ** 30
    reencode_ctl.QUEUE.write_text(json.dumps(q), encoding="utf-8")
    assert "예전 방식 재인코딩 파일 약 101.0GB" in reencode_ctl.state()["summary"]
    assert web_admin._reencode_buttons()["inline_keyboard"][1][0]["callback_data"].startswith("re_trash:")


def test_schedule_due_once_per_day():
    tz = web_admin.APP_TZ
    at = lambda h, m: datetime(2026, 10, 28, h, m, tzinfo=tz)
    assert web_admin._reencode_due("01:00", at(1, 0), {})
    assert not web_admin._reencode_due("01:00", at(2, 5), {})
    assert not web_admin._reencode_due("01:00", at(0, 59), {})
    assert not web_admin._reencode_due("01:00", at(1, 10), {"01:00": "2026-10-28"})
