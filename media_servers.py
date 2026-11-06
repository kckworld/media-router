# -*- coding: utf-8 -*-
"""사용자 맵핑 드롭다운 (2026-11-07): Jellyfin·Plex 서버의 사용자 목록(읽기만).

- Jellyfin: GET {JELLYFIN_URL}/Users (api_key) → Id(32자) · Name
- Plex: GET {PLEX_URL}/accounts (X-Plex-Token) → id · name — 재생 세션의 User id/title과 같은 값(서버 로컬 계정 목록)
- 값(URL·키)은 .env(MR), 원본은 media-stats/viewer-geo.conf. 키·토큰은 로그·오류 메시지에 안 남긴다.
- 5분 캐시. 못 불러오면 화면은 직접 입력으로.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

CACHE_SECONDS = 300
TIMEOUT = 8
SERVICES = ("jellyfin", "plex")
_cache: dict = {}
_lock = threading.Lock()


class ServerError(Exception):
    pass


def configured(service: str) -> bool:
    if service == "jellyfin":
        return bool(os.getenv("JELLYFIN_URL") and os.getenv("JELLYFIN_API_KEY"))
    if service == "plex":
        return bool(os.getenv("PLEX_URL") and os.getenv("PLEX_TOKEN"))
    return False


def _get(url: str, accept: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "media-router"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    except Exception as e:                                   # URL엔 키가 들어 있어 메시지는 종류만
        raise ServerError(type(e).__name__) from None


def _fetch(service: str) -> list[dict]:
    if service == "jellyfin":
        url = os.environ["JELLYFIN_URL"].rstrip("/") + "/Users?" + urllib.parse.urlencode({"api_key": os.environ["JELLYFIN_API_KEY"]})
        try:
            data = json.loads(_get(url, "application/json"))
        except ValueError:
            raise ServerError("응답 형식") from None
        users = [{"user_id": str(u.get("Id") or ""), "username": str(u.get("Name") or "")} for u in data if isinstance(u, dict)]
    else:
        url = os.environ["PLEX_URL"].rstrip("/") + "/accounts?" + urllib.parse.urlencode({"X-Plex-Token": os.environ["PLEX_TOKEN"]})
        try:
            root = ET.fromstring(_get(url, "application/xml"))
        except ET.ParseError:
            raise ServerError("응답 형식") from None
        users = [{"user_id": str(a.get("id") or ""), "username": str(a.get("name") or "")} for a in root if a.tag == "Account"]
    users = [u for u in users if u["user_id"] and u["username"]]
    users.sort(key=lambda u: u["username"].lower())
    return users


def list_users(service: str, refresh: bool = False) -> list[dict]:
    if service not in SERVICES:
        raise ServerError("모르는 서비스")
    if not configured(service):
        raise ServerError("서버 주소·키 설정 없음")
    now = time.time()
    with _lock:
        hit = _cache.get(service)
        if hit and not refresh and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    users = _fetch(service)
    with _lock:
        _cache[service] = (now, users)
    return users
