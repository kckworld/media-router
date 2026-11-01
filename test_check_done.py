# -*- coding: utf-8 -*-
"""체크 목록의 '받은 것도 보기' (2026-11-01): 받음 처리된 항목은 따로, 기본 숨김, 체크박스 없이 나온다."""
import web_admin


def _rules():
    return [
        {"id": 1, "category": "예능", "pattern": "*런닝맨*", "subfolder": "런닝맨", "days": ["월"], "updated_map": {"월": "Y"}},
        {"id": 2, "category": "드라마", "pattern": "*신병*", "subfolder": "신병", "days": ["월"], "updated_map": {"월": "N"}},
        {"id": 3, "category": "예능", "pattern": "*놀면*", "subfolder": "놀면뭐하니", "days": ["화"], "updated_map": {"화": "Y"}},
    ]


def test_check_entries_splits_done(monkeypatch):
    monkeypatch.setattr(web_admin, "get_next_episode", lambda r: None)
    pending, _ = web_admin._check_entries(_rules(), "월")
    done, _ = web_admin._check_entries(_rules(), "월", done=True)
    assert [e["subfolder"] for e in pending] == ["신병"]
    assert [e["subfolder"] for e in done] == ["런닝맨"]     # 다른 요일(화) 받은 건 안 나옴


def test_today_page_has_hidden_done_list(monkeypatch):
    monkeypatch.setattr(web_admin, "get_next_episode", lambda r: None)
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "load_cfg", lambda: {"rules": _rules()})
    web_admin.app.config["TESTING"] = True
    html = web_admin.app.test_client().get("/?mode=check&day=월").get_data(as_text=True)
    assert 'id="doneToggle"' in html and "받은 것도 보기 (1)" in html
    assert '<ul class="list done-list" id="doneList" hidden>' in html
    done_part = html.split('id="doneList"', 1)[1].split("</ul>", 1)[0]
    assert "런닝맨" in done_part and 'name="rkday"' not in done_part   # 받음 처리·전체 선택에 안 섞임
    assert "신병" not in done_part


def test_no_toggle_when_nothing_done(monkeypatch):
    monkeypatch.setattr(web_admin, "get_next_episode", lambda r: None)
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "load_cfg", lambda: {"rules": _rules()[1:2]})
    web_admin.app.config["TESTING"] = True
    html = web_admin.app.test_client().get("/?mode=check&day=월").get_data(as_text=True)
    assert 'id="doneToggle"' not in html and "신병" in html
