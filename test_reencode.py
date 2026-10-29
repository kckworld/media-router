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
    assert st["tiers"]["1"] == {"done": 1, "todo": 1, "failed": 0, "hold": 0, "saved": 6 * 2 ** 30, "expect": 10 * 2 ** 30}
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


def test_running_also_checks_work_stop(re_env):
    (re_env["work"] / "control.json").write_text(json.dumps({"run": True}), encoding="utf-8")
    assert reencode_ctl.running()
    (re_env["work"] / "STOP").write_text("", encoding="utf-8")       # PC 워커가 보는 멈춤 신호
    assert not reencode_ctl.running()


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


def test_night_start_is_3am():
    assert web_admin.REENCODE_NIGHT_AT == "03:00"
    tz = web_admin.APP_TZ
    at = lambda h, m: datetime(2026, 10, 28, h, m, tzinfo=tz)
    assert not web_admin._reencode_due("03:00", at(1, 0), {})
    assert web_admin._reencode_due("03:00", at(3, 30), {})
    assert not web_admin._reencode_due("03:00", at(4, 5), {})       # 1시간 넘게 지나면 건너뜀


def test_schedule_due_once_per_day():
    tz = web_admin.APP_TZ
    at = lambda h, m: datetime(2026, 10, 28, h, m, tzinfo=tz)
    assert web_admin._reencode_due("01:00", at(1, 0), {})
    assert not web_admin._reencode_due("01:00", at(2, 5), {})
    assert not web_admin._reencode_due("01:00", at(0, 59), {})
    assert not web_admin._reencode_due("01:00", at(1, 10), {"01:00": "2026-10-28"})


def _add_new_items(re_env, items):
    q = json.loads(reencode_ctl.QUEUE.read_text(encoding="utf-8"))
    q["new_items"] = items
    reencode_ctl.QUEUE.write_text(json.dumps(q), encoding="utf-8")


def test_new_items_in_state_and_has_work(re_env, monkeypatch):
    _add_new_items(re_env, [{"id": "N0001", "kind": "new", "src": "/v/임시/재인코딩/new/런닝맨.E826.mkv", "status": "fed", "size": 3 * 2 ** 30},
                            {"id": "N0002", "kind": "new", "src": "/v/new/x.mkv", "status": "failed", "msg": "길이가 다름"}])
    st = reencode_ctl.state()
    assert "🆕 새 예능 인코딩 대기 1개 · 실패 1개(new 폴더에 [손상] 표시)" in st["summary"]
    assert st["waiting_list"][0]["id"] == "N0001" and st["waiting_list"][0]["tier"] == "새 예능"
    assert st["failed"][0]["id"] == "N0002" and st["new_waiting"] == 1
    write_status(re_env, state="encoding", current="N0001", updated="2026-10-27 20:00:00")
    assert reencode_ctl.state()["current"]["name"] == "런닝맨.E826.mkv"
    # 일괄 쪽 일이 없어도 새 예능이 남았으면 새벽 시작 대상
    q = json.loads(reencode_ctl.QUEUE.read_text(encoding="utf-8"))
    for it in q["items"]:
        it["status"] = "done"
    reencode_ctl.QUEUE.write_text(json.dumps(q), encoding="utf-8")
    assert reencode_ctl.has_work() and not reencode_ctl.batch_has_work()


def test_newstart_does_not_resume_batch(re_env):
    now = int(time.time())
    web_admin._handle_telegram_callback({"id": "q", "data": f"re_newstart:{now}", "message": {"chat": {"id": 1}, "message_id": 7850}})
    assert re_env["tasks"] == ["ReencodeWorker"]
    assert not any(cmd == "resume" for cmd, _ in re_env["host"])     # 멈춤(STOP)은 그대로
    assert ("run", "") in re_env["host"]


