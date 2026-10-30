# -*- coding: utf-8 -*-
"""작업 > 드라마·예능 (티프리카) 테스트. 티프리카·TMDB·qBittorrent는 가짜로."""
import json

import pytest

import tf_shows
import web_admin


def test_parse_title():
    p = tf_shows.parse_title("닥터X - 하얀 마피아의 시대.E05.261023.720p-NEXT")
    assert p == {"show": "닥터X - 하얀 마피아의 시대", "ep": 5, "air": "261023", "release": "NEXT"}
    p = tf_shows.parse_title("나의 유죄인간.Love In Disguise.E04.1080p.NF.WEB-DL.AAC2.0.H.264-MrHulk")
    assert p["show"] == "나의 유죄인간" and p["ep"] == 4 and p["release"] == "MrHulk"
    assert tf_shows.parse_title("용감한 형사들 5.E27.261030.720p-NEXT")["show"] == "용감한 형사들 5"


LIST_HTML = '''<!--베스트--> <a href="board.php?mode=view&b_id=tent&id=11" title="런닝맨.E826.261026.720p-NEXT">x</a>
<a href="board.php?mode=view&b_id=tent&id=12" title="런닝맨.E825.261019.720p-NEXT">x</a>
<a href="board.php?mode=view&b_id=tent&id=13" title="나 혼자 산다.E670.261023.720p-NEXT">x</a> 주간 베스트
<a href="board.php?mode=view&b_id=tent&id=21&page=1" class="stitle1"> 라디오 스타.E987.261028.720p-NEXT </a><td class="datetime">10-29</td>'''


def test_best_dedupes_same_show(monkeypatch):
    monkeypatch.setattr(tf_shows.movies, "_fetch", lambda url: LIST_HTML if "board.php" in url else "")
    best = tf_shows.fetch_best("variety")
    assert [m["id"] for m in best["day"]] == ["11", "13"]
    assert tf_shows.parse_list(LIST_HTML, "tent") == [{"id": "21", "title": "라디오 스타.E987.261028.720p-NEXT", "when": "10-29"}]


def test_subscribed_and_rule():
    lines = ["나는SOLO 720p", "연애박사 | Sniper"]
    assert tf_shows.subscribed("나는 SOLO.E276.261029.720p-NEXT", lines) == "나는SOLO 720p"
    assert tf_shows.subscribed("연애박사.E08.261027.720p-NEXT", lines) == "연애박사 | Sniper"
    assert tf_shows.subscribed("런닝맨.E826", lines) is None
    rules = [{"category": "예능", "pattern": "런닝맨", "subfolder": "런닝맨 (2010)"},
             {"category": "드라마", "pattern": "런닝맨", "subfolder": "x"},
             {"category": "예능", "pattern": "놀면", "pattern2": "뭐하니", "exclude_pattern": "1080p", "subfolder": "놀면 뭐하니"}]
    assert tf_shows.rule_for("런닝맨.E826.261026.720p-NEXT", rules, "예능")["subfolder"] == "런닝맨 (2010)"
    assert tf_shows.rule_for("놀면 뭐하니.E349.720p-NEXT", rules, "예능")["subfolder"] == "놀면 뭐하니"
    assert tf_shows.rule_for("놀면 뭐하니.E349.1080p", rules, "예능") is None
    assert tf_shows.rule_for("라디오 스타.E987", rules, "예능") is None


