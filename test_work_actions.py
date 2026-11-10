# -*- coding: utf-8 -*-
"""작업 [취소]·[보류]·[다시 하기] (2026-11-10): work.json 상태 바꾸기(flock·제자리), 보류는 held/<ID>.md, 소유자만"""
import json

import pytest

import accounts
import web_admin
import work_board

WORK = {"updated": "2026-11-10T01:00+09:00", "items": [
    {"id": "W-20261110-07", "title": "재다운로드", "cat": "미디어", "status": "진행 중", "owner": "팀장", "engine": "Claude",
     "task": "tasks/a.md", "note": "16개", "history": [{"at": "2026-11-10T05:50+09:00", "status": "진행 중", "note": "시작"}]},
    {"id": "W-20261110-08", "title": "끝난 일", "status": "완료", "owner": "팀장"},
    {"id": "W-20261110-09", "title": "대기 중", "status": "대기", "owner": "팀장"}]}


@pytest.fixture
def wk(monkeypatch, tmp_path):
    (tmp_path / "work.json").write_text(json.dumps(WORK, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "a.md").write_text("# a\n## 결과\n- 텐트 92%\n## 범위\n안 보임\n", encoding="utf-8")
    monkeypatch.setattr(web_admin, "WORK_JSON", tmp_path / "work.json")
    monkeypatch.setattr(web_admin, "WORK_TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(web_admin, "WORK_HELD_DIR", tmp_path / "held")
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "current_user", lambda req: accounts.OWNER)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), tmp_path


def items(tmp):
    return {i["id"]: i for i in json.loads((tmp / "work.json").read_text(encoding="utf-8"))["items"]}


def test_hold_writes_held_file_then_resume(wk):
    c, tmp = wk
    r = c.post("/api/skills/work/action", json={"id": "W-20261110-07", "action": "hold"}).get_json()
    assert r["ok"] and r["status"] == "보류" and r["held"] == "held/W-20261110-07.md"
    it = items(tmp)["W-20261110-07"]
    assert it["status"] == "보류" and it["history"][-1]["note"] == "사용자가 MR에서 보류"
    held = (tmp / "held" / "W-20261110-07.md").read_text(encoding="utf-8")
    assert "# 보류: 재다운로드" in held and "텐트 92%" in held and "다시 시작하려면" in held and "안 보임" not in held
    assert c.post("/api/skills/work/action", json={"id": "W-20261110-07", "action": "hold"}).status_code == 400   # 이미 보류
    r = c.post("/api/skills/work/action", json={"id": "W-20261110-07", "action": "resume"}).get_json()
    assert r["ok"] and items(tmp)["W-20261110-07"]["status"] == "대기"


def test_cancel_and_rules(wk):
    c, tmp = wk
    assert c.post("/api/skills/work/action", json={"id": "W-20261110-09", "action": "cancel"}).get_json()["ok"]
    it = items(tmp)["W-20261110-09"]
    assert it["status"] == "취소" and it["done"]
    assert c.post("/api/skills/work/action", json={"id": "W-20261110-08", "action": "cancel"}).status_code == 400  # 완료는 못 바꿈
    assert c.post("/api/skills/work/action", json={"id": "W-20261110-09", "action": "resume"}).status_code == 400  # 보류만 다시
    assert c.post("/api/skills/work/action", json={"id": "../x", "action": "cancel"}).status_code == 400
    assert c.post("/api/skills/work/action", json={"id": "W-20261110-07", "action": "delete"}).status_code == 400
    # 다른 항목·형식은 그대로(제자리 덮어쓰기, 같은 들여쓰기)
    raw = (tmp / "work.json").read_text(encoding="utf-8")
    assert '\n "items": [' in raw and items(tmp)["W-20261110-08"]["status"] == "완료"


def test_owner_only(wk, monkeypatch):
    c, tmp = wk
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")
    assert c.post("/api/skills/work/action", json={"id": "W-20261110-09", "action": "cancel"}).status_code == 403
    assert items(tmp)["W-20261110-09"]["status"] == "대기"


def test_page_has_buttons(wk):
    c, tmp = wk
    html = c.get("/skills?tab=work").get_data(as_text=True)
    assert 'data-act="hold"' in html and 'data-act="cancel"' in html and "/api/skills/work/action" in html