def test_night_with_only_new_items_keeps_stop(re_env, monkeypatch):
    monkeypatch.setattr(reencode_ctl, "has_work", lambda: True)
    monkeypatch.setattr(reencode_ctl, "batch_has_work", lambda: False)
    monkeypatch.setattr(web_admin, "_is_pc_online", lambda host: False)
    web_admin._run_reencode_night()
    assert re_env["tasks"] == ["ReencodeWorker"] and not any(cmd == "resume" for cmd, _ in re_env["host"])


def test_encoding_in_progress_from_worker_heartbeat(re_env):
    fmt = lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now(web_admin.APP_TZ)
    write_status(re_env, state="encoding", updated=fmt(now))
    assert web_admin._is_encoding_in_progress()
    write_status(re_env, state="uploading", updated=fmt(now))
    assert web_admin._is_encoding_in_progress()
    write_status(re_env, state="waiting", updated=fmt(now))
    assert not web_admin._is_encoding_in_progress()
    from datetime import timedelta
    write_status(re_env, state="encoding", updated=fmt(now - timedelta(minutes=30)))   # 하트비트 끊김
    assert not web_admin._is_encoding_in_progress()


def test_telegram_menu_reencode_button(re_env):
    assert [{"text": web_admin.TG_BTN_REENCODE}, {"text": web_admin.TG_BTN_BATCH}] in web_admin.TG_MENU["keyboard"]
    web_admin._handle_telegram_text("🎬 인코딩 시작")          # 예전 메뉴 글자도 재인코딩 상태로
    assert re_env["sent"][-1][0].startswith("♻️ 예능 재인코딩")


def test_encode_page_and_removed_routes(re_env):
    c = re_env["client"]
    html = c.get("/encode_log").get_data(as_text=True)
    assert "재인코딩 (예능 · 드라마 · 새로 받은 영상)" in html and "offAfterBtn" not in html and "reSamples" not in html and "PC 예약 종료" in html and "runEncode" not in html
    assert c.post("/run_encode").status_code == 404
    assert c.get("/encode_hold_stats").status_code == 404 and c.get("/encode_log/tail").status_code == 404


def test_settings_change_schedule(re_env, monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "REENCODE_SETTINGS_PATH", tmp_path / "re_settings.json")
    assert web_admin.reencode_settings() == {"night_on": True, "night_at": "03:00", "morning_on": True, "morning_at": "08:00"}
    monkeypatch.setattr(web_admin, "MEDIA_REQUEST_SETTINGS", tmp_path / "req.json")
    monkeypatch.setattr(web_admin, "save_cfg", lambda cfg: None)
    form = {"own_file_mode": "664", "own_dir_mode": "775", "re_night_at": "04:30", "re_morning_on": "1",
            "re_morning_at": "07:00", "request_code": "손흥민"}
    html = re_env["client"].post("/settings", data=form).get_data(as_text=True)
    assert "저장하지 못했어요" not in html and 'value="04:30"' in html and 'value="손흥민"' in html
    assert web_admin.reencode_settings() == {"night_on": False, "night_at": "04:30", "morning_on": True, "morning_at": "07:00"}
    assert web_admin._request_code() == "손흥민"
    bad = re_env["client"].post("/settings", data=dict(form, re_night_at="25:00")).get_data(as_text=True)
    assert "03:00처럼" in bad and web_admin.reencode_settings()["night_at"] == "04:30"   # 잘못된 값은 저장 안 함