@pytest.fixture
def tf_env(monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "TF_ADDED_PATH", tmp_path / "added.json")
    monkeypatch.setattr(web_admin, "TF_TMDB_CACHE", tmp_path / "tmdb.json")
    monkeypatch.setattr(web_admin, "get_tmdb_api_key", lambda: "")
    kw_v, kw_d = tmp_path / "bt4g_keywords.txt", tmp_path / "drama_keywords.txt"
    kw_v.write_text("나는SOLO 720p\n", encoding="utf-8")
    kw_d.write_text("연애박사 | Sniper\n", encoding="utf-8")
    monkeypatch.setattr(web_admin, "BT4G_KEYWORDS_PATH", kw_v)
    monkeypatch.setattr(web_admin, "DRAMA_KEYWORDS_PATH", kw_d)
    monkeypatch.setattr(web_admin, "load_cfg", lambda: {"rules": [{"category": "예능", "pattern": "런닝맨", "subfolder": "런닝맨 (2010)"}]})
    monkeypatch.setattr(tf_shows, "fetch_best", lambda kind: {"day": [{"id": "11", "title": "런닝맨.E826.261026.720p-NEXT"}], "week": []})
    monkeypatch.setattr(tf_shows, "fetch_list", lambda kind, page: [{"id": "21", "title": "라디오 스타.E987.261028.720p-NEXT", "when": "10-29"}])
    monkeypatch.setattr(tf_shows, "fetch_magnet", lambda kind, pid: ("a" * 40, "런닝맨.E826.261026.720p-NEXT"))
    web_admin._movie_cache["at"].clear()
    added = []
    monkeypatch.setattr(web_admin, "_qbit_torrents", lambda hashes: {})
    monkeypatch.setattr(web_admin, "_qbit_get", lambda path, params=None: {"예능": {"savePath": "/downloads/예능", "assignedCategory": ""}})
    monkeypatch.setattr(web_admin, "_qbit_post", lambda path, data: added.append((path, data)))
    web_admin.app.config["TESTING"] = True
    return {"client": web_admin.app.test_client(), "added": added, "kw_v": kw_v, "kw_d": kw_d}


def test_list_marks_rule_and_subscription(tf_env):
    d = tf_env["client"].get("/api/tf/variety").get_json()
    assert d["ok"] and d["b_id"] == "tent"
    m = d["day"][0]
    assert m["show"] == "런닝맨" and m["ep"] == 826 and m["rule"] == "런닝맨 (2010)" and m["subscribed"] is None
    assert d["list"][0]["rule"] is None
    html = tf_env["client"].get("/tf/drama").get_data(as_text=True)
    assert "티프리카 드라마" in html and "/drama_keywords" in html
    assert tf_env["client"].get("/tf/movie").status_code == 404


def test_download_adds_to_qbit_with_rule_savepath(tf_env):
    c = tf_env["client"]
    r = c.post("/api/tf/variety/download", json={"post_id": "11"})
    assert r.status_code == 200 and "받기 시작" in r.get_json()["message"]
    path, data = tf_env["added"][0]
    assert path == "/api/v2/torrents/add" and data["savepath"] == "/downloads/예능" and "btih:" + "a" * 40 in data["urls"]
    assert "forceStart" not in data and c.get("/api/tf/variety").get_json()["day"][0]["added"]
    assert c.post("/api/tf/variety/download", json={"post_id": "x"}).status_code == 400


def test_subscribe_writes_keyword_with_release(tf_env):
    c = tf_env["client"]
    r = c.post("/api/tf/drama/subscribe", json={"post_id": "5", "title": "나의 유죄인간.Love In Disguise.E04.1080p.NF.WEB-DL-MrHulk"})
    body = r.get_json()
    assert r.status_code == 200 and "릴 MrHulk" in body["message"] and "분류 규칙이 없어요" in body["message"]
    assert "나의 유죄인간 | MrHulk" in tf_env["kw_d"].read_text(encoding="utf-8")
    r = c.post("/api/tf/drama/subscribe", json={"title": "연애박사.E08.261027.720p-NEXT"})
    assert r.status_code == 409 and "이미 계속 받는 중" in r.get_json()["error"]
    r = c.post("/api/tf/variety/subscribe", json={"title": "런닝맨.E826.261026.720p-NEXT"})
    assert r.status_code == 200 and tf_env["kw_v"].read_text(encoding="utf-8").splitlines()[-1] == "런닝맨"
