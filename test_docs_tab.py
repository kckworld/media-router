# -*- coding: utf-8 -*-
"""설정 > Claude > [문서] (2026-11-10): .md 보기·고치기 — 경로 제한, .history, 바뀐 파일 덮어쓰기 방지, 소유자만"""
import os

import pytest

import accounts
import docs_mgr
import web_admin


@pytest.fixture
def dc(monkeypatch, tmp_path):
    roots = {}
    for k in ("rules", "held", "tasks", "memory"):
        (tmp_path / k).mkdir()
        roots[k] = str(tmp_path / k)
    (tmp_path / "rules" / "TEAM.md").write_text("# 팀\n규칙 1\n", encoding="utf-8")
    (tmp_path / "rules" / "agy-run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "rules" / ".secret.md").write_text("숨김", encoding="utf-8")
    (tmp_path / "outside.md").write_text("밖", encoding="utf-8")
    os.symlink(tmp_path / "outside.md", tmp_path / "rules" / "link.md")
    (tmp_path / "memory" / "MEMORY.md").write_text("- 메모\n", encoding="utf-8")
    monkeypatch.setattr(web_admin, "DOC_ROOTS", roots)
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "current_user", lambda req: accounts.OWNER)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), tmp_path


def test_list_only_md_no_hidden_no_symlink(dc):
    c, tmp = dc
    g = {x["key"]: [f["name"] for f in x["files"]] for x in c.get("/api/skills/docs").get_json()["groups"]}
    assert g["rules"] == ["TEAM.md"] and g["memory"] == ["MEMORY.md"] and g["held"] == []


def test_read_write_history_and_conflict(dc):
    c, tmp = dc
    d = c.get("/api/skills/docs/file?root=rules&name=TEAM.md").get_json()
    assert d["ok"] and d["text"].startswith("# 팀")
    ino = os.stat(tmp / "rules" / "TEAM.md").st_ino
    r = c.post("/api/skills/docs/file", json={"root": "rules", "name": "TEAM.md", "text": "# 팀\n규칙 2\n", "sha": d["sha"]}).get_json()
    assert r["ok"] and (tmp / "rules" / "TEAM.md").read_text(encoding="utf-8") == "# 팀\n규칙 2\n"
    assert os.stat(tmp / "rules" / "TEAM.md").st_ino == ino                         # 제자리 덮어쓰기(단일 파일 마운트 안전)
    hist = list((tmp / "rules" / ".history").iterdir())
    assert len(hist) == 1 and hist[0].read_text(encoding="utf-8") == "# 팀\n규칙 1\n"
    # 옛 sha로 저장하면(그 사이 세션이 바꿈) 409
    r = c.post("/api/skills/docs/file", json={"root": "rules", "name": "TEAM.md", "text": "덮어쓰기", "sha": d["sha"]})
    assert r.status_code == 409 and (tmp / "rules" / "TEAM.md").read_text(encoding="utf-8") == "# 팀\n규칙 2\n"


def test_path_rules(dc):
    c, tmp = dc
    for root, name in (("rules", "agy-run.sh"), ("rules", ".secret.md"), ("rules", "link.md"), ("rules", "../outside.md"),
                       ("nope", "TEAM.md"), ("rules", "sub/x.md")):
        assert c.get(f"/api/skills/docs/file?root={root}&name={name}").status_code == 404
        assert c.post("/api/skills/docs/file", json={"root": root, "name": name, "text": "x", "sha": ""}).status_code in (400, 409)
    assert (tmp / "outside.md").read_text(encoding="utf-8") == "밖"
    # 새 .md는 만들 수 있다(sha 비움)
    assert c.post("/api/skills/docs/file", json={"root": "held", "name": "W-20261110-99.md", "text": "보류", "sha": ""}).get_json()["ok"]
    assert docs_mgr.write(web_admin.DOC_ROOTS, "held", "big.md", "x" * (500 * 1024), "")[0] is False


def test_owner_only_and_page(dc, monkeypatch):
    c, tmp = dc
    html = c.get("/skills?tab=docs").get_data(as_text=True)
    assert 'id="dcList"' in html and 'id="dcSave"' in html
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")
    assert c.get("/api/skills/docs").status_code == 403
    assert c.post("/api/skills/docs/file", json={"root": "rules", "name": "TEAM.md", "text": "x", "sha": ""}).status_code == 403
