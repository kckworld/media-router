# -*- coding: utf-8 -*-
"""다운로드 탭: 다 받았는데 아직 안 옮겨진 파일 (2026-11-01)"""
import os

import pending_downloads as pd
import web_admin

RULES = [{"id": 21, "category": "예능", "pattern": "런닝맨", "subfolder": "런닝맨"},
         {"id": 15, "category": "예능", "pattern": "전지적", "pattern2": "참견", "subfolder": "전지적 참견 시점"},
         {"id": 30, "category": "드라마", "pattern": "100일의 거짓말", "exclude_pattern": "SAMPLE", "subfolder": "100일의 거짓말"}]
BASE = {"예능": "/v/03.예능", "드라마": "/v/02.드라마"}


def _f(path, age, now, size=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (now - age, now - age))
    return path


def test_scan_reasons(tmp_path):
    now = 2_000_000_000
    a, b = tmp_path / "torrent", tmp_path / "임시dl"
    _f(a / "런닝맨.E825.261101.mp4", 60, now)                               # 규칙 맞음, 방금
    _f(a / "100일의 거짓말 E07" / "100일의 거짓말.E07.1080p.mkv", 7200, now)    # 규칙 맞음, 2시간
    _f(a / "100일의 거짓말.E07.SAMPLE.mkv", 60, now)                        # 제외 패턴 → 규칙 없음
    _f(a / "전지적 시점.E1.mp4", 60, now)                                   # pattern2 없음 → 규칙 없음
    _f(a / "모르는 영상.mkv", 60, now)
    _f(a / "_movies" / "인턴 (2015)" / "The.Intern.mkv", 60, now)
    _f(a / "incomplete" / "받는중.mkv", 60, now)                            # 빼기
    _f(a / "런닝맨.E826.261108.mp4.!qB", 60, now)                           # 받는 중 → 빼기
    _f(a / ".hidden" / "x.mp4", 60, now)
    _f(b / "전지적 참견 시점.E420.261031.720p.mp4", 30, now)
    items = pd.scan([str(a), str(b) + "/", str(tmp_path / "없는폴더")], RULES, BASE, now=now, path_map={})
    by = {x["name"]: x for x in items}
    assert set(by) == {"런닝맨.E825.261101.mp4", "100일의 거짓말.E07.1080p.mkv", "100일의 거짓말.E07.SAMPLE.mkv",
                       "전지적 시점.E1.mp4", "모르는 영상.mkv", "The.Intern.mkv", "전지적 참견 시점.E420.261031.720p.mp4"}
    assert by["런닝맨.E825.261101.mp4"]["kind"] == "soon" and "'런닝맨'" in by["런닝맨.E825.261101.mp4"]["reason"]
    assert by["100일의 거짓말.E07.1080p.mkv"]["kind"] == "stuck"
    assert by["100일의 거짓말.E07.1080p.mkv"]["rel"] == "100일의 거짓말 E07/100일의 거짓말.E07.1080p.mkv"
    assert by["100일의 거짓말.E07.SAMPLE.mkv"]["kind"] == "norule" and by["전지적 시점.E1.mp4"]["kind"] == "norule"
    assert by["The.Intern.mkv"]["kind"] == "movie"
    assert by["전지적 참견 시점.E420.261031.720p.mp4"]["folder"] == str(b)            # 끝의 / 정리
    assert items[0]["name"] == "전지적 참견 시점.E420.261031.720p.mp4"                  # 최근 도착 순
    paused = {x["name"]: x for x in pd.scan([str(a)], RULES, BASE, paused=True, now=now, path_map={})}
    assert paused["런닝맨.E825.261101.mp4"]["kind"] == "paused"
    nobase = {x["name"]: x for x in pd.scan([str(a)], RULES, {}, now=now, path_map={})}
    assert nobase["런닝맨.E825.261101.mp4"]["kind"] == "norule"


def test_container_path_map(tmp_path):
    _f(tmp_path / "a.mkv", 10, 2_000_000_000)
    items = pd.scan(["/volume2/torrent/download"], [], {}, now=2_000_000_000,
                    path_map={"/volume2/torrent/download": str(tmp_path)})
    assert [x["folder"] for x in items] == ["/volume2/torrent/download"]


def test_route(monkeypatch, tmp_path):
    _f(tmp_path / "런닝맨.E825.261101.mp4", 10, 2_000_000_000)
    monkeypatch.setattr(web_admin, "load_cfg", lambda: {"paths": {"sources": [str(tmp_path)]}, "rules": RULES, "base_paths": BASE})
    monkeypatch.setattr(web_admin, "processing_pause_state", lambda: {"paused": False})
    web_admin.app.config["TESTING"] = True
    c = web_admin.app.test_client()
    monkeypatch.setattr(web_admin, "authed", lambda req: ("login", 302))
    assert c.get("/downloads/pending").status_code == 401
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    d = c.get("/downloads/pending").get_json()
    assert d["success"] and d["items"][0]["name"] == "런닝맨.E825.261101.mp4"
    html = c.get("/downloads").get_data(as_text=True)
    assert 'id="pendBody"' in html and "아직 안 옮겨진 파일" in html
