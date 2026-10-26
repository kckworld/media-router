# -*- coding: utf-8 -*-
"""영화 받기: qBittorrent 기준 '받는 중' 판단, 취소, {tmdb-번호} 이름"""
import json

import pytest

import movies
import web_admin

BTIH = "deadbeef" * 5


@pytest.fixture
def wa(monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "MOVIE_JOBS_PATH", tmp_path / "movie_jobs.json")
    monkeypatch.setattr(web_admin, "MOVIE_TMDB_CACHE", tmp_path / "movie_tmdb_cache.json")
    monkeypatch.setattr(movies, "MOVIE_DL_DIR", tmp_path / "dl")
    qbit, torrents = [], {}

    def fake_get(path, params=None):
        assert path == "/api/v2/torrents/info"
        want = set((params or {}).get("hashes", "").split("|"))
        return [dict(t, hash=h) for h, t in torrents.items() if h in want]

    monkeypatch.setattr(web_admin, "_qbit_get", fake_get)
    monkeypatch.setattr(web_admin, "_qbit_post", lambda path, data: qbit.append((path, data)))
    monkeypatch.setattr(web_admin, "_tg_notify", lambda *a, **k: True)
    monkeypatch.setattr(web_admin, "get_tmdb_api_key", lambda: "k")
    monkeypatch.setattr(movies, "fetch_view", lambda pid: {
        "btih": BTIH, "subs": [], "page_title": "토렌트 - 텐징 Tenzing 2026 1080p WEB-DL"})
    monkeypatch.setattr(movies, "tmdb_lookup", lambda key, ko, en, year: {"id": 1288069, "title": "'텐징' - Tenzing", "year": 2026})
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    web_admin.app.config["TESTING"] = True
    return qbit, torrents, tmp_path


def jobs():
    return json.loads(web_admin.MOVIE_JOBS_PATH.read_text(encoding="utf-8"))


def test_make_prefix_with_tmdb_and_quotes():
    assert movies.make_prefix("'텐징' - Tenzing", 2026, 1288069) == "텐징 - Tenzing (2026) {tmdb-1288069}"
    assert movies.make_prefix("인턴", 2026) == "인턴 (2026)"


def test_job_names(wa):
    ok, msg = web_admin.start_movie_download("400639")
    assert ok and "'텐징 - Tenzing (2026)'" in msg and "tmdb" not in msg
    assert jobs()[0]["prefix"] == "텐징 - Tenzing (2026) {tmdb-1288069}"
    assert jobs()[0]["folder"] == "타"


def test_deleted_in_qbit_becomes_canceled_and_can_redownload(wa):
    qbit, torrents, _ = wa
    assert web_admin.start_movie_download("400639")[0] and len(qbit) == 1
    # qBittorrent 화면에서 지움 → 다시 누르면 취소로 정리하고 새로 받는다
    ok, _ = web_admin.start_movie_download("400639")
    assert ok and len(qbit) == 2
    assert [j["status"] for j in jobs()] == ["canceled", "downloading"]


def test_stopped_torrent_is_resumed(wa):
    qbit, torrents, _ = wa
    web_admin.start_movie_download("400639")
    torrents[BTIH] = {"state": "stoppedDL", "progress": 0.4}
    ok, msg = web_admin.start_movie_download("400639")
    assert ok and "다시 받기" in msg and qbit[-1] == ("/api/v2/torrents/start", {"hashes": BTIH})
    torrents[BTIH] = {"state": "downloading", "progress": 0.5}
    ok, msg = web_admin.start_movie_download("400639")
    assert not ok and "50.0%" in msg


def test_finished_and_removed_by_ratio_is_not_canceled(wa):
    qbit, torrents, tmp_path = wa
    web_admin.start_movie_download("400639")
    d = tmp_path / "dl" / BTIH
    d.mkdir(parents=True)
    (d / "movie.mkv").write_text("v")
    web_admin._movie_reconcile()
    assert jobs()[0]["status"] == "downloading"
    j = web_admin.app.test_client().get("/api/movies").get_json()["jobs"][0]
    assert j["qbit_state"] == "waiting_move"


def test_api_shows_progress(wa, monkeypatch):
    qbit, torrents, _ = wa
    monkeypatch.setattr(movies, "fetch_best", lambda: {"day": [], "week": []})
    monkeypatch.setattr(movies, "fetch_list", lambda page: [])
    web_admin.start_movie_download("400639")
    torrents[BTIH] = {"state": "downloading", "progress": 0.25}
    j = web_admin.app.test_client().get("/api/movies").get_json()["jobs"][0]
    assert j["progress"] == 0.25 and j["display"] == "텐징 - Tenzing (2026)"


def test_downloads_cancel_deletes_files_and_marks_movie(wa, monkeypatch):
    qbit, torrents, _ = wa
    web_admin.start_movie_download("400639")
    c = web_admin.app.test_client()
    r = c.post("/api/downloads/cancel", json={"hash": BTIH.upper()})
    assert r.get_json()["success"]
    assert qbit[-1] == ("/api/v2/torrents/delete", {"hashes": BTIH, "deleteFiles": "true"})
    assert jobs()[0]["status"] == "canceled"
    assert c.post("/api/downloads/cancel", json={"hash": "x; rm"}).status_code == 400
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.post("/api/downloads/cancel", json={"hash": BTIH}).status_code == 403
