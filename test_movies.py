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
    sent, qbit = [], []
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, markup=None, token=None: sent.append((text, markup)) or True)
    monkeypatch.setattr(web_admin, "_tg_call", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(web_admin, "_qbit_post", lambda path, data: qbit.append((path, data)))
    monkeypatch.setattr(web_admin, "get_tmdb_api_key", lambda: "k")
    monkeypatch.setattr(movies, "fetch_view", lambda pid: {
        "btih": "deadbeef" * 5, "subs": [], "page_title": "토렌트 - 인턴 The.Intern.2026.1080p.FHD.H264.AAC"})
    monkeypatch.setattr(movies, "tmdb_lookup", lambda key, ko, en, year: {"id": 607833, "title": "인턴", "year": 2026})
    return web_admin, sent, qbit


def test_menu_has_movie_button(wa):
    web_admin, sent, _ = wa
    assert [{"text": web_admin.TG_BTN_MOVIES}] in web_admin.TG_MENU["keyboard"]


def test_movie_best_sends_buttons(wa, monkeypatch):
    web_admin, sent, _ = wa
    monkeypatch.setattr(movies, "fetch_best", lambda: {
        "day": [{"id": "1", "title": "인턴 The.Intern.2026"}], "week": [{"id": "2", "title": "호프 Hope.2026"}]})
    web_admin._handle_telegram_text(web_admin.TG_BTN_MOVIES)
    text, markup = sent[-1]
    rows = markup["inline_keyboard"]
    assert rows[0][0]["text"] == "🔥 인턴 The.Intern.2026" and rows[0][0]["callback_data"].startswith("mv_1:")
    assert rows[1][0]["text"].startswith("📅 호프")


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
    assert jobs[0]["prefix"] == "인턴 (2026)" and jobs[0]["folder"] == "아"
    assert "인턴 (2026)" in sent[-1][0] and "영화/아" in sent[-1][0]
    # 같은 버튼을 또 누르면 처리하지 않음, 같은 영화를 다른 목록에서 눌러도 '이미 받는 중'
    web_admin._handle_telegram_callback(cq)
    assert len(qbit) == 1
    cq2 = dict(cq, message={"chat": {"id": 1}, "message_id": 78})
    web_admin._handle_telegram_callback(cq2)
    assert len(qbit) == 1 and "이미 받는 중" in sent[-1][0]