def test_scheduler_skips_turned_off_slot(re_env, monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "REENCODE_SETTINGS_PATH", tmp_path / "re_settings.json")
    web_admin.REENCODE_SETTINGS_PATH.write_text(json.dumps({"night_on": False, "night_at": "04:30", "morning_on": True, "morning_at": "04:00"}))
    monkeypatch.setattr(reencode_ctl, "available", lambda: True)
    monkeypatch.setattr(reencode_ctl, "needs_collect", lambda: False)
    ran = []
    monkeypatch.setattr(web_admin, "_run_reencode_night", lambda: ran.append("night"))
    monkeypatch.setattr(web_admin, "_reencode_status_text", lambda head="": ran.append("morning") or "요약")
    fixed = datetime(2026, 10, 28, 4, 40, tzinfo=web_admin.APP_TZ)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(web_admin, "datetime", FakeDT)
    calls = {"n": 0}

    def fake_sleep(sec):
        calls["n"] += 1
        if calls["n"] > 1:
            raise SystemExit
    monkeypatch.setattr(web_admin.time, "sleep", fake_sleep)

    class NowThread:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(web_admin.threading, "Thread", NowThread)
    try:
        web_admin._reencode_scheduler_loop()
    except SystemExit:
        pass
    state = json.loads(web_admin.REENCODE_SCHED_PATH.read_text())
    assert ran == ["morning"] and state.get("morning") == "2026-10-28" and "night" not in state


def _off_after_env(re_env, monkeypatch):
    now = datetime.now(web_admin.APP_TZ)
    write_status(re_env, state="encoding", current="0004", updated=now.strftime("%Y-%m-%d %H:%M:%S"))
    off, cancel = [], []
    monkeypatch.setattr(web_admin, "_request_pc_shutdown", lambda force: off.append(force) or (True, "1분 후 종료", 200, False))
    monkeypatch.setattr(web_admin, "_request_cancel_shutdown", lambda: cancel.append(1) or (True, "취소", 200))
    return off, cancel


def _set_item(job_id, **kw):
    q = json.loads(reencode_ctl.QUEUE.read_text(encoding="utf-8"))
    for it in q["items"]:
        if it["id"] == job_id:
            it.update(kw)
    reencode_ctl.QUEUE.write_text(json.dumps(q), encoding="utf-8")


def test_off_after_current_waits_for_collect_then_shuts_down(re_env, monkeypatch):
    off, cancel = _off_after_env(re_env, monkeypatch)
    r = re_env["client"].post("/api/reencode/off-after-current")
    assert r.status_code == 200 and "도레미 마켓.E435.mp4" in r.get_json()["message"]
    assert ("stop", "web") in re_env["host"]
    state = json.loads(web_admin.REENCODE_SCHED_PATH.read_text())
    assert state["off_after"]["id"] == "0004"
    assert re_env["client"].get("/api/reencode").get_json()["off_after"]["id"] == "0004"
    # 워커가 올리고 멈춰도(shutting_down) NAS 교체 전이면 끄지 않고, 평소 '5분 후 꺼져요' 알림도 안 보낸다
    write_status(re_env, state="shutting_down", current="", updated="2026-10-27 22:00:00")
    assert web_admin._reencode_watch_worker(state) and not web_admin._reencode_off_after(state)
    assert off == [] and not re_env["sent"]
    _set_item("0004", status="collecting")
    assert not web_admin._reencode_off_after(state) and off == []
    _set_item("0004", status="done")
    assert web_admin._reencode_off_after(state)
    assert off == [True] and "off_after" not in state and "교체까지 끝났어요" in re_env["sent"][-1][0]


def test_off_after_current_failure_keeps_pc_on(re_env, monkeypatch):
    off, cancel = _off_after_env(re_env, monkeypatch)
    assert re_env["client"].post("/api/reencode/off-after-current").status_code == 200
    state = json.loads(web_admin.REENCODE_SCHED_PATH.read_text())
    _set_item("0004", status="pending", attempts=1, msg="1번째 실패, 다시 시도 예정: 길이가 다름")   # 검사 실패 → 재시도 대기
    assert web_admin._reencode_off_after(state)
    assert off == [] and cancel == [1] and "off_after" not in state
    assert "끄지 않았어요" in re_env["sent"][-1][0] and "길이가 다름" in re_env["sent"][-1][0]


