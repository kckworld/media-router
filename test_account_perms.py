# -*- coding: utf-8 -*-
"""계정 2단계 (2026-11-04): 계정별 메뉴 권한(none/view/edit)을 서버에서 강제, 소유자 전용 고정, 주의 메뉴 확인"""
import re

import pytest

import accounts
import menu
import web_admin


@pytest.fixture
def pm(monkeypatch, tmp_path):
    p = tmp_path / "accounts.json"
    monkeypatch.setattr(web_admin, "ACCOUNTS_PATH", p)
    monkeypatch.setattr(web_admin, "MENU_LAYOUT_PATH", tmp_path / "menu.json")
    monkeypatch.setattr(web_admin, "PASSWORD", "owner-pw-123")
    monkeypatch.setattr(web_admin, "VIEWER_PASSWORD", "")
    monkeypatch.setattr(web_admin, "_login_failures", {})
    monkeypatch.setattr(web_admin, "_login_global", {"fails": [], "locked_until": 0.0})
    accounts._cache.update(path=None, mtime=None, data=None)
    web_admin.app.config["TESTING"] = True
    owner = web_admin.app.test_client()
    owner.post("/login", data={"pw": "owner-pw-123"})
    assert accounts.add(p, "test", "시험", "test-pass-1") is None
    user = web_admin.app.test_client()
    user.post("/login", data={"uid": "test", "pw": "test-pass-1"})
    return owner, user, p


def nav_hrefs(html):
    nav = re.search(r'<nav id="pageOrder".*?</nav>', html, re.S).group(0)
    return re.findall(r'href="([^"]+)"', nav)


def test_journal_view_only(pm, tmp_path, monkeypatch):
    owner, user, p = pm
    monkeypatch.setattr(web_admin, "DRAFT_DIR", tmp_path / "drafts")
    (tmp_path / "drafts").mkdir()
    perms = {k: "none" for k in menu.PAGES if not menu.PAGES[k].get("owner_only")}
    perms["journal"] = "view"
    r = owner.post("/api/accounts", json={"action": "perms", "id": "test", "perms": perms})
    assert r.status_code == 409 and r.get_json()["need_confirm"] and "일기" in r.get_json()["caution"]   # 개인정보 확인
    assert owner.post("/api/accounts", json={"action": "perms", "id": "test", "perms": perms, "confirm": True}).get_json()["ok"]
    html = user.get("/journal").get_data(as_text=True)
    assert nav_hrefs(html) == ["/journal"]                                     # 메뉴엔 일기만
    assert user.get("/downloads", headers={"Accept": "text/html"}).headers["Location"].endswith("/journal")
    assert user.get("/downloads/data").status_code == 403
    assert user.get("/api/journal").status_code == 200
    assert user.post("/api/journal/save", json={"date": "x", "title": "a", "body": "b"}).status_code == 403   # 보기만
    assert user.post("/api/diary", json={"text": "x"}).status_code == 403
    assert user.post("/run_batch").status_code == 403
    assert user.post("/wake_pc").status_code == 403
    assert "readonly" in html.split("<body", 1)[1][:60]


def test_edit_permission_allows_post(pm, tmp_path, monkeypatch):
    owner, user, p = pm
    monkeypatch.setattr(web_admin, "DIARY_DIR", tmp_path / "memos")
    owner.post("/api/accounts", json={"action": "perms", "id": "test", "perms": {"journal": "edit"}, "confirm": True})
    assert user.post("/api/diary", json={"text": "메모"}).get_json()["ok"]
    assert "readonly" not in user.get("/journal").get_data(as_text=True).split("<body", 1)[1][:60]
    assert user.get("/downloads").status_code == 200                           # 안 정한 메뉴는 기본(보기)


def test_owner_only_cannot_be_given(pm):
    owner, user, p = pm
    r = owner.post("/api/accounts", json={"action": "perms", "id": "test", "perms": {"people": "view", "settings": "edit"}})
    assert r.status_code == 400 and "소유자 전용" in r.get_json()["error"]
    accounts.set_perms(p, "test", {"people": "edit", "accounts": "edit", "skills": "edit"})    # 파일을 직접 고쳐도
    for path in ("/api/people/search", "/api/accounts", "/api/skills/install", "/settings/claude_login"):
        assert user.post(path, json={}).status_code == 403
    assert user.get("/settings", headers={"Accept": "text/html"}).status_code == 302
    assert "/people" not in nav_hrefs(user.get("/downloads").get_data(as_text=True))


def test_default_like_old_viewer(pm):
    owner, user, p = pm
    hrefs = nav_hrefs(user.get("/downloads").get_data(as_text=True))
    assert "/downloads" in hrefs and "/requests" not in hrefs and "/recon" not in hrefs and "/journal" not in hrefs
    assert user.post("/check", data={}).status_code == 403
    owner_hrefs = nav_hrefs(owner.get("/downloads").get_data(as_text=True))
    assert "/accounts" in owner_hrefs and "/people" in owner_hrefs


def test_every_route_belongs_to_a_page_or_is_public():
    """새 화면을 만들고 menu.py에 안 넣으면 여기서 걸린다(일반 계정 권한 검사에서 빠지지 않게)"""
    public = ("/", "/request", "/survey", "/popular", "/login", "/logout", "/health", "/static", "/favicon.ico",
              "/apple-touch-icon", "/api/internal/kakao-send", "/pc_status", "/schedule_pc_shutdown/status",
              "/people/reauth")
    missing = []
    for rule in web_admin.app.url_map.iter_rules():
        path = re.sub(r"<[^>]+>", "x", rule.rule)
        if path.startswith("/tf/x") or path.startswith("/api/tf/x"):
            path = path.replace("/tf/x", "/tf/drama")
        if any(path == pub or path.startswith(pub + "/") or (pub != "/" and path.startswith(pub)) for pub in public):
            continue
        if menu.page_for(path) is None:
            missing.append(rule.rule)
    assert missing == [], f"menu.py에 없는 주소: {missing}"
