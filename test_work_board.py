# -*- coding: utf-8 -*-
"""설정 > Claude > [작업] (2026-11-07): work.json 읽기만, 작업 파일은 결과·확인 필요 부분만, 소유자만 — 가짜 데이터로"""
import json
import os

import pytest

import accounts
import web_admin
import work_board

WORK = {"updated": "2026-11-07T01:00+09:00", "items": [
    {"id": "W-1", "title": "진행 중인 일", "cat": "MR", "status": "진행 중", "owner": "미디어 팀", "engine": "Claude",
     "task": "tasks/a.md", "requested": "2026-11-07T00:30+09:00", "updated": "2026-11-07T00:40+09:00",
     "history": [{"at": "2026-11-07T00:30+09:00", "status": "대기", "note": "받음"}], "secret_extra": "버림"},
    {"id": "W-2", "title": "결정 기다림", "cat": "보안", "status": "확인 필요", "owner": "팀장", "engine": "agy", "task": "../../etc/passwd"},
    {"id": "W-3", "title": "끝난 일", "cat": "생활", "status": "완료", "owner": "인사팀", "engine": "Claude", "task": "tasks/nofile.md"},
    {"title": "id 없음"}]}
TASK_A = """# 제목
## 할 일
비밀 아님 하지만 안 보여 줌
## 결과
- 배포함
## 확인 필요
- 사용자에게 물어봄
## 범위
안 보임
"""


@pytest.fixture
def wk(monkeypatch, tmp_path):
    (tmp_path / "work.json").write_text(json.dumps(WORK, ensure_ascii=False), encoding="utf-8")
    t = tmp_path / "tasks"
    t.mkdir()
    (t / "a.md").write_text(TASK_A, encoding="utf-8")
    (tmp_path / "outside.md").write_text("## 결과\n밖에 있는 파일", encoding="utf-8")
    os.symlink(tmp_path / "outside.md", t / "link.md")
    monkeypatch.setattr(web_admin, "WORK_JSON", tmp_path / "work.json")
    monkeypatch.setattr(web_admin, "WORK_TASKS_DIR", t)
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), tmp_path


def test_list_read_only_clean_fields(wk):
    c, tmp = wk
    before = (tmp / "work.json").read_text(encoding="utf-8")
    d = c.get("/api/skills/work").get_json()
    assert d["ok"] and d["updated"].startswith("2026-11-07") and [i["id"] for i in d["items"]] == ["W-1", "W-2", "W-3"]
    assert "secret_extra" not in d["items"][0] and d["items"][0]["history"][0]["note"] == "받음"
    assert (tmp / "work.json").read_text(encoding="utf-8") == before                 # MR은 안 씀
    (tmp / "work.json").write_text("{깨짐", encoding="utf-8")
    assert c.get("/api/skills/work").get_json()["ok"] is False


def test_task_sections_only_results(wk):
    c, tmp = wk
    t = c.get("/api/skills/work/task?id=W-1").get_json()
    assert t["ok"] and not t["missing"] and [s["title"] for s in t["sections"]] == ["결과", "확인 필요"]
    assert "배포함" in t["sections"][0]["body"] and "안 보여 줌" not in json.dumps(t, ensure_ascii=False)
    assert c.get("/api/skills/work/task?id=W-2").get_json()["missing"] is True       # ../ 경로 거부
    assert c.get("/api/skills/work/task?id=W-3").get_json()["missing"] is True       # 파일 없음
    assert c.get("/api/skills/work/task?id=W-9").status_code == 404


def test_symlink_and_other_files_refused(wk, tmp_path):
    items = [{"id": "X", "task": "tasks/link.md"}, {"id": "Y", "task": "tasks/sub/a.md"}]
    assert work_board.task_sections(tmp_path / "tasks", items, "X")["missing"] is True
    assert work_board.task_sections(tmp_path / "tasks", items, "Y")["missing"] is True


def test_owner_only(wk, monkeypatch):
    c, tmp = wk
    host = sorted(web_admin.REQUEST_PUBLIC_HOSTS)[0]
    assert c.get("/api/skills/work", headers={"Host": host}).status_code == 404
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.get("/api/skills/work").status_code == 403
    assert c.get("/api/skills/work/task?id=W-1").status_code == 403
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")
    assert c.get("/api/skills/work").status_code == 403
    monkeypatch.setattr(web_admin, "authed", lambda req: ("login", 302))
    assert c.get("/api/skills/work").status_code == 401


def test_work_tab_page(wk, monkeypatch):
    c, tmp = wk
    h = c.get("/skills?tab=work").get_data(as_text=True)
    assert 'id="wkList"' in h and 'id="wkSess"' in h and 'aria-current="page">작업<' in h and "/api/skills/work" in h
    assert 'id="sessList"' not in h                                                   # 세션 관리 카드는 다른 탭
    h = c.get("/skills").get_data(as_text=True)
    assert 'id="sessList"' in h and 'href="/skills?tab=work"' in h and 'id="wkList"' not in h
