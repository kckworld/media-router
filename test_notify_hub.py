# -*- coding: utf-8 -*-
"""알림 허브(11-10): 켜기/끄기·채널·봇·카톡 방·기록, HA·n8n이 부르는 내부 주소, 화면 API"""
import json

import pytest

import notify_hub as nh
import web_admin


def fake_senders():
    tg, kk = [], []
    return tg, kk, (lambda t, c, text, photo, pm: tg.append((t, c, text, photo, pm)) or (True, "")), \
        (lambda room, poster, text: kk.append((room, poster, text)) or (True, ""))


def test_defaults_and_dispatch_channels(tmp_path):
    d = nh.load(tmp_path / "hub.json")
    assert "mr.request_done" in d["items"] and "n8n.weather_bot" in d["items"] and "n8n.hourly" not in d["items"]
    d["bots"]["synology_kck"] = {"name": "S", "token": "1:x" * 1}
    d["items"]["ha.power_report"]["chat_id"] = "-500"
    tg, kk, ts, ks = fake_senders()
    log = tmp_path / "log.sqlite3"
    tok = lambda b: {"synology_kck": "TOK", "mr": "MRTOK"}.get(b, "")
    r = nh.dispatch(d, "ha.power_report", "<b>전기</b>", parse_mode="HTML", bot_token_of=tok, tg_send=ts, kakao_send=ks, log_path=log)
    assert r["ok"] and tg == [("TOK", "-500", "<b>전기</b>", "", "HTML")] and not kk
    # 카톡으로 바꾸면 HTML 태그를 빼고 카톡 방으로
    assert nh.update_item(d, "ha.power_report", {"channel": "kakao"}) == "카톡으로 보내려면 카톡 방을 골라 주세요."
    assert nh.update_item(d, "ha.power_report", {"channel": "kakao", "kakao_room": "건이네가족"}) is None
    nh.dispatch(d, "ha.power_report", "<b>전기</b>&amp;", parse_mode="HTML", bot_token_of=tok, tg_send=ts, kakao_send=ks, log_path=log)
    assert kk == [("건이네가족", "", "전기&")]
    # 끄면 안 보내고 '꺼짐' 기록만
    nh.update_item(d, "ha.power_report", {"enabled": False})
    r = nh.dispatch(d, "ha.power_report", "x", bot_token_of=tok, tg_send=ts, kakao_send=ks, log_path=log)
    assert r["skipped"] and len(tg) == 1 and len(kk) == 1
    rows = nh.query_log(log, key="ha.power_report")
    assert [x["status"] for x in rows] == ["off", "ok", "ok"] and rows[1]["channel"] == "kakao"


def test_kakao_fallback_and_caller_targets(tmp_path):
    d = nh.load(tmp_path / "hub.json")
    tg = []
    ts = lambda t, c, text, photo, pm: tg.append(c) or (True, "")
    ks = lambda room, poster, text: (False, "창이 열려 있지 않음")
    tok = lambda b: "T"
    d["items"]["n8n.dongtan_weather"]["chat_id"] = "-917"
    r = nh.dispatch(d, "n8n.dongtan_weather", "날씨", bot_token_of=tok, tg_send=ts, kakao_send=ks, log_path=tmp_path / "l")
    assert r["ok"] and tg == ["-917"] and [x["ok"] for x in r["results"]] == [False, True]
    # 날씨봇: 받는 사람은 n8n이 정한다(caller_targets) — 잘못된 ID는 버림
    tg.clear()
    nh.dispatch(d, "n8n.weather_bot", "맑음", chat_ids=["111", "222", "x;rm"], bot_token_of=tok, tg_send=ts, kakao_send=ks)
    assert tg == ["111", "222"]
    # 받는 사람을 못 정하는 알림은 caller chat_ids를 무시
    tg.clear()
    d["items"]["n8n.ohtani"]["chat_id"] = "42"
    nh.dispatch(d, "n8n.ohtani", "선발", chat_ids=["999"], bot_token_of=tok, tg_send=ts, kakao_send=ks)
    assert tg == ["42"]


