# -*- coding: utf-8 -*-
"""단톡방 설문 (2026-11-02): 공개 /survey + 관리자 /surveys"""
import json

import pytest

import media_requests
import media_survey
import web_admin


@pytest.fixture
def sv(monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "SURVEY_PATH", tmp_path / "survey.json")
    monkeypatch.setattr(web_admin, "SURVEY_SETTINGS", tmp_path / "survey_settings.json")
    monkeypatch.setattr(web_admin, "MEDIA_REQUEST_SETTINGS", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps({"code": "1234"}), encoding="utf-8")
    monkeypatch.setattr(web_admin, "_sv_submit_limit", media_requests.RateLimiter(5, 3600))
    monkeypatch.setattr(web_admin, "_sv_submit_total", media_requests.RateLimiter(60, 3600))
    monkeypatch.setattr(web_admin, "_rq_code_fail_limit", media_requests.RateLimiter(10, 3600))
    monkeypatch.setattr(web_admin, "_rq_code_fail_total", media_requests.RateLimiter(50, 3600))
    sent = []
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, markup=None, token=None: sent.append(text) or True)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), sent, tmp_path


GOOD = {"code": "1234", "name": "홍길동", "app": "둘 다", "plex_id": "hong", "jellyfin_id": "hong2",
        "otts": ["넷플릭스", "티빙", "가짜"], "ott_etc": "", "reason": "OTT에 없는 걸 보려고", "reason_etc": "",
        "genres": ["드라마", "예능"], "recommend": "굿보이", "pains": ["자막"], "pain_etc": "",
        "improve": "", "say": "고마워요", "website": ""}


def saved(tmp_path):
    return json.loads((tmp_path / "survey.json").read_text(encoding="utf-8"))


def test_page_and_submit(sv):
    c, sent, tmp_path = sv
    html = c.get("/survey").get_data(as_text=True)
    assert "본인 이름" in html and "단톡방 이름이 아니라 내 이름을" in html and 'store("rq_name")' not in html
    assert "kckworld 설문" in html and 'id="svForm"' in html and "유튜브 프리미엄" in html and "해외드라마" in html
    r = c.post("/survey/submit", json=GOOD).get_json()
    assert r == {"ok": True}
    it = saved(tmp_path)[0]
    assert it["name"] == "홍길동" and it["otts"] == ["넷플릭스", "티빙"] and it["plex_id"] == "hong" and it["times"] == 1
    assert sent and sent[0].startswith("📝 설문 새 답: 홍길동") and "OTT 넷플릭스, 티빙" in sent[0]


def test_only_name_required_and_same_name_overwrites(sv):
    c, sent, tmp_path = sv
    assert c.post("/survey/submit", json={"code": "1234", "name": " "}).status_code == 400
    assert c.post("/survey/submit", json={"code": "1234", "name": "홍길동"}).get_json()["ok"]
    assert c.post("/survey/submit", json=dict(GOOD, name="홍 길동", app="Plex")).get_json()["ok"]
    items = saved(tmp_path)
    assert len(items) == 1 and items[0]["times"] == 2 and items[0]["app"] == "Plex"
    assert items[0]["jellyfin_id"] == ""                         # Plex만 고르면 Jellyfin 아이디는 버림
    assert "답 고침" in sent[-1]


def test_security_code_honeypot_rate_and_closed(sv, monkeypatch):
    c, sent, tmp_path = sv
    assert c.post("/survey/submit", json=dict(GOOD, code="9999")).status_code == 403
    assert c.post("/survey/submit", json=dict(GOOD, website="bot")).get_json() == {"ok": True}
    assert not (tmp_path / "survey.json").exists()                 # 봇 답은 안 남김
    for i in range(5):
        assert c.post("/survey/submit", json=dict(GOOD, name=f"사람{i}")).status_code == 200
    assert c.post("/survey/submit", json=dict(GOOD, name="사람9")).status_code == 429
    (tmp_path / "survey_settings.json").write_text(json.dumps({"open": False}), encoding="utf-8")
    assert "설문이 끝났어요" in c.get("/survey").get_data(as_text=True)
    assert c.post("/survey/submit", json=GOOD).status_code == 403


def test_public_host_serves_survey_only(sv, monkeypatch):
    c, sent, tmp_path = sv
    host = sorted(web_admin.REQUEST_PUBLIC_HOSTS)[0]
    assert c.get("/survey", headers={"Host": host}).status_code == 200
    assert c.post("/survey/submit", json=GOOD, headers={"Host": host}).status_code == 200
    assert c.get("/surveys", headers={"Host": host}).status_code == 404
    assert c.get("/api/surveys", headers={"Host": host}).status_code == 404


def test_admin_stats_csv_merge_delete_and_viewer_mask(sv, monkeypatch):
    c, sent, tmp_path = sv
    c.post("/survey/submit", json=GOOD)
    c.post("/survey/submit", json={"code": "1234", "name": "홍길똥", "improve": "자막 싱크", "pain_etc": "느림"})
    monkeypatch.setattr(web_admin, "authed", lambda req: ("login", 302))
    assert c.get("/api/surveys").status_code == 401
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    d = c.get("/api/surveys").get_json()
    assert d["stats"]["total"] == 2
    ott = dict(next(x for x in d["stats"]["chips"] if x["title"] == "보고 있는 OTT")["rows"])
    assert ott["넷플릭스"] == 1 and ott["왓챠"] == 0
    pains = dict(next(x for x in d["stats"]["chips"] if x["title"] == "불편했던 점")["rows"])
    assert pains["기타"] == 1 and pains["자막"] == 1
    csv_text = c.get("/surveys.csv").get_data(as_text=True)
    assert csv_text.startswith("﻿제출 시각,이름") and "홍길동" in csv_text
    assert "설문" in c.get("/surveys").get_data(as_text=True)
    ids = [it["id"] for it in d["items"]]
    keep = next(it["id"] for it in d["items"] if it["name"] == "홍길동")
    assert c.post("/api/surveys/merge", json={"ids": ids, "keep": keep}).get_json()["ok"]
    items = saved(tmp_path)
    assert len(items) == 1 and items[0]["improve"] == "자막 싱크" and items[0]["app"] == "둘 다" and items[0]["times"] == 2
    # 보기 전용: 이름·아이디 가림, 바꾸기는 막힘(_readonly_guard)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    v = c.get("/api/surveys").get_json()
    assert v["items"][0]["name"] == "홍**" and v["items"][0]["plex_id"] == "(있음)"
    assert "홍길동" not in c.get("/surveys.csv").get_data(as_text=True)
    assert c.post("/api/surveys/delete", json={"ids": [keep]}).status_code == 403
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    assert c.post("/api/surveys/delete", json={"ids": [keep]}).get_json()["deleted"] == 1
    assert saved(tmp_path) == []
    assert c.post("/api/surveys/open", json={"open": False}).get_json()["open"] is False
    assert not web_admin._survey_open()


def test_validate_none_chip_exclusive():
    d, _ = media_survey.validate({"name": "a", "otts": ["없음", "넷플릭스"], "pains": ["없음"], "pain_etc": "x",
                                  "reason": "둘 다 조금씩", "reason_etc": "직접"})
    assert d["otts"] == ["넷플릭스"] and d["pains"] == [] and d["reason"] == "" and d["reason_etc"] == "직접"