def test_off_after_current_needs_encoding_and_expires(re_env, monkeypatch):
    off, cancel = _off_after_env(re_env, monkeypatch)
    write_status(re_env, state="waiting", updated=datetime.now(web_admin.APP_TZ).strftime("%Y-%m-%d %H:%M:%S"))
    r = re_env["client"].post("/api/reencode/off-after-current")
    assert r.status_code == 409 and "인코딩 중인 파일이 없어요" in r.get_json()["error"] and not re_env["host"]
    state = {"off_after": {"id": "0004", "since": int(time.time()) - web_admin.REENCODE_OFF_AFTER_TTL - 1, "attempts": 0}}
    assert web_admin._reencode_off_after(state) and "off_after" not in state and off == []


def test_settings_status_and_claude_tab(re_env, monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "DATA_DIR", tmp_path)
    (tmp_path / "auto_subs.json").write_text(json.dumps({
        "a|1": {"status": "replaced", "chars": 9049, "at": 1792369457.3, "path": "/v/코난.ko.ass"},
        "b|2": {"status": "no_track", "at": 1793000422.9}}))
    monkeypatch.setattr(web_admin, "_qbit_get", lambda path: {"connection_status": "connected"})
    monkeypatch.setattr(web_admin, "_is_pc_online", lambda host: False)
    write_status(re_env, state="waiting", updated="2026-10-27 22:00:00")
    d = re_env["client"].get("/settings/status").get_json()
    assert d["worker"]["state"] == "waiting" and d["deepl"]["count"] == 1 and d["deepl"]["chars"] == 9049
    assert isinstance(d["kobis"], bool)
    html = re_env["client"].get("/settings").get_data(as_text=True)
    assert "♻️ 재인코딩" in html and "bt4gToggle" in html and "set-fold" in html and "claudePill" not in html
    skills = re_env["client"].get("/skills").get_data(as_text=True)
    assert "claudePill" in skills and "sessList" in skills and "loadClaudeSessions();" in skills
    assert ">Claude<" in skills and "Claude 스킬" not in skills


def test_pending_upload_in_summary(re_env):
    write_status(re_env, state="encoding", current="0004", pending_upload=2, updated="2026-10-27 22:50:00")
    assert "PC: encoding — #0004 · 올리기 대기 2개 (10-27 22:50)" in reencode_ctl.state()["summary"]
    write_status(re_env, state="encoding", current="0004", pending_upload=0, updated="2026-10-27 22:50:00")
    assert "올리기 대기" not in reencode_ctl.state()["summary"]


def test_night_skips_when_worker_already_running(re_env, monkeypatch):
    monkeypatch.setattr(web_admin, "_is_pc_online", lambda host: True)
    now = datetime.now(web_admin.APP_TZ).strftime("%Y-%m-%d %H:%M:%S")
    write_status(re_env, state="encoding", current="0004", updated=now)
    (re_env["work"] / "control.json").write_text(json.dumps({"run": True}), encoding="utf-8")
    web_admin._run_reencode_night()
    assert not re_env["sent"] and not re_env["tasks"] and not re_env["host"]      # 이미 돌고 있으면 조용히
    # 새 예능만 하느라 일괄 쪽이 멈춰 있으면 묻지 않고 이어서 하게만
    (re_env["work"] / "control.json").write_text(json.dumps({"run": False}), encoding="utf-8")
    web_admin._run_reencode_night()
    assert re_env["host"] == [("resume", "auto")] and not re_env["tasks"]
    assert "이어서 하게 했어요" in re_env["sent"][-1][0]