def test_duplicate_within_3min_not_resent(tmp_path, monkeypatch):
    """11-11: n8n 재시도로 같은 글이 3번 오면 한 번만 보내고 나머지는 'dup' 기록(실패한 건 다시 보낼 수 있음)"""
    monkeypatch.setattr(nh, "_recent", {})
    d = nh.load(tmp_path / "hub.json")
    sent = []
    ts = lambda t, c, text, photo, pm: sent.append(c) or (True, "")
    log = tmp_path / "log.sqlite3"
    for _ in range(3):
        r = nh.dispatch(d, "n8n.weather_bot", "맑음", chat_ids=["111"], bot_token_of=lambda b: "T", tg_send=ts,
                        kakao_send=None, log_path=log)
        assert r["ok"]
    assert sent == ["111"]
    assert [x["status"] for x in nh.query_log(log, key="n8n.weather_bot")] == ["dup", "dup", "ok"]
    nh.dispatch(d, "n8n.weather_bot", "흐림", chat_ids=["111"], bot_token_of=lambda b: "T", tg_send=ts, log_path=log)
    assert sent == ["111", "111"]                                   # 다른 글은 보냄
    monkeypatch.setattr(nh, "_recent", {})                          # 재시작해도 기록으로 막음
    nh.dispatch(d, "n8n.weather_bot", "흐림", chat_ids=["111"], bot_token_of=lambda b: "T", tg_send=ts, log_path=log)
    assert sent == ["111", "111"]
    fails = []
    bad = lambda t, c, text, photo, pm: fails.append(c) or (False, "x")
    nh.dispatch(d, "n8n.weather_bot", "비", chat_ids=["222"], bot_token_of=lambda b: "T", tg_send=bad, log_path=log)
    nh.dispatch(d, "n8n.weather_bot", "비", chat_ids=["222"], bot_token_of=lambda b: "T", tg_send=bad, log_path=log)
    assert fails == ["222", "222"]                                  # 실패는 다시 시도 허용


def test_validation_bots_rooms(tmp_path):
    d = nh.load(tmp_path / "hub.json")
    assert nh.set_bot(d, "mr", "x", "1:" + "a" * 35) and nh.set_bot(d, "Bad ID", "x", "1") and nh.set_bot(d, "fam", "가족", "bad")
    assert nh.set_bot(d, "fam", "가족", "12345:" + "a" * 35) is None
    assert nh.public_view(d)["bots"]["fam"] == {"name": "가족", "username": "", "token_tail": "…aaaa"}
    assert ("a" * 35) not in json.dumps(nh.public_view(d)["bots"])
    assert nh.update_item(d, "n8n.ohtani", {"bot": "nope"}) == "등록 안 된 봇이에요."
    assert nh.update_item(d, "n8n.ohtani", {"chat_id": "abc"}).startswith("대화방 ID")
    assert nh.update_item(d, "n8n.ohtani", {"kakao_room": "없는방"}).startswith("등록 안 된 카톡 방")
    d["items"]["n8n.ohtani"]["bot"] = "fam"
    assert nh.delete_bot(d, "fam").startswith("이 봇을 쓰는 알림")
    assert nh.set_rooms(d, ["김창기"]).startswith("쓰는 알림이 있는 방")      # 건이네가족은 기본 알림이 씀
    assert nh.set_rooms(d, ["건이네가족", " 새 방 ", "새 방"]) is None and d["rooms"] == ["건이네가족", "새 방"]


@pytest.fixture(autouse=True)
def _fresh_dedupe(monkeypatch):
    monkeypatch.setattr(nh, "_recent", {})


@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "NOTIFY_HUB_PATH", tmp_path / "hub.json")
    monkeypatch.setattr(web_admin, "NOTIFY_LOG_PATH", tmp_path / "log.sqlite3")
    monkeypatch.setattr(web_admin, "MEDIA_REQUEST_SETTINGS", tmp_path / "rq.json")
    monkeypatch.setattr(web_admin, "INTERNAL_KEY_PATH", tmp_path / "key")
    monkeypatch.setattr(web_admin, "_tg_cfg", lambda: {"enabled": True, "chat_id": "7", "bot_token": "MRTOK1234"})
    sent, kakao = [], []
    monkeypatch.setattr(web_admin, "_tg_call", lambda m, p, token=None: sent.append((m, p, token)) or
                        {"ok": True, "result": {"username": "fam_bot"}})
    monkeypatch.setattr(web_admin, "_kakao_send", lambda room, poster, text: kakao.append((room, poster, text)) or {"ok": True})
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), sent, kakao


