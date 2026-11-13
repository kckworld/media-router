# -*- coding: utf-8 -*-
"""생활 > 고정비·택배 (2026-11-13): 호스트가 만든 JSON 보여 주기, 사용자 수정(*_rules.json) 뒤 호스트 다시 계산, 소유자만"""
import json

import pytest

import accounts
import web_admin


@pytest.fixture
def lf(monkeypatch, tmp_path):
    d = tmp_path / "card_recon"
    d.mkdir()
    (d / "fixed.json").write_text(json.dumps({"per_month": 100, "per_year": 1200, "month_total": 100, "year_only_total": 0,
                                              "items": [{"key": "kt", "name": "KT", "amount": 100, "cycle": "month", "day": 21,
                                                         "status": "due", "next_date": "2026-11-21", "excluded": False}]}), encoding="utf-8")
    monkeypatch.setattr(web_admin, "RECON_DIR", d)
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "current_user", lambda req: accounts.OWNER)
    calls = []
    monkeypatch.setattr(web_admin, "_life_refresh", lambda kind: (calls.append(kind), (True, ""))[1])
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), d, calls


def test_pages_render(lf):
    c, d, _ = lf
    assert 'id="fxList"' in c.get("/fixed").get_data(as_text=True)
    assert 'id="pcBody"' in c.get("/parcels").get_data(as_text=True) and "setupShopping" in c.get("/parcels").get_data(as_text=True)
    assert c.get("/api/fixed").get_json()["data"]["per_month"] == 100
    assert c.get("/api/parcels").get_json()["data"] is None          # 아직 메일 없음


def test_fixed_rules(lf):
    c, d, calls = lf
    assert c.post("/api/fixed/rules", json={"action": "exclude", "key": "kt"}).get_json()["ok"]
    r = json.loads((d / "fixed_rules.json").read_text(encoding="utf-8"))
    assert r["exclude"] == ["kt"] and calls == ["fixed"]
    c.post("/api/fixed/rules", json={"action": "include", "key": "kt"})
    assert json.loads((d / "fixed_rules.json").read_text(encoding="utf-8"))["exclude"] == []
    assert c.post("/api/fixed/rules", json={"action": "add", "name": "넷플릭스", "amount": "17,000", "cycle": "year",
                                            "month": 3, "day": 5}).get_json()["ok"]
    m = json.loads((d / "fixed_rules.json").read_text(encoding="utf-8"))["manual"]
    assert m[0]["name"] == "넷플릭스" and m[0]["amount"] == 17000 and m[0]["month"] == 3
    c.post("/api/fixed/rules", json={"action": "delete", "id": m[0]["id"]})
    assert json.loads((d / "fixed_rules.json").read_text(encoding="utf-8"))["manual"] == []
    for bad in ({"action": "add", "name": "x", "amount": "abc", "day": 1}, {"action": "add", "name": "", "amount": 1, "day": 1},
                {"action": "add", "name": "x", "amount": 1, "day": 40}, {"action": "zzz"}, {"action": "exclude"}):
        assert c.post("/api/fixed/rules", json=bad).status_code == 400


def test_parcels_hide_and_refresh(lf, monkeypatch):
    c, d, calls = lf
    assert c.post("/api/parcels/hide", json={"id": "np:123"}).get_json()["ok"]
    assert json.loads((d / "parcels_rules.json").read_text(encoding="utf-8"))["hidden"] == ["np:123"]
    c.post("/api/parcels/hide", json={"id": "np:123", "hidden": False})
    assert json.loads((d / "parcels_rules.json").read_text(encoding="utf-8"))["hidden"] == []
    assert c.post("/api/parcels/hide", json={}).status_code == 400
    assert c.post("/api/parcels/refresh").get_json()["ok"] and calls[-1] == "parcels"
    monkeypatch.setattr(web_admin, "_life_refresh", lambda kind: (False, "실패"))
    assert c.post("/api/fixed/refresh").status_code == 502


def test_owner_only(lf, monkeypatch):
    c, _, _ = lf
    monkeypatch.setattr(web_admin, "current_user", lambda req: "someone")
    monkeypatch.setattr(web_admin, "_req_user_perms", lambda req: ("someone", {}))
    for url in ("/api/fixed", "/api/parcels"):
        assert c.get(url).status_code in (302, 403)
    assert c.post("/api/fixed/rules", json={"action": "exclude", "key": "kt"}).status_code in (302, 403)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.post("/api/parcels/hide", json={"id": "x"}).status_code in (302, 403)


