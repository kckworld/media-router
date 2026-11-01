# -*- coding: utf-8 -*-
"""시청 통계 탭·공개 인기 순위 테스트. Postgres는 가짜 커서로 — 개인정보가 보기 전용·공개 응답에 안 나가는지가 핵심."""
from contextlib import contextmanager

import pytest

import watch_stats
import web_admin


class FakeCursor:
    """SQL을 기록하고, 질의 모양에 맞는 가짜 행을 돌려준다"""

    def __init__(self):
        self.sqls, self.description, self._rows = [], [], []

    def execute(self, sql, args=None):
        self.sqls.append(sql)
        s = " ".join(sql.split())
        if s.startswith("SELECT COUNT(*), ROUND"):
            self._rows, self.description = [(848, 699.5)], [("a",), ("b",)]
        elif "COUNT(DISTINCT person_id) FROM" in s:
            self._rows, self.description = [(22,)], [("n",)]
        elif "AS content" in s and "WITH v AS" not in s:
            self._rows, self.description = [("신병 S04", 39, 2, 12.5)], [("content",), ("plays",), ("viewers",), ("hours",)]
        elif "EXTRACT(DOW" in s:
            self._rows, self.description = [(5, 21, 3.5)], [("d",), ("h",), ("v",)]
        elif "person_name AS person, source" in s:
            self._rows, self.description = [("민영", "plex", 30.0)], [("person",), ("source",), ("hours",)]
        elif "v_play_history_geo" in s:
            self._rows = [("10-30 21:00", "민영", "TV", "신병 시즌4", "3화 x", "plex", 45, "서울")]
            self.description = [(c,) for c in ("at", "person", "type", "title", "episode", "source", "minutes", "region")]
        elif "MIN(seen_at)" in s:
            self._rows, self.description = [("2026-10-28",)], [("since",)]
        elif "WITH v AS" in s and "COUNT(*) FILTER (WHERE pct < 50)" in s:
            self._rows, self.description = [(10, 6, 2, 81)], [("a",), ("b",), ("c",), ("d",)]
        elif "WITH v AS" in s:
            self._rows, self.description = [("신병", 3, 90, 67)], [("content",), ("views",), ("avg_pct",), ("done_pct",)]
        elif "AS method" in s:
            self._rows, self.description = [("DirectPlay", 7.5), ("Transcode", 2.5)], [("method",), ("hours",)]
        elif "s.device" in s:
            self._rows, self.description = [("민영", "민영의 iPad", 2.0, 100)], [("person",), ("device",), ("hours",), ("tx_pct",)]
        elif "lib.total" in s:
            self._rows = [("민영", "신병", 18, 32, 3, 3, "11-01")]
            self.description = [(c,) for c in ("person", "series", "watched", "total", "season", "episode", "last")]
        elif "FROM persons" in s:
            self._rows, self.description = [("민영",)], [("name",)]
        else:
            self._rows, self.description = [], [("x",), ("y",)]

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return self._rows


def test_summary_private_vs_public_queries():
    cur = FakeCursor()
    s = watch_stats.summary(cur, 30)
    assert s["plays"] == 848 and s["active_users"] == 22 and s["heatmap"][5][21] == 3.5
    assert s["users"][0]["person"] == "민영" and s["recent"][0]["region"] == "서울" and s["persons"] == ["민영"]
    assert s["series"][0]["total"] == 32 and s["playback"]["devices"][0]["device"] == "민영의 iPad"
    assert s["playback"]["since"] == "2026-10-28" and s["playback"]["methods"][1]["method"] == "Transcode"
    assert s["completion"]["views"] == 10 and s["completion"]["finished"] == 6 and s["completion"]["worst"][0]["content"] == "신병"
    cur = FakeCursor()
    s = watch_stats.summary(cur, 30, private=False)
    assert not {"users", "recent", "persons", "series"} & set(s) and "devices" not in s["playback"]
    assert s["completion"]["avg_pct"] == 81
    joined = " ".join(cur.sqls)
    assert "v_play_history_geo" not in joined and "FROM persons" not in joined and "person_name AS person" not in joined
    assert "s.device" not in joined and "lib.total" not in joined


def test_person_filter_reaches_session_log():
    cur = FakeCursor()
    watch_stats.playback(cur, 7, "민영", "plex", private=False)
    assert all("p.name = %s" in q and "s.service = %s" in q for q in cur.sqls if "stream_session_log s" in q)


@pytest.fixture
def st_env(monkeypatch):
    @contextmanager
    def fake_db():
        class Conn:
            def set_session(self, **kw):
                pass

            def cursor(self):
                return FakeCursor()
        yield Conn()
    monkeypatch.setattr(web_admin, "_stats_db", fake_db)
    web_admin._stats_cache["data"].clear()
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client()


def test_viewer_gets_no_personal_data(st_env, monkeypatch):
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    d = st_env.get("/api/stats?person=민영").get_json()
    assert d["ok"] and d["private"] is False and d["popular"][0]["content"] == "신병 S04"
    assert not {"users", "recent", "persons", "series"} & set(d) and "devices" not in d["playback"]
    assert "민영" not in str(d)
    html = st_env.get("/stats").get_data(as_text=True)
    assert 'id="stRecent"' not in html and 'id="stPerson"' not in html
    assert 'id="stSeries"' not in html and 'id="stDevices"' not in html and 'id="stPlatforms"' in html


def test_admin_gets_everything(st_env, monkeypatch):
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    d = st_env.get("/api/stats?days=7&service=plex").get_json()
    assert d["private"] and d["days"] == 7 and d["recent"][0]["person"] == "민영"
    assert 'id="stRecent"' in st_env.get("/stats").get_data(as_text=True)


def test_public_popular_needs_no_login(st_env, monkeypatch):
    monkeypatch.setattr(web_admin, "authed", lambda req: (_ for _ in ()).throw(AssertionError("로그인 확인하면 안 됨")))
    html = st_env.get("/popular?days=7").get_data(as_text=True)
    assert "신병 S04" in html and "39회 · 2명" in html and "민영" not in html
