# -*- coding: utf-8 -*-
"""사용자 맵핑 드롭다운 (2026-11-07): Jellyfin·Plex 사용자 목록, 맵핑된 건 표시, 키는 오류에 안 나옴 — 가짜 응답으로"""
import json
from contextlib import contextmanager

import pytest

import media_servers
import web_admin

JF = json.dumps([{"Id": "a" * 32, "Name": "jfuser"}, {"Id": "b" * 32, "Name": "Alpha"}, {"Id": "", "Name": "빈ID"}]).encode()
PX = b'<MediaContainer><Account id="1" name="owner"/><Account id="12345" name="friend"/><Other id="9" name="x"/></MediaContainer>'


@pytest.fixture
def env(monkeypatch):
    for k, v in {"JELLYFIN_URL": "http://jf:8096/", "JELLYFIN_API_KEY": "JFKEY-SECRET", "PLEX_URL": "http://px:32400",
                 "PLEX_TOKEN": "PXTOKEN-SECRET"}.items():
        monkeypatch.setenv(k, v)
    media_servers._cache.clear()
    seen = []

    def fake_get(url, accept):
        seen.append(url)
        return JF if "/Users?" in url else PX
    monkeypatch.setattr(media_servers, "_get", fake_get)
    return seen


def test_lists_and_cache(env):
    assert media_servers.list_users("jellyfin") == [{"user_id": "b" * 32, "username": "Alpha"}, {"user_id": "a" * 32, "username": "jfuser"}]
    assert media_servers.list_users("plex") == [{"user_id": "12345", "username": "friend"}, {"user_id": "1", "username": "owner"}]
    media_servers.list_users("plex")
    assert len(env) == 2                                                              # 5분 캐시
    assert env[0].startswith("http://jf:8096/Users?api_key=") and "/accounts?X-Plex-Token=" in env[1]


def test_errors_hide_keys(env, monkeypatch):
    def boom(url, accept):
        raise media_servers.ServerError("URLError")
    monkeypatch.setattr(media_servers, "_get", boom)
    with pytest.raises(media_servers.ServerError) as e:
        media_servers.list_users("jellyfin")
    assert "SECRET" not in str(e.value)
    monkeypatch.delenv("PLEX_TOKEN")
    with pytest.raises(media_servers.ServerError):
        media_servers.list_users("plex")
    with pytest.raises(media_servers.ServerError):
        media_servers.list_users("emby")


class _Cur:
    def __init__(self, rows): self.rows = rows
    def execute(self, sql, args=None): self.args = args
    def fetchall(self): return self.rows
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, rows): self.rows = rows
    def cursor(self): return _Cur(self.rows)
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_route_marks_mapped(env, monkeypatch):
    monkeypatch.setattr(web_admin, "_stats_db", lambda: _Conn([("12345", "friend", "김친구")]))
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    web_admin.app.config["TESTING"] = True
    c = web_admin.app.test_client()
    d = c.get("/api/users/service_users?service=plex").get_json()
    assert d["ok"] and {u["username"]: u["mapped_to"] for u in d["users"]} == {"friend": "김친구", "owner": None}
    monkeypatch.setattr(media_servers, "_get", lambda url, a: (_ for _ in ()).throw(media_servers.ServerError("timeout")))
    media_servers._cache.clear()
    r = c.get("/api/users/service_users?service=plex")
    assert r.status_code == 502 and "직접 입력" in r.get_json()["error"] and "SECRET" not in r.get_data(as_text=True)
    monkeypatch.setattr(web_admin, "authed", lambda req: ("login", 302))
    assert c.get("/api/users/service_users?service=plex").status_code == 401


def test_page_has_dropdown(monkeypatch):
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    web_admin.app.config["TESTING"] = True
    h = web_admin.app.test_client().get("/users").get_data(as_text=True)
    assert 'id="newPick"' in h and "loadServiceUsers()" in h and "/api/users/service_users?service=" in h and 'id="newUserId"' in h