def test_fixed_categories(lf):
    # 11-13: 항목별 카테고리, 카테고리 추가·삭제, 화면에 이번 달 남은 고정비·보기 전환
    c, d, _ = lf
    rules = lambda: json.loads((d / "fixed_rules.json").read_text(encoding="utf-8"))
    assert c.post("/api/fixed/rules", json={"action": "category", "key": "kt", "category": "통신비"}).get_json()["ok"]
    assert rules()["categories"] == {"kt": "통신비"} and rules()["cat_list"] == ["통신비"]
    c.post("/api/fixed/rules", json={"action": "add_category", "name": "차량"})
    assert rules()["cat_list"] == ["통신비", "차량"]
    c.post("/api/fixed/rules", json={"action": "del_category", "name": "통신비"})
    assert rules()["cat_list"] == ["차량"] and rules()["categories"] == {}           # 그 카테고리 항목은 자동 추정으로
    c.post("/api/fixed/rules", json={"action": "category", "key": "kt", "category": "차량"})
    c.post("/api/fixed/rules", json={"action": "category", "key": "kt", "category": ""})
    assert rules()["categories"] == {}
    assert c.post("/api/fixed/rules", json={"action": "add_category", "name": " "}).status_code == 400
    c.post("/api/fixed/rules", json={"action": "exclude", "key": "kt"})
    assert rules()["cat_list"] == ["차량"]                                             # 다른 동작이 카테고리를 안 지움
    html = c.get("/fixed").get_data(as_text=True)
    assert "이번 달 남은 고정비" in html and 'id="vCat"' in html and "data-cat=" in html and "add_category" in html


def test_fixed_force_and_manual_match(lf):
    # 11-13: 후보를 고정비로 넣기/빼기, 직접 추가에 찾을 글자·금액 범위(쿠팡 와우 50% 할인)
    c, d, _ = lf
    rules = lambda: json.loads((d / "fixed_rules.json").read_text(encoding="utf-8"))
    c.post("/api/fixed/rules", json={"action": "exclude", "key": "학원"})
    assert c.post("/api/fixed/rules", json={"action": "force", "key": "학원"}).get_json()["ok"]
    assert rules()["force"] == ["학원"] and rules()["exclude"] == []                 # 넣으면 제외도 풀림
    c.post("/api/fixed/rules", json={"action": "exclude", "key": "학원"})
    assert rules()["force"] == [] and rules()["exclude"] == ["학원"]                 # 제외하면 넣은 것도 풀림
    c.post("/api/fixed/rules", json={"action": "force", "key": "학원"})
    c.post("/api/fixed/rules", json={"action": "unforce", "key": "학원"})
    assert rules()["force"] == []
    r = c.post("/api/fixed/rules", json={"action": "add", "name": "쿠팡 와우", "amount": "7890", "day": 24,
                                         "match": "쿠팡(쿠페이)", "min": "3,945", "max": "7890"})
    assert r.get_json()["ok"]
    m = rules()["manual"][0]
    assert m["match"] == "쿠팡(쿠페이)" and m["min"] == 3945 and m["max"] == 7890
    assert c.post("/api/fixed/rules", json={"action": "add", "name": "x", "amount": 1, "day": 1, "min": 9, "max": 3}).status_code == 400
    html = c.get("/fixed").get_data(as_text=True)
    assert 'id="fxCd"' in html and "data-force=" in html and 'id="adMatch"' in html and "금액 변동" in html


def test_parcels_received_and_edit(lf):
    c, d, calls = lf
    rules = lambda: json.loads((d / "parcels_rules.json").read_text(encoding="utf-8"))
    assert c.post("/api/parcels/received", json={"id": "G마켓:1"}).get_json()["ok"]
    assert "G마켓:1" in rules()["received"] and calls[-1] == "parcels"
    assert c.post("/api/parcels/edit", json={"id": "G마켓:1", "item": "양말", "total": "11,900", "shop": "G마켓", "eta": "2026-11-15"}).get_json()["ok"]
    assert rules()["edits"]["G마켓:1"] == {"item": "양말", "shop": "G마켓", "eta": "2026-11-15", "total": 11900}
    assert "G마켓:1" in rules()["received"]                                       # 수정해도 받음 표시는 남음
    c.post("/api/parcels/received", json={"id": "G마켓:1", "received": False})
    assert rules()["received"] == {} and "G마켓:1" in rules()["edits"]
    c.post("/api/parcels/hide", json={"id": "G마켓:1"})
    assert rules()["hidden"] == ["G마켓:1"] and "G마켓:1" in rules()["edits"]       # 숨겨도 수정은 남음
    c.post("/api/parcels/edit", json={"id": "G마켓:1"})
    assert rules()["edits"] == {}                                                  # 다 비우면 고친 것 지움
    for bad in ({"id": "x", "total": "abc"}, {"id": "x", "eta": "11/15"}, {"item": "x"}):
        assert c.post("/api/parcels/edit", json=bad).status_code == 400
    html = c.get("/parcels").get_data(as_text=True)
    assert "data-got=" in html and "data-edit=" in html and "/api/parcels/edit" in html
    assert "o.invoice" in html and "pc-inv" in html                              # 택배사·송장번호(11-13)


def test_run_all_life_refresh_command():
    s = open("run_all.sh", encoding="utf-8").read()
    assert '"life-refresh fixed"|"life-refresh parcels")' in s and "fixed_costs.py" in s and "parcels.py" in s
