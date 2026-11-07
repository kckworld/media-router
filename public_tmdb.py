# -*- coding: utf-8 -*-
"""공개 사이트 설명 창 (2026-11-07, W-20261107-14): 작품 이름으로 TMDB 한국어 자료(줄거리·방영·장르·포스터)를 찾아 캐시.

- TMDB 키는 서버에서만 쓴다. 공개로 나가는 건 아래 정리한 필드뿐(키·요청 주소 없음).
- 캐시: data/pub_tmdb_detail.json — 찾은 건 7일, 못 찾은 건 1일 뒤 다시 찾음.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import requests

TMDB = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w342"
HIT_SECONDS = 7 * 86400
MISS_SECONDS = 86400
STATUS_KO = {"Returning Series": "방영 중", "Ended": "완결", "Canceled": "종영(중단)", "In Production": "제작 중",
             "Released": "개봉", "Planned": "예정", "Post Production": "후반 작업", "Pilot": "파일럿"}
# 11-07(W-15): TMDB가 한국어 요청에도 영어로 주는 TV 장르 → 한국어
GENRE_KO = {"Action & Adventure": "액션·모험", "Sci-Fi & Fantasy": "SF·판타지", "War & Politics": "전쟁·정치", "Kids": "어린이",
            "News": "뉴스", "Reality": "리얼리티", "Soap": "연속극", "Talk": "토크", "Mystery": "미스터리", "Animation": "애니메이션",
            "Comedy": "코미디", "Crime": "범죄", "Documentary": "다큐멘터리", "Drama": "드라마", "Family": "가족", "Western": "서부",
            "Action": "액션", "Adventure": "모험", "Fantasy": "판타지", "Horror": "공포", "Music": "음악", "Romance": "로맨스",
            "Science Fiction": "SF", "Thriller": "스릴러", "War": "전쟁", "History": "역사", "TV Movie": "TV 영화"}
_lock = threading.Lock()


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def _load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _store(path: Path, key: str, val: dict | None) -> None:
    with _lock:
        d = _load(path)
        d[key] = {"at": int(time.time()), "info": val}
        if len(d) > 3000:                                     # 오래된 것부터 정리
            for k in sorted(d, key=lambda k: d[k].get("at", 0))[: len(d) - 3000]:
                d.pop(k, None)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)


def _get(api_key: str, path: str, **params) -> dict | None:
    params.update(api_key=api_key, language="ko-KR")
    try:
        r = requests.get(TMDB + path, params=params, timeout=8)
    except Exception:
        return None
    return r.json() if r.status_code == 200 else None


def genres_ko(names: list[str]) -> list[str]:
    out = []
    for n in names:
        k = GENRE_KO.get(n, n)
        if k and k not in out:
            out.append(k)
    return out


def _clean(media: str, m: dict) -> dict:
    tv = media == "tv"
    date = (m.get("first_air_date") if tv else m.get("release_date")) or ""
    poster = m.get("poster_path") or ""
    return {"media": media, "tmdb_id": int(m["id"]), "title": str(m.get("name" if tv else "title") or "")[:120],
            "original": str(m.get("original_name" if tv else "original_title") or "")[:120],
            "overview": str(m.get("overview") or "").strip()[:1500], "date": date[:10],
            "last_date": str(m.get("last_air_date") or "")[:10] if tv else "",
            "status": STATUS_KO.get(m.get("status") or "", ""),
            "genres": genres_ko([str(g.get("name") or "")[:30] for g in (m.get("genres") or [])])[:5],
            "networks": [str(n.get("name") or "")[:30] for n in (m.get("networks") or [])][:3] if tv else [],
            "seasons": m.get("number_of_seasons") if tv else None, "episodes": m.get("number_of_episodes") if tv else None,
            "runtime": m.get("runtime") if not tv else None,
            "poster": (IMG + poster) if re.fullmatch(r"/[A-Za-z0-9_\-]+\.(?:jpg|png)", poster) else None,
            "url": f"https://www.themoviedb.org/{media}/{int(m['id'])}?language=ko-KR"}


def lookup(api_key: str, name: str, series: bool, year: int | None, cache_path: Path) -> dict | None:
    """이름(+연도)으로 찾기. 같은 이름(띄어쓰기·기호 무시)을 먼저, 없으면 첫 결과"""
    media = "tv" if series else "movie"
    key = f"{media}:{_norm(name)}:{year or ''}"
    hit = _load(cache_path).get(key)
    if hit and time.time() - int(hit.get("at") or 0) < (HIT_SECONDS if hit.get("info") else MISS_SECONDS):
        return hit.get("info")
    if not api_key or not name:
        return None
    q = {"query": name}
    if year:
        q["first_air_date_year" if series else "year"] = int(year)
    res = _get(api_key, f"/search/{media}", **q)
    results = (res or {}).get("results") or []
    if not results and year:                                  # 연도가 달라서 못 찾는 경우(시즌 연도 등)
        results = ((_get(api_key, f"/search/{media}", query=name) or {}).get("results") or [])
    if res is None:
        return None                                           # 연결 실패는 캐시 안 함
    exact = [r for r in results if _norm(r.get("name" if series else "title") or "") == _norm(name)]
    m = (exact or results or [None])[0]
    info = None
    if m and m.get("id"):
        det = _get(api_key, f"/{media}/{int(m['id'])}") or m
        info = _clean(media, det)
    _store(cache_path, key, info)
    return info
