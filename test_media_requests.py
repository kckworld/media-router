# -*- coding: utf-8 -*-
"""단톡방 영상 요청 페이지"""
import json

import pytest

import media_requests
import web_admin


@pytest.fixture
def rq(monkeypatch, tmp_path):
    monkeypatch.setattr(web_admin, "MEDIA_REQUESTS_PATH", tmp_path / "media_requests.json")
    monkeypatch.setattr(web_admin, "MEDIA_REQUEST_SETTINGS", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps({"code": "1234"}), encoding="utf-8")
    monkeypatch.setattr(web_admin, "_rq_search_limit", media_requests.RateLimiter(30, 60))
    monkeypatch.setattr(web_admin, "_rq_submit_limit", media_requests.RateLimiter(5, 3600))
    monkeypatch.setattr(web_admin, "_rq_code_fail_limit", media_requests.RateLimiter(10, 3600))
    monkeypatch.setattr(web_admin, "_rq_code_fail_total", media_requests.RateLimiter(50, 3600))
    monkeypatch.setattr(web_admin, "_rq_submit_total", media_requests.RateLimiter(30, 3600))
    monkeypatch.setattr(web_admin, "get_tmdb_api_key", lambda: "k")
    monkeypatch.setattr(media_requests, "tmdb_detail", lambda key, mt, tid: {
        "tmdb_id": tid, "media_type": mt, "title": "인턴", "year": 2026,
        "poster": "https://image.tmdb.org/t/p/w185/a.jpg", "overview": "", "category": "영화"})
    sent = []
    monkeypatch.setattr(web_admin, "_tg_call", lambda method, payload, token=None: sent.append((method, payload)) or {"ok": True})
    monkeypatch.setattr(web_admin, "_tg_cfg", lambda: {"enabled": True, "chat_id": "1", "bot_token": "t"})
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, markup=None, token=None: sent.append(("text", {"text": text, "reply_markup": markup})) or True)
    monkeypatch.setattr(web_admin, "load_cfg", lambda: {"rules": [], "base_paths": {}})
    monkeypatch.setattr(web_admin, "MOVIE_JOBS_PATH", tmp_path / "movie_jobs.json")
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), sent, tmp_path


GOOD = {"code": "1234", "name": "홍길동", "category": "영화", "platform": "넷플릭스", "memo": "자막 있으면 좋아요",
        "title": "인턴", "tmdb_id": 607833, "media_type": "movie", "website": ""}


def saved(tmp_path):
    return json.loads((tmp_path / "media_requests.json").read_text(encoding="utf-8"))


def test_page_is_public_and_simple(rq):
    c, _, _ = rq
    html = c.get("/request").get_data(as_text=True)
    assert "영상 요청" in html and "단톡방 코드" in html and "넷플릭스" in html
    assert "/settings" not in html and "nav" not in html.lower().split("<main")[0]


