# -*- coding: utf-8 -*-
"""가계부 대조 화면 (2026-11-03): 관리자만, 결과 JSON 보여 주기, [다시 비교]는 호스트 SSH"""
import json
import time

import pytest

import web_admin


@pytest.fixture
def rc(monkeypatch, tmp_path):
    d = tmp_path / "card_recon"
    d.mkdir()
    (d / "2026-09.json").write_text(json.dumps({"month": "2026-09", "groups": []}), encoding="utf-8")
    (d / "2026-10.json").write_text(json.dumps({"month": "2026-10", "mmbak_time": "t", "groups": [
        {"key": "k", "name": "카드", "status": "diff", "compare_total": 10, "ledger_total": 7, "diff": 3,
         "statement_only": [{"date": "2026-10-01", "card": "c", "merchant": "m", "pay": 3}], "ledger_only": [], "excluded": []}],
        "check_card_in_ledger": []}), encoding="utf-8")
    (d / "bad.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(web_admin, "RECON_DIR", d)
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setitem(web_admin._recon_run, "running", False)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client()


def test_page_and_data(rc):
    html = rc.get("/recon").get_data(as_text=True)
    assert "다시 비교" in html and "2026년 10월 사용분" in html and "구글 드라이브 백업" in html
    d = rc.get("/api/recon").get_json()
    assert d["months"] == ["2026-10", "2026-09"] and d["month"] == "2026-10"      # 기본은 최근 달
    assert d["data"]["groups"][0]["status"] == "diff"
    assert rc.get("/api/recon?month=2026-09").get_json()["data"]["month"] == "2026-09"
    assert rc.get("/api/recon?month=../x").get_json()["data"] is None


def test_viewer_and_public_blocked(rc, monkeypatch):
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert rc.get("/api/recon").status_code == 403
    assert rc.get("/recon", headers={"Accept": "text/html"}).status_code == 302
    assert rc.post("/api/recon/run", json={"month": "2026-10"}).status_code == 403
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    host = sorted(web_admin.REQUEST_PUBLIC_HOSTS)[0]
    assert rc.get("/recon", headers={"Host": host}).status_code == 404


def test_run_calls_host_once(rc, monkeypatch, tmp_path):
    key = tmp_path / "key"
    key.write_text("x")
    monkeypatch.setattr(web_admin, "RUN_BATCH_SSH_KEY", str(key))
    calls = []

    class R:
        stdout = '{"ok": true}\n'

    def fake_run(args, **kw):
        calls.append(args[-1])
        time.sleep(0.2)
        return R()
    monkeypatch.setattr(web_admin.subprocess, "run", fake_run)
    assert rc.post("/api/recon/run", json={"month": "2026-1x"}).status_code == 400
    assert rc.post("/api/recon/run", json={"month": "2026-10"}).get_json()["ok"]
    assert rc.post("/api/recon/run", json={"month": "2026-10"}).status_code == 409        # 도는 중엔 한 번만
    for _ in range(50):
        if not web_admin._recon_run["running"]:
            break
        time.sleep(0.05)
    assert calls == ["card-recon 2026-10"]
    run = rc.get("/api/recon").get_json()["run"]
    assert run["ok"] is True and not run["running"] and run["month"] == "2026-10"


def test_discount_fields_shown(rc):
    # 11-07: 청구할인 합계·가계부 할인·안내는 필드 있는 카드만(현대·KB·옛 JSON은 안 그림) — 그리는 건 화면 JS
    d = rc.get("/api/recon").get_json()["data"]
    assert "discount_total" not in d["groups"][0]                            # 옛 JSON 그대로 통과(안 깨짐)
    html = rc.get("/recon").get_data(as_text=True)
    assert "function discLine(g)" in html and "g.discount_total == null" in html and "g.discount_hint" in html
    assert "청구할인" in html and "가계부 할인" in html
    assert html.count("discLine(g)") == 2                                      # 정의 + 카드 줄에서 호출


def test_separate_charges_shown(rc):
    # 11-09: 우리 …741(용돈계좌) 후불교통은 가계부 대조에서 빼고 '이번 결제 합계'와 함께 표시만
    html = rc.get("/recon").get_data(as_text=True)
    assert "function sepLine(g)" in html and "g.separate_charges" in html and "g.withdraw_total" in html
    assert html.count("sepLine(g)") == 2 and "가계부 제외" in html


def test_transfer_and_min_spend_ui(rc):
    # 11-13: 현대카드 카드별 이체 합계 검증 + 카드별 최소 사용액
    html = rc.get("/recon").get_data(as_text=True)
    assert "function transferLine(g)" in html and "function transferBody(g)" in html and "transferLine(g) +" in html
    assert 'id="msCard"' in html and "최소액 설정" in html and "/api/recon/min_spend" in html


def test_min_spend_save_and_read(rc):
    d = web_admin.RECON_DIR
    (d / "spend.json").write_text(json.dumps({"month": "2026-11", "assets": {"현대카드 기아차": 120000}}), encoding="utf-8")
    assert rc.get("/api/recon").get_json()["min_spend"] == {}
    r = rc.post("/api/recon/min_spend", json={"assets": {"현대카드 기아차": "300,000", "우리카드": 0}})
    assert r.get_json() == {"ok": True, "assets": {"현대카드 기아차": 300000}}        # 0·빈칸은 안 씀
    j = rc.get("/api/recon").get_json()
    assert j["min_spend"] == {"현대카드 기아차": 300000} and j["spend"]["assets"]["현대카드 기아차"] == 120000
    assert rc.post("/api/recon/min_spend", json={"assets": {"x": "abc"}}).status_code == 400
    assert rc.post("/api/recon/min_spend", json={"assets": {"x": -1}}).status_code == 400
    assert rc.post("/api/recon/min_spend", json={"assets": []}).status_code == 400
    assert json.loads((d / "min_spend.json").read_text(encoding="utf-8"))["assets"] == {"현대카드 기아차": 300000}


def test_min_spend_viewer_blocked(rc, monkeypatch):
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert rc.post("/api/recon/min_spend", json={"assets": {"a": 1}}).status_code == 403
