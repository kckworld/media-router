# -*- coding: utf-8 -*-
"""티프리카 영화 베스트 → 텔레그램 → 받기 → 이름 붙여 옮기기"""
import gzip
import json
from pathlib import Path

import pytest

import movie_mover
import movies

FIX = Path(__file__).parent / "tests_fixtures"


def fixture(name):
    return gzip.open(FIX / name).read().decode("utf-8")


def test_parse_best_lists():
    day = movies.parse_daily_best(fixture("tfreeca_movie_list.html.gz"))
    week = movies.parse_week_best(fixture("tfreeca_movie_week.html.gz"))
    assert [m["id"] for m in day] == ["400650", "400651", "400646", "400649", "400647"]
    assert day[1]["title"].startswith("쇼타씨의 마지막 출장")
    assert len(week) == movies.WEEK_LIMIT
    assert week[0] == {"id": "400643", "title": "인턴 The.Intern.2026.1080p.FHD.H264.AAC"}


def test_parse_view_finds_magnet_and_separate_subtitle():
    v = movies.parse_view(fixture("tfreeca_movie_view.html.gz"))
    assert v["btih"] == "af4f1f5f096f81a792f3097df6e275a54f73c554"
    assert v["subs"] == ["프로포즈 The Proposal 2009 1080p WEB-DL HEVC x265 5.1 BONE.smi"]


@pytest.mark.parametrize("title,expected", [
    ("인턴 The.Intern.2026.1080p.FHD.H264.AAC", ("인턴", "The Intern", 2026)),
    ("존 카터：바숨 전쟁의 서막 John.Carter.2012.2160p.4K.BluRay", ("존 카터：바숨 전쟁의 서막", "John Carter", 2012)),
    ("외계+인 1부 (2022) Alienoid 1080p.FHDRip.H264.AAC", ("외계+인 1부", "Alienoid", 2022)),
    ("다마알 4 Dhamaal.4.2026.1080p.NF.WEB-DL", ("다마알 4", "Dhamaal 4", 2026)),
    ("게일의 원나잇 패스 Gail Daughtry and the Celebrity Sex Pass.1080p.SCOPE", ("게일의 원나잇 패스", "Gail Daughtry and the Celebrity Sex Pass", None)),
])
def test_split_post_title(title, expected):
    assert movies.split_post_title(title) == expected


def test_tmdb_pick_prefers_exact_year():
    results = [{"id": 1, "title": "인턴", "release_date": "2015-09-23"},
               {"id": 2, "title": "인턴", "release_date": "2026-09-16"}]
    assert movies._pick(results, 2026)["id"] == 2
    assert movies._pick(results, 2027)["id"] == 2      # 1년 차이까지 허용
    assert movies._pick(results, 2020) is None
    assert movies._pick(results, None)["id"] == 1


@pytest.mark.parametrize("title,folder", [
    ("인턴", "아"), ("꼬마 니콜라", "가"), ("쌍둥이", "사"), ("존 카터 - 바숨 전쟁의 서막", "자"),
    ("Alien", "1-Z"), ("007 노 타임 투 다이", "1-Z"), ("하모니", "하"), ("뽀로로", "바"),
])
def test_initial_folder(title, folder):
    assert movies.initial_folder(title) == folder


def test_make_prefix_sanitizes():
    assert movies.make_prefix("존 카터: 바숨 전쟁의 서막", 2012) == "존 카터 - 바숨 전쟁의 서막 (2012)"
    assert movies.make_prefix("인턴", 2026) == "인턴 (2026)"


def _job(tmp_path, btih="abc", **kw):
    job = {"btih": btih, "prefix": "인턴 (2026)", "folder": "아", "added": 1000, "status": "downloading"}
    job.update(kw)
    return job