def test_drama_hold_tiers_and_samples(re_env):
    q = json.loads(reencode_ctl.QUEUE.read_text(encoding="utf-8"))
    q["items"] += [{"id": "1539", "src": "/v/01.드라마/닥터 브레인.E04.mkv", "tier": "D1", "profile": "drama", "status": "hold",
                    "size": 4 * 2 ** 30, "expect_save": 2 * 2 ** 30, "mbps": 10.75, "res": "1920x818"},
                   {"id": "1540", "src": "/v/01.드라마/x.E01.mkv", "tier": "D3", "profile": "drama", "status": "hold", "expect_save": 2 ** 30}]
    q["samples"] = [{"id": "T0001", "kind": "sample", "profile": "drama", "src": "/v/01.드라마/굿보이.E02.mkv", "size": 4 * 2 ** 30,
                     "mbps": 8.97, "status": "done", "new_size": 3 * 2 ** 30, "out_mbps": 6.1,
                     "final": "/v/임시/재인코딩/sample/굿보이.E02.SAMPLE.mp4"},
                    {"id": "T0002", "kind": "sample", "profile": "drama", "src": "/v/01.드라마/y.E01.mkv", "size": 2 ** 30, "status": "fed"}]
    reencode_ctl.QUEUE.write_text(json.dumps(q), encoding="utf-8")
    write_status(re_env, state="encoding", current="T0002", updated="2026-10-28 14:30:00")
    st = reencode_ctl.state()          # 숫자 순위와 D1~D3가 섞여도 정렬이 깨지지 않는다
    assert list(st["tiers"]) == ["1", "2", "3", "D1", "D3"]
    assert st["tiers"]["D1"]["hold"] == 1 and st["tiers"]["D1"]["todo"] == 0
    assert "실패 1 · 보류(드라마) 2\n" in st["summary"]
    assert [s["id"] for s in st["samples"]] == ["T0002", "T0001"] and st["samples"][1]["out_mbps"] == 6.1
    assert st["current"]["tier"] == "샘플" and st["waiting_list"][0]["id"] == "T0002"
    assert not reencode_ctl.batch_has_work() or any(it["status"] in ("pending", "fed") for it in q["items"])
    assert re_env["client"].get("/api/reencode").get_json()["samples"][1]["final"].endswith(".SAMPLE.mp4")


def test_new_items_label_drama(re_env):
    _add_new_items(re_env, [{"id": "N0001", "kind": "new", "src": "/v/new/런닝맨.E826.mkv", "status": "fed"},
                            {"id": "N0002", "kind": "new", "profile": "drama", "src": "/v/new/drama/굿보이.E03.mkv", "status": "pending"}])
    st = reencode_ctl.state()
    assert "🆕 새 영상(예능 1·드라마 1) 인코딩 대기 2개" in st["summary"]
    assert [r["tier"] for r in st["waiting_list"][:2]] == ["새 예능", "새 드라마"]
    _add_new_items(re_env, [{"id": "N0002", "kind": "new", "profile": "drama", "src": "/v/new/drama/굿보이.E03.mkv", "status": "pending"}])
    assert "🆕 새 드라마 인코딩 대기 1개" in reencode_ctl.state()["summary"]


def test_failed_ack_moves_to_acked(re_env, monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "REENCODE_ACK_PATH", tmp_path / "ack.json")
    c = re_env["client"]
    d = c.get("/api/reencode").get_json()
    assert [f["id"] for f in d["failed"]] == ["0003"] and d["failed_acked"] == []
    assert c.post("/api/reencode/ack", json={"id": "0003"}).get_json()["ok"]
    d = c.get("/api/reencode").get_json()
    assert d["failed"] == [] and [f["id"] for f in d["failed_acked"]] == ["0003"]
    assert c.post("/api/reencode/ack", json={"id": "../x"}).status_code == 400


@pytest.mark.parametrize("rc, out, code", [
    (1190, b"", 1190),
    (1, "시스템 종료가 이미 예약되어 있습니다.(1190)\r\n".encode("cp949"), 1190),
    (1, "시스템 종료가 진행 중이 아니기 때문에 취소할 수 없습니다.(1116)\r\n".encode("cp949"), 1116),
    (1, b"", 1),
])
def test_shutdown_code_reads_number_from_message(rc, out, code):
    """SSH로 돌린 shutdown.exe는 종료코드가 1로 오고 번호는 메시지 끝에만 남을 수 있다 (2026-10-29)"""
    result = web_admin.subprocess.CompletedProcess([], rc, stdout=b"", stderr=out)
    assert web_admin._shutdown_code(result) == code
