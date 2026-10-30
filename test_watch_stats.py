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
        elif "AS content" in s:
            self._rows, self.description = [("신병 S04", 39, 2, 12.5)], [("content",), ("plays",), ("viewers",), ("hours",)]
        elif "EXTRACT(DOW" in s:
            self._rows, self.description = [(5, 21, 3.5)], [("d",), ("h",), ("v",)]
        elif "person_name AS person, source" in s:
            self._rows, self.description = [("민영", "plex", 30.0)], [("person",), ("source",), ("hours",)]
        elif "v_play_history_geo" in s:
            self._rows = [("10-30 21:00", "민영", "TV", "신병 시즌4", "3화 x", "plex", 45, "서울")]
            self.description = [(c,) for c in ("at", "person", "type", "title", "episode", "source", "minutes", "region")]
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
    cur = FakeCursor()
    s = watch_stats.summary(cur, 30, private=False)
    assert not {"users", "recent", "persons"} & set(s)
    joined = " ".join(cur.sqls)
    assert "v_play_history_geo" not in joined and "FROM persons" not in joined and "person_name AS person" not in joined


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
    assert not {"users", "recent", "persons"} & set(d)
    html = st_env.get("/stats").get_data(as_text=True)
    assert 'id="stRecent"' not in html and 'id="stPerson"' not in html


def test_admin_gets_everything(st_env, monkeypatch):
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    d = st_env.get("/api/stats?days=7&service=plex").get_json()
    assert d["private"] and d["days"] == 7 and d["recent"][0]["person"] == "민영"
    assert 'id="stRecent"' in st_env.get("/stats").get_data(as_text=True)


def test_public_popular_needs_no_login(st_env, monkeypatch):
    monkeypatch.setattr(web_admin, "authed", lambda req: (_ for _ in ()).throw(AssertionError("로그인 확인하면 안 됨")))
    html = st_env.get("/popular?days=7").get_data(as_text=True)
    assert "신병 S04" in html and "39회 · 2명" in html and "민영" not in html
