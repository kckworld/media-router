# -*- coding: utf-8 -*-
"""일기 메모 /diary (2026-11-02)"""
import json
import os

import pytest

import diary_memos as dm
import web_admin


@pytest.fixture
def dy(monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "DIARY_DIR", tmp_path / "data" / "diary_memos")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), tmp_path / "data" / "diary_memos"


def test_write_list_delete(dy):
    c, base = dy
    html = c.get("/diary").get_data(as_text=True)
    assert "일기 메모" in html and 'id="writeBtn"' in html and 'id="pageOrder"' in html      # MR 틀 안(생활 그룹)
    app_html = c.get("/diary?app=1").get_data(as_text=True)
    assert "/static/diary.webmanifest" in app_html and 'id="writeBtn"' in app_html and "pageOrder" not in app_html
    assert c.post("/api/diary", json={"text": "  "}).status_code == 400
    assert c.post("/api/diary", json={"text": "x" * 501}).status_code == 400
    r = c.post("/api/diary", json={"text": "점심에 공원 산책\n날씨 좋음"}).get_json()
    assert r["ok"] and r["item"]["src"] == "web" and r["item"]["time"].endswith("+09:00")
    day = r["item"]["time"][:10]
    f = base / f"{day}.jsonl"
    line = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
    assert line["text"] == "점심에 공원 산책\n날씨 좋음" and set(line) == {"id", "time", "text", "src"}
    assert oct(f.stat().st_mode & 0o777) == "0o644" and oct(base.stat().st_mode & 0o777) == "0o755"
    # 텔레그램 봇 메모처럼 src 없는 줄이 섞여 있어도 읽고, 지우기는 web 것만
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "tg1", "time": day + "T08:00:00+09:00", "text": "봇 메모"}, ensure_ascii=False) + "\n")
    items = c.get(f"/api/diary?date={day}").get_json()["items"]
    assert [i["text"] for i in items] == ["봇 메모", "점심에 공원 산책\n날씨 좋음"]          # 시각 순
    assert c.post("/api/diary/delete", json={"date": day, "id": "tg1"}).status_code == 404
    assert c.post("/api/diary/delete", json={"date": day, "id": r["item"]["id"]}).get_json()["ok"]
    assert [i["text"] for i in c.get(f"/api/diary?date={day}").get_json()["items"]] == ["봇 메모"]
    assert c.get("/api/diary?date=../../etc").status_code == 400


def test_admin_only(dy, monkeypatch):
    c, base = dy
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.get("/diary", headers={"Accept": "text/html"}).status_code == 302        # 보기 전용은 못 봄
    assert c.get("/api/diary").status_code == 403
    assert c.post("/api/diary", json={"text": "x"}).status_code == 403
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    host = sorted(web_admin.REQUEST_PUBLIC_HOSTS)[0]
    assert c.get("/diary", headers={"Host": host}).status_code == 404                  # 공개 주소엔 없음
    monkeypatch.setattr(web_admin, "authed", lambda req: ("login", 302))
    assert c.post("/api/diary", json={"text": "x"}).status_code == 401


def test_shift_and_valid_date():
    assert dm.shift("2026-11-01", 1) == "2026-11-02" and dm.valid_date("2026-02-28") and not dm.valid_date("2026-02-30")


def test_telegram_memos_merged_read_only(dy):
    c, base = dy
    r = c.post("/api/diary", json={"text": "웹 메모"}).get_json()
    day = r["item"]["time"][:10]
    tg = base / f"{day}.tg.jsonl"
    tg.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"time": day + "T07:00:00+09:00", "text": "봇 아침", "src": "telegram"},
        {"time": day + "T23:59:00+09:00", "lat": 37.5, "lon": 127.0, "src": "telegram"},
        {"time": day + "T09:00:00+09:00", "src": "telegram"}]) + "\n", encoding="utf-8")
    before = tg.read_text(encoding="utf-8")
    items = c.get(f"/api/diary?date={day}").get_json()["items"]
    assert [(i.get("text"), i["src"], i.get("location")) for i in items] == [
        ("봇 아침", "telegram", None), ("웹 메모", "web", None), (None, "telegram", True)]
    assert all("id" not in i for i in items if i["src"] == "telegram")
    c.post("/api/diary", json={"text": "또 웹"})
    assert tg.read_text(encoding="utf-8") == before                    # MR은 .tg.jsonl을 안 씀
    assert "📍 위치" in c.get("/diary").get_data(as_text=True)


def test_life_group_menu_and_swipe_order(dy, monkeypatch):
    """생활 그룹(11-03): 가계부·일기 메모는 '생활'로, 작업엔 미디어 일만. 스와이프 순서는 생활 안에서 이어짐"""
    import re
    c, base = dy
    html = c.get("/downloads").get_data(as_text=True)
    assert 'class="icon-btn diary-btn" href="/diary"' in html                 # 상단 ✎ 일기 버튼은 그대로
    order = re.findall(r'href="([^"]+)"', re.search(r'<nav id="pageOrder".*?</nav>', html, re.S).group(0))
    assert order.index("/encode_log") + 1 == order.index("/recon") and order.index("/recon") + 1 == order.index("/diary")
    assert order.index("/diary") + 1 == order.index("/settings")
    nav = re.search(r'<nav class="bottomnav".*?</nav>', html, re.S).group(0)
    assert ">생활<" in nav.replace("</svg>", ">") and 'href="/recon"' in nav
    diary_html = c.get("/diary").get_data(as_text=True)
    assert "tab-swipe.js" in diary_html                                        # 일기 화면에서도 스와이프 됨
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    v = c.get("/downloads").get_data(as_text=True)
    assert "diary-btn" not in v and 'href="/recon"' not in v and ">생활<" not in v.replace("</svg>", ">")   # 보기 전용엔 생활 그룹 없음
