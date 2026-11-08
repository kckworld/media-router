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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

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


# ─── 계정별 재생 기록(11-09): 맵핑 안 된 계정·안 쓰는 계정 찾기 ─────────────
# play_history는 맵핑 안 된 계정의 재생을 account_id 없이 저장해서 사람별로 못 센다 → 서버에서 직접 센다.
# - Jellyfin: Playback Reporting(submit_custom_query, UserId별 집계, DateCreated는 시간대 없는 KST) + /Users의 LastActivityDate(UTC)
# - Plex: /status/sessions/history/all?accountID= (Container-Size 1로 totalSize·최근/최초 viewedAt만)
ACTIVITY_SECONDS = 600
_KST = timezone(timedelta(hours=9))


def _iso(s, tz):
    """'2026-11-08 08:25:50.92…' / '2026-06-20T11:04:53.82Z' → KST ISO. tz는 원본 값의 시간대."""
    try:
        return datetime.fromisoformat(str(s)[:19]).replace(tzinfo=tz).astimezone(_KST).isoformat()
    except (TypeError, ValueError):
        return None


def _jellyfin_activity() -> dict:
    base, key = os.environ["JELLYFIN_URL"].rstrip("/"), os.environ["JELLYFIN_API_KEY"]
    qs = urllib.parse.urlencode({"api_key": key})
    try:
        users = json.loads(_get(base + "/Users?" + qs, "application/json"))
    except ValueError:
        raise ServerError("응답 형식") from None
    out = {}
    for u in users:
        if isinstance(u, dict) and u.get("Id") and u.get("Name"):
            out[str(u["Id"])] = {"username": str(u["Name"]), "plays": 0, "watched_sec": 0, "first": None, "last": None,
                                 "last_seen": _iso(u.get("LastActivityDate"), timezone.utc) if u.get("LastActivityDate") else None}
    q = {"CustomQueryString": "SELECT UserId, COUNT(*), MIN(DateCreated), MAX(DateCreated), SUM(PlayDuration) "
                              "FROM PlaybackActivity GROUP BY UserId", "ReplaceUserId": False}
    req = urllib.request.Request(base + "/user_usage_stats/submit_custom_query?" + qs, data=json.dumps(q).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "media-router"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            rows = json.loads(r.read()).get("results") or []
    except Exception:                                        # 플러그인이 없거나 실패해도 계정 목록은 보여 준다
        rows = []
    for row in rows:
        if len(row) >= 5 and row[0] in out:
            uid, cnt, first, last, dur = row[:5]
            out[uid].update(plays=int(cnt or 0), watched_sec=int(float(dur or 0)),
                            first=_iso(first, _KST), last=_iso(last, _KST))
    return out


def _plex_one(base: str, token: str, aid: str, sort: str):
    url = base + "/status/sessions/history/all?" + urllib.parse.urlencode(
        {"sort": "viewedAt:" + sort, "accountID": aid, "X-Plex-Token": token})
    req = urllib.request.Request(url, headers={"Accept": "application/xml", "User-Agent": "media-router",
                                               "X-Plex-Container-Start": "0", "X-Plex-Container-Size": "1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            root = ET.fromstring(r.read())
    except Exception as e:                                   # URL에 토큰이 있어 메시지는 종류만
        raise ServerError(type(e).__name__) from None
    v = [c.get("viewedAt") for c in root if c.get("viewedAt")]
    return int(root.get("totalSize") or 0), (datetime.fromtimestamp(int(v[0]), _KST).isoformat() if v else None)


def _plex_activity() -> dict:
    base, token = os.environ["PLEX_URL"].rstrip("/"), os.environ["PLEX_TOKEN"]
    accounts = [u for u in _fetch("plex") if u["user_id"] != "0"]   # id 0은 서버 자체 계정

    def one(u):
        total, last = _plex_one(base, token, u["user_id"], "desc")
        first = _plex_one(base, token, u["user_id"], "asc")[1] if total else None
        return u["user_id"], {"username": u["username"], "plays": total, "watched_sec": None,
                              "first": first, "last": last, "last_seen": None}

    with ThreadPoolExecutor(8) as ex:
        return dict(ex.map(one, accounts))


def activity(service: str, refresh: bool = False) -> dict:
    """{user_id: {username, plays, watched_sec, first, last, last_seen}} — 10분 캐시."""
    if service not in SERVICES:
        raise ServerError("모르는 서비스")
    if not configured(service):
        raise ServerError("서버 주소·키 설정 없음")
    ck = "activity:" + service
    now = time.time()
    with _lock:
        hit = _cache.get(ck)
        if hit and not refresh and now - hit[0] < ACTIVITY_SECONDS:
            return hit[1]
    data = _jellyfin_activity() if service == "jellyfin" else _plex_activity()
    with _lock:
        _cache[ck] = (now, data)
    return data
