# -*- coding: utf-8 -*-
"""생활 > 일기 (2026-11-03): 초안 보기·복사·고쳐 저장(final.json), 리뷰·교훈 읽기만"""
import json
import os

import pytest

import web_admin


@pytest.fixture
def jn(monkeypatch, tmp_path):
    d = tmp_path / "diary_drafts"
    d.mkdir()
    for day, title in (("2026-11-01", "첫날"), ("2026-11-02", "둘째 날")):
        (d / f"{day}.draft.json").write_text(json.dumps({"date": day, "title": title, "body": "본문\n둘째 줄",
                                                          "made_at": day + "T23:00:00+09:00",
                                                          "photos": [{"no": 1, "time": day + "T10:00:00", "who": "아빠", "place": "공원"}]},
                                                         ensure_ascii=False), encoding="utf-8")
    (d / "lessons.json").write_text(json.dumps({"rules": [{"text": "아이 이름은 줄임말로", "count": 2}]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(web_admin, "DRAFT_DIR", d)
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), d


def test_list_show_save_and_review(jn):
    c, d = jn
    html = c.get("/journal").get_data(as_text=True)
    assert "제목 복사" in html and "본문 복사" in html and "초안 보기" in html and 'id="pageOrder"' in html
    r = c.get("/api/journal").get_json()
    assert r["dates"] == ["2026-11-02", "2026-11-01"] and r["date"] == "2026-11-02"      # 기본 가장 최근
    assert r["draft"]["title"] == "둘째 날" and r["final"] is None and r["lessons"]["rules"][0]["count"] == 2
    assert c.post("/api/journal/save", json={"date": "2026-11-02", "title": "", "body": "x"}).status_code == 400
    assert c.post("/api/journal/save", json={"date": "2026-10-01", "title": "a", "body": "b"}).status_code == 400   # 초안 없는 날
    s1 = c.post("/api/journal/save", json={"date": "2026-11-02", "title": " 고친 제목 ", "body": "고친\r\n본문"}).get_json()
    f = d / "2026-11-02.final.json"
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert s1["ok"] and saved["title"] == "고친 제목" and saved["body"] == "고친\n본문" and saved["saved_at"].endswith("+09:00")
    assert oct(os.stat(f).st_mode & 0o777) == "0o644"
    assert c.get("/api/journal").get_json()["final"]["title"] == "고친 제목"
    # 팀장 쪽 리뷰: 지금 저장본에 대한 것만 보여 줌
    (d / "2026-11-02.review.json").write_text(json.dumps({"points": ["날씨를 지어냄"], "final_saved_at": saved["saved_at"]},
                                                         ensure_ascii=False), encoding="utf-8")
    assert c.get("/api/journal").get_json()["review"]["points"] == ["날씨를 지어냄"]
    import time
    time.sleep(1.1)
    c.post("/api/journal/save", json={"date": "2026-11-02", "title": "또", "body": "또 고침"})
    assert c.get("/api/journal").get_json()["review"] is None                     # 새로 저장 → 다시 '학습 중'
    assert json.loads((d / "2026-11-02.final.prev.json").read_text(encoding="utf-8"))["title"] == "고친 제목"   # 직전 하나 보관
    assert json.loads((d / "2026-11-02.draft.json").read_text(encoding="utf-8"))["title"] == "둘째 날"          # 초안은 안 건드림


def test_admin_only(jn, monkeypatch):
    c, d = jn
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.get("/api/journal").status_code == 403
    assert c.post("/api/journal/save", json={"date": "2026-11-02", "title": "a", "body": "b"}).status_code == 403
    assert c.get("/journal", headers={"Accept": "text/html"}).status_code == 302
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    host = sorted(web_admin.REQUEST_PUBLIC_HOSTS)[0]
    assert c.get("/journal", headers={"Host": host}).status_code == 404


def test_no_drafts_yet(jn, monkeypatch, tmp_path):
    c, d = jn
    empty = tmp_path / "none"
    empty.mkdir()
    monkeypatch.setattr(web_admin, "DRAFT_DIR", empty)
    r = c.get("/api/journal").get_json()
    assert r["ok"] and r["dates"] == [] and r["draft"] is None
    assert "아직 일기 초안이 없어요" in c.get("/journal").get_data(as_text=True)