def test_process_moves_completed_movie(tmp_path):
    host, lib = tmp_path / "_movies", tmp_path / "lib"
    d = host / "abc" / "The.Intern.2026.1080p"
    d.mkdir(parents=True)
    (d / "The.Intern.2026.1080p.mkv").write_text("v")
    (d / "The.Intern.2026.1080p.srt").write_text("s")
    (d / "Sample.mkv").write_text("x")
    (d / "info.nfo").write_text("n")
    sent = []
    changes = movie_mover.process([_job(tmp_path)], host, lib, sent.append, now=2000)
    assert sorted(p.name for p in (lib / "아").iterdir()) == [
        "인턴 (2026).The.Intern.2026.1080p.mkv", "인턴 (2026).The.Intern.2026.1080p.srt"]
    assert not (host / "abc").exists()
    assert changes["abc"]["status"] == "done"
    assert "영화/아" in sent[0]


def test_process_waits_while_downloading(tmp_path):
    host, lib = tmp_path / "_movies", tmp_path / "lib"
    (host / "abc").mkdir(parents=True)
    (host / "abc" / "movie.mkv.!qB").write_text("v")
    assert movie_mover.process([_job(tmp_path)], host, lib, lambda t: None, now=2000) == {}
    # 일주일 넘으면 한 번 알림
    sent = []
    changes = movie_mover.process([_job(tmp_path)], host, lib, sent.append, now=1000 + 8 * 86400)
    assert changes["abc"] == {"status": "stalled"} and len(sent) == 1


def _done_movie(tmp_path):
    host, lib = tmp_path / "_movies", tmp_path / "lib"
    d = host / "abc"
    d.mkdir(parents=True)
    (d / "The.Proposal.2009.1080p.mkv").write_text("v")
    return host, lib


SUB = "프로포즈 The Proposal 2009 1080p WEB-DL HEVC x265 5.1 BONE.smi"


def test_subtitle_waits_then_follows_movie_name(tmp_path):
    host, lib = _done_movie(tmp_path)
    drop = tmp_path / "임시download"
    drop.mkdir()
    job = _job(tmp_path, prefix="프로포즈 (2009)", folder="파", subs=[SUB], post_id="400648")
    sent = []
    changes = movie_mover.process([job], host, lib, sent.append, now=2000, sub_dirs=[drop])
    assert changes["abc"]["subs_pending"] is True and changes["abc"]["video"] == "프로포즈 (2009).The.Proposal.2009.1080p.mkv"
    assert "board.php?mode=view&b_id=tmovie&id=400648" in sent[0]
    job.update(changes["abc"])
    # 아직 안 넣었으면 그대로 기다림
    assert movie_mover.process([job], host, lib, sent.append, now=3000, sub_dirs=[drop]) == {}
    # 브라우저가 ' (1)'을 붙여 받아도 찾는다
    (drop / SUB.replace(".smi", " (1).smi")).write_text("s")
    changes = movie_mover.process([job], host, lib, sent.append, now=4000, sub_dirs=[drop])
    assert changes["abc"] == {"subs_pending": False}
    assert (lib / "파" / "프로포즈 (2009).The.Proposal.2009.1080p.smi").read_text() == "s"
    assert not list(drop.iterdir())


def test_subtitle_already_there_when_movie_finishes(tmp_path):
    host, lib = _done_movie(tmp_path)
    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / SUB).write_text("s")
    job = _job(tmp_path, prefix="프로포즈 (2009)", folder="파", subs=[SUB], post_id="400648")
    sent = []
    changes = movie_mover.process([job], host, lib, sent.append, now=2000, sub_dirs=[drop])
    assert "subs_pending" not in changes["abc"]
    assert (lib / "파" / "프로포즈 (2009).The.Proposal.2009.1080p.smi").exists()
    assert "미리 받아 둔 자막" in sent[0]


def test_main_does_not_revive_canceled_job(tmp_path, monkeypatch):
    jobs_path = tmp_path / "movie_jobs.json"
    old = _job(tmp_path, status="canceled")
    new = _job(tmp_path)
    jobs_path.write_text(json.dumps([old, new]), encoding="utf-8")
    monkeypatch.setattr(movie_mover, "JOBS_PATH", jobs_path)
    monkeypatch.setattr(movie_mover, "process", lambda jobs: {"abc": {"status": "done"}})
    movie_mover.main()
    assert [j["status"] for j in json.loads(jobs_path.read_text(encoding="utf-8"))] == ["canceled", "done"]