def test_submit_saves_and_notifies_with_poster(rq):
    c, sent, tmp_path = rq
    r = c.post("/request/submit", json=GOOD, headers={"CF-Connecting-IP": "1.1.1.1"})
    assert r.get_json() == {"ok": True}
    it = saved(tmp_path)[0]
    assert (it["title"], it["year"], it["name"], it["platform"], it["status"]) == ("인턴", 2026, "홍길동", "넷플릭스", "new")
    method, p = sent[-1]
    assert method == "sendPhoto" and p["photo"].endswith("/w342/a.jpg")
    assert p["caption"].startswith("🙋 홍길동님 요청\n인턴 (2026)\n영화 · 넷플릭스\n메모: 자막 있으면 좋아요")
    assert p["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith(f"rq_done_{it['id']}:")


def test_server_uses_tmdb_detail_not_client_title(rq):
    c, _, tmp_path = rq
    c.post("/request/submit", json=dict(GOOD, title="<script>가짜</script>"))
    assert saved(tmp_path)[0]["title"] == "인턴"


def test_manual_title_without_tmdb(rq):
    c, sent, tmp_path = rq
    r = c.post("/request/submit", json=dict(GOOD, tmdb_id="", media_type="", title="어떤 옛날 예능 2005", category="예능", platform=""))
    assert r.get_json()["ok"]
    assert saved(tmp_path)[0]["title"] == "어떤 옛날 예능 2005" and sent[-1][0] == "text"


@pytest.mark.parametrize("bad,msg", [
    ({"name": ""}, "이름"), ({"category": "다큐"}, "골라"), ({"platform": "넷플릭스2"}, "플랫폼"),
    ({"tmdb_id": "", "title": ""}, "제목"), ({"memo": "x" * 301}, "메모"), ({"name": "가" * 31}, "이름"),
])
def test_validation(rq, bad, msg):
    c, _, _ = rq
    r = c.post("/request/submit", json=dict(GOOD, **bad))
    assert r.status_code == 400 and msg in r.get_json()["error"]


def test_wrong_code_and_no_code(rq):
    c, _, tmp_path = rq
    r = c.post("/request/submit", json=dict(GOOD, code="0000"))
    assert r.status_code == 403 and "코드" in r.get_json()["error"]
    (tmp_path / "settings.json").write_text(json.dumps({"code": ""}), encoding="utf-8")
    assert c.post("/request/submit", json=GOOD).status_code == 403
    assert "요청을 받지 않아요" in c.get("/request").get_data(as_text=True)


def test_code_guessing_blocked_even_with_spoofed_ip(rq):
    c, _, _ = rq
    codes = [c.post("/request/submit", json=dict(GOOD, code=f"{i:04d}"),
                    headers={"CF-Connecting-IP": f"9.9.{i // 256}.{i % 256}"}).status_code for i in range(60)]
    assert codes[:50] == [403] * 50 and set(codes[50:]) == {429}
    # 코드 틀림 한도에 걸려도 맞는 코드는 그대로 통과
    assert c.post("/request/submit", json=GOOD, headers={"CF-Connecting-IP": "8.8.8.8"}).status_code == 200


def test_honeypot_silently_ignored(rq):
    c, sent, tmp_path = rq
    assert c.post("/request/submit", json=dict(GOOD, website="http://spam")).get_json() == {"ok": True}
    assert not (tmp_path / "media_requests.json").exists() and not sent


def test_rate_limit_per_ip(rq):
    c, _, _ = rq
    for _ in range(5):
        assert c.post("/request/submit", json=GOOD, headers={"CF-Connecting-IP": "2.2.2.2"}).status_code == 200
    assert c.post("/request/submit", json=GOOD, headers={"CF-Connecting-IP": "2.2.2.2"}).status_code == 429
    assert c.post("/request/submit", json=GOOD, headers={"CF-Connecting-IP": "3.3.3.3"}).status_code == 200


def test_public_host_only_serves_request_page(rq, monkeypatch):
    c, _, _ = rq
    monkeypatch.setattr(web_admin, "REQUEST_PUBLIC_HOSTS", {"request.kckworld.com"})
    h = {"Host": "request.kckworld.com"}
    assert "영상 요청" in c.get("/", headers=h).get_data(as_text=True)
    for path in ("/login", "/settings", "/movies", "/api/movies", "/requests", "/api/requests", "/downloads/data"):
        assert c.get(path, headers=h).status_code == 404, path
    assert c.post("/run_batch", headers=h).status_code == 404
    assert c.post("/request/submit", json=GOOD, headers=h).status_code == 200
    # 원래 주소는 그대로
    assert c.get("/login", headers={"Host": "media.kckworld.com"}).status_code == 200


def test_admin_list_and_update(rq, monkeypatch):
    c, sent, tmp_path = rq
    c.post("/request/submit", json=GOOD)
    rid = saved(tmp_path)[0]["id"]
    monkeypatch.setattr(web_admin, "authed", lambda req: ("login", 302))
    assert c.get("/api/requests").status_code == 401           # 로그인 필요
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    assert c.get("/api/requests").get_json()["items"][0]["id"] == rid
    assert c.post(f"/api/requests/{rid}", json={"status": "done"}).get_json()["ok"]
    assert saved(tmp_path)[0]["status"] == "done"
    assert c.post("/api/requests/code", json={"code": "9999"}).get_json()["code"] == "9999"
    assert c.post(f"/api/requests/{rid}", json={"delete": True}).get_json()["ok"]
    assert saved(tmp_path) == []
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    assert c.get("/api/requests").status_code == 403            # 보기 전용 계정은 이름 목록 못 봄


def test_telegram_done_button(rq):
    c, sent, tmp_path = rq
    c.post("/request/submit", json=GOOD)
    rid = saved(tmp_path)[0]["id"]
    import time
    web_admin._handle_telegram_callback({"id": "q", "data": f"rq_done_{rid}:{int(time.time()) - 86400 * 3}",
                                         "message": {"chat": {"id": 1}, "message_id": 5}})
    assert saved(tmp_path)[0]["status"] == "done"


def test_done_sends_kakao_ready_message_once(rq, monkeypatch):
    c, sent, tmp_path = rq
    monkeypatch.setattr(media_requests, "tmdb_detail", lambda key, mt, tid: {
        "tmdb_id": tid, "media_type": mt, "title": "인턴", "year": 2026, "rating": 6.5,
        "poster": "https://image.tmdb.org/t/p/w185/a.jpg", "overview": "줄거리입니다.", "category": "영화"})
    c.post("/request/submit", json=GOOD)
    rid = saved(tmp_path)[0]["id"]
    sent.clear()
    import time
    cb = {"id": "q", "data": f"rq_done_{rid}:{int(time.time())}", "message": {"chat": {"id": 1}, "message_id": 9}}
    web_admin._handle_telegram_callback(cb)
    photos = [p for m, p in sent if m == "sendPhoto"]
    assert len(photos) == 1 and photos[0]["photo"].endswith("/w500/a.jpg") and "reply_markup" not in photos[0]
    assert photos[0]["caption"] == "🎬 요청하신 작품이 올라왔어요!\n인턴 (2026) · 영화 · ★6.5\n줄거리입니다.\n요청: 홍** 님"
    assert media_requests.mask_name("Tom") == "T**" and media_requests.mask_name(" 손 ") == "손**"
    # 이미 완료인 걸 다시 완료로 바꿔도 또 보내지 않음
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    c.post(f"/api/requests/{rid}", json={"status": "done"})
    assert len([1 for m, _ in sent if m == "sendPhoto"]) == 1


def test_library_line_series_and_movie(tmp_path):
    base = tmp_path / "01.드라마"
    show = base / "연애박사 (2026)"
    show.mkdir(parents=True)
    for e in (1, 2, 3):
        (show / f"Between.Steps.S01E0{e}.1080p.DSNP.WEB-DL.mkv").write_text("v")
    (show / "Between.Steps.S01E03.1080p.DSNP.WEB-DL.ko.srt").write_text("s")
    rules = [{"category": "드라마", "subfolder": "연애박사 (2026)"}]
    it = {"title": "연애박사", "category": "드라마"}
    line = media_requests.library_line(it, rules, {"드라마": str(base)}, tmp_path / "movies", [])
    assert line.startswith("📺 1~3화 · 1080p · WEB-DL")
    # 시즌 폴더 + 규칙 이름이 TMDB 제목과 달라도 분류 폴더에서 이름으로 못 찾으면 빈 줄
    assert media_requests.library_line({"title": "치이카와", "category": "애니메이션"}, [], {}, tmp_path, []) == ""
    # 영화: 영화 받기 기록(TMDB 번호)으로
    mv = tmp_path / "movies" / "아"
    mv.mkdir(parents=True)
    (mv / "인턴 (2026) {tmdb-607833}.The.Intern.2026.1080p.FHD.mkv").write_text("v")
    jobs = [{"tmdb_id": 607833, "status": "done", "folder": "아", "video": "인턴 (2026) {tmdb-607833}.The.Intern.2026.1080p.FHD.mkv"}]
    m = {"title": "인턴", "year": 2026, "category": "영화", "tmdb_id": 607833}
    assert media_requests.library_line(m, [], {}, tmp_path / "movies", jobs) == "🎞 1080p"
    # 기록이 없어도 '제목 (연도)'로 찾는다
    assert media_requests.library_line(m, [], {}, tmp_path / "movies", []) == "🎞 1080p"
    (mv / "인턴 (2026) {tmdb-607833}.The.Intern.2026.1080p.FHD.mkv").rename(mv / "인턴 (2026).Gecko [IQ.WEB-DL 1080P AVC].mkv")
    assert media_requests.library_line(m, [], {}, tmp_path / "movies", []) == "🎞 1080p · WEB-DL"


def test_guess_category():
    assert media_requests.guess_category("movie", [18]) == "영화"
    assert media_requests.guess_category("movie", [16]) == "애니메이션"
    assert media_requests.guess_category("tv", [10764]) == "예능"
    assert media_requests.guess_category("tv", [18]) == "드라마"