def internal(c, body, key=None, host="192.168.50.153:5080"):
    return c.post("/api/internal/notify", json=body, headers={"X-Internal-Key": key or web_admin._internal_key(), "Host": host},
                  environ_base={"REMOTE_ADDR": "172.17.0.1"})


def test_internal_endpoint_auth_and_send(hub):
    c, sent, kakao = hub
    assert internal(c, {"key": "ha.power_report", "text": "x"}, key="wrong").status_code == 403
    assert internal(c, {"key": "ha.power_report", "text": "x"}, host="mr.kckworld.com").status_code == 404
    assert internal(c, {"key": "nope.key", "text": "x"}).status_code == 404
    # 처음 설정: 봇 synology_kck가 아직 없으면 실패로 기록(조용히 사라지지 않음)
    r = internal(c, {"key": "ha.power_report", "text": "전기"})
    assert r.status_code == 502 and "토큰" in r.get_json()["error"]
    d = web_admin._hub()
    nh.set_bot(d, "synology_kck", "S", "12345:" + "b" * 35)
    d["items"]["ha.power_report"]["chat_id"] = "-5442094917"
    nh.save(web_admin.NOTIFY_HUB_PATH, d)
    r = internal(c, {"key": "ha.power_report", "text": "<b>전기</b>", "parse_mode": "HTML"})
    assert r.status_code == 200 and sent[-1] == ("sendMessage", {"chat_id": "-5442094917", "text": "<b>전기</b>", "parse_mode": "HTML"},
                                                   "12345:" + "b" * 35)
    r = internal(c, {"key": "ha.power_report", "text": "뒤에서", "wait": False})
    assert r.status_code == 202
    assert internal(c, {"key": "ha.power_report", "text": "x", "photo": "file:///etc/passwd"}).status_code == 400


def test_page_api_and_masking(hub, monkeypatch):
    c, sent, kakao = hub
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    cfg = c.get("/api/notify/config").get_json()
    assert cfg["bots"]["mr"]["token_tail"] == "…1234" and "MRTOK" not in json.dumps(cfg)
    assert c.post("/api/notify/bot", json={"id": "fam", "name": "가족", "token": "12345:" + "c" * 35}).get_json()["ok"]
    cfg = c.get("/api/notify/config").get_json()
    assert cfg["bots"]["fam"]["username"] == "@fam_bot" and ("c" * 35) not in json.dumps(cfg)
    assert c.post("/api/notify/item/n8n.ohtani", json={"bot": "fam", "chat_id": "4242"}).get_json()["ok"]
    r = c.post("/api/notify/test/n8n.ohtani").get_json()
    assert r["ok"] and sent[-1][1]["chat_id"] == "4242" and sent[-1][2] == "12345:" + "c" * 35 and "[알림 허브 시험]" in sent[-1][1]["text"]
    assert c.post("/api/notify/item/n8n.ohtani", json={"enabled": False}).get_json()["ok"]
    assert c.post("/api/notify/test/n8n.ohtani").get_json()["skipped"]
    rows = c.get("/api/notify/log?key=n8n.ohtani").get_json()["rows"]
    assert [x["status"] for x in rows] == ["off", "ok"]
    assert c.get("/api/notify/log?q=시험&limit=abc").get_json()["ok"]
    html = c.get("/notify").get_data(as_text=True)
    assert 'id="ntItems"' in html and "보낸 기록" in html
    # MR 봇을 쓰는 알림이 없으면 봇 목록에서 뺀다(11-10 사용자)
    assert c.post("/api/notify/item/mr.request_done", json={"bot": "fam"}).get_json()["ok"]
    assert "mr" not in c.get("/api/notify/config").get_json()["bots"]


def test_first_load_migrates_request_kakao_setting(hub):
    web_admin.MEDIA_REQUEST_SETTINGS.write_text(json.dumps({"code": "123456", "kakao_on": True, "kakao_room": "김창기"}), encoding="utf-8")
    d = web_admin._hub()
    it = d["items"]["mr.request_done"]
    assert it["channel"] == "both" and it["kakao_room"] == "김창기"
    assert oct(web_admin.NOTIFY_HUB_PATH.stat().st_mode & 0o777) == "0o600"