def test_place_files_never_overwrites(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.mkv").write_text("new")
    dest = tmp_path / "lib" / "아"
    dest.mkdir(parents=True)
    (dest / "인턴 (2026).a.mkv").write_text("old")
    placed = movies.place_files(src, "인턴 (2026)", dest)
    assert placed[0].name == "인턴 (2026).a (1).mkv"
    assert (dest / "인턴 (2026).a.mkv").read_text() == "old"


# ── 텔레그램 ─────────────────────────────────────────

@pytest.fixture
def wa(monkeypatch, tmp_path):
    import web_admin
    monkeypatch.setattr(web_admin, "MOVIE_JOBS_PATH", tmp_path / "movie_jobs.json")
    monkeypatch.setattr(web_admin, "MOVIE_TMDB_CACHE", tmp_path / "movie_tmdb_cache.json")
    sent, qbit = [], []
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, markup=None, token=None: sent.append((text, markup)) or True)
    monkeypatch.setattr(web_admin, "_tg_call", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(web_admin, "_qbit_post", lambda path, data: qbit.append((path, data)))
    monkeypatch.setattr(web_admin, "get_tmdb_api_key", lambda: "k")
    monkeypatch.setattr(movies, "fetch_view", lambda pid: {
        "btih": "deadbeef" * 5, "subs": [], "page_title": "토렌트 - 인턴 The.Intern.2026.1080p.FHD.H264.AAC"})
    monkeypatch.setattr(movies, "tmdb_lookup", lambda key, ko, en, year: {"id": 607833, "title": "인턴", "year": 2026})
    return web_admin, sent, qbit


def test_parse_latest_list():
    rows = movies.parse_list(fixture("tfreeca_movie_list.html.gz"))
    assert len(rows) == 35
    assert rows[0]["id"] == "400651" and rows[0]["title"].startswith("쇼타씨의 마지막 출장") and rows[0]["when"]


def test_movies_page_and_api(wa, monkeypatch):
    web_admin, sent, qbit = wa
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(movies, "fetch_best", lambda: {"day": [{"id": "1", "title": "인턴"}], "week": []})
    monkeypatch.setattr(movies, "fetch_list", lambda page: [{"id": str(page), "title": "목록", "when": "10-26"}])
    web_admin._movie_cache["at"].clear()
    c = web_admin.app.test_client()
    assert "티프리카 영화" in c.get("/movies").get_data(as_text=True)
    d = c.get("/api/movies?page=2").get_json()
    assert d["day"][0]["title"] == "인턴" and d["list"][0]["id"] == "2" and d["page"] == 2
    r = c.post("/api/movies/download", json={"post_id": "400643"})
    assert r.status_code == 200 and r.get_json()["ok"] and qbit[0][0] == "/api/v2/torrents/add"
    assert c.get("/api/movies").get_json()["jobs"][0]["display"] == "인턴 (2026)"
    assert c.post("/api/movies/download", json={"post_id": "400643"}).status_code == 409   # 이미 받는 중
    assert c.post("/api/movies/download", json={"post_id": "../x"}).status_code == 400
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.post("/api/movies/download", json={"post_id": "1"}).status_code == 403


def test_menu_has_movie_button(wa):
    web_admin, sent, _ = wa
    assert [{"text": web_admin.TG_BTN_MOVIES}] in web_admin.TG_MENU["keyboard"]


def test_enrich_caches_by_post(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(movies, "tmdb_lookup", lambda key, ko, en, year: calls.append(ko) or
                        {"id": 1, "title": "'텐징' - Tenzing", "year": 2026, "poster": None, "rating": None, "overview": ""})
    items = [{"id": "400639", "title": "텐징 Tenzing 2026 1080p"}]
    out = movies.enrich(items, "k", tmp_path / "c.json")
    assert out[0]["display"] == "텐징 - Tenzing (2026)" and "namu.wiki/Search?q=" in out[0]["namu"]
    movies.enrich(items, "k", tmp_path / "c.json")
    assert calls == ["텐징"]   # 두 번째는 캐시


def test_movie_best_sends_buttons(wa, monkeypatch, tmp_path):
    web_admin, sent, _ = wa
    monkeypatch.setattr(web_admin, "MOVIE_TMDB_CACHE", tmp_path / "movie_tmdb_cache.json")
    monkeypatch.setattr(web_admin.time, "sleep", lambda s: None)
    monkeypatch.setattr(movies, "fetch_best", lambda: {
        "day": [{"id": "1", "title": "인턴 The.Intern.2026"}], "week": [{"id": "2", "title": "호프 Hope.2026"}]})
    photos = []
    lookups = {"인턴": {"id": 607833, "title": "인턴", "year": 2026, "poster": "https://image.tmdb.org/t/p/w342/a.jpg",
                       "rating": 7.1, "overview": "줄거리"}}
    monkeypatch.setattr(movies, "tmdb_lookup", lambda key, ko, en, year: lookups.get(ko))
    monkeypatch.setattr(web_admin, "_tg_call", lambda method, payload, token=None: photos.append((method, payload)) or {"ok": True})
    web_admin._handle_telegram_text(web_admin.TG_BTN_MOVIES)
    # 인턴: 포스터 메시지 (제목 (연도) ★평점 + 줄거리 + 글 제목, 버튼 3개)
    method, p = photos[0]
    assert method == "sendPhoto" and p["photo"].endswith("/a.jpg")
    assert p["caption"].startswith("🔥 인턴 (2026)  ★7.1\n줄거리\n인턴 The.Intern.2026")
    kb = p["reply_markup"]["inline_keyboard"]
    assert kb[0][0]["callback_data"].startswith("mv_1:")
    assert kb[1][0]["url"].endswith("b_id=tmovie&id=1") and kb[1][1]["url"].startswith("https://namu.wiki/Search?q=")
    # 호프: TMDB에서 못 찾음 → 포스터 없이 글로
    text, markup = sent[-1]
    assert text.startswith("📅 호프 (2026)") and markup["inline_keyboard"][0][0]["callback_data"].startswith("mv_2:")


def test_movie_button_starts_download_and_keeps_list(wa, monkeypatch):
    web_admin, sent, qbit = wa
    edits = []
    monkeypatch.setattr(web_admin, "_tg_call", lambda method, payload, token=None: edits.append(method) or {"ok": True})
    cq = {"id": "q", "data": f"mv_400643:{int(__import__('time').time()) - 3600}",
          "message": {"chat": {"id": 1}, "message_id": 77}}
    web_admin._handle_telegram_callback(cq)
    assert "editMessageReplyMarkup" not in edits          # 목록 버튼은 남겨서 여러 편 고를 수 있게
    path, data = qbit[0]
    assert path == "/api/v2/torrents/add"
    assert data["savepath"] == "/downloads/_movies/" + "deadbeef" * 5 and data["tags"] == "영화"
    jobs = json.loads(web_admin.MOVIE_JOBS_PATH.read_text(encoding="utf-8"))
    assert jobs[0]["prefix"] == "인턴 (2026) {tmdb-607833}" and jobs[0]["display"] == "인턴 (2026)" and jobs[0]["folder"] == "아"
    assert "인턴 (2026)" in sent[-1][0] and "영화/아" in sent[-1][0]
    # 같은 버튼을 또 누르면 처리하지 않음, 같은 영화를 다른 목록에서 눌러도 '이미 받는 중'
    web_admin._handle_telegram_callback(cq)
    assert len(qbit) == 1
    cq2 = dict(cq, message={"chat": {"id": 1}, "message_id": 78})
    web_admin._handle_telegram_callback(cq2)
    assert len(qbit) == 1 and "이미 받는 중" in sent[-1][0]
