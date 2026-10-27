"""영화진흥위원회(KOBIS) 오픈 API로 국내 누적 관객수 (2026-10-27)

KOBIS는 영화별 누적 관객수를 바로 주지 않고, 날짜별·주별 박스오피스 상위 10편의 누적 관객수만 준다.
그래서 영화 목록 검색으로 국내 개봉일을 찾고, 개봉 주부터 주간 박스오피스를 한 주씩 넘겨 보며
10위 안에 있던 마지막 주의 누적 관객수를 쓴다. 10위 안에 든 적 없는 영화(OTT 전용, 소규모·미개봉)는 None.
결과는 data/kobis_cache.json에 저장 - 상영이 끝난 영화는 다시 묻지 않고, 상영 중이면 하루에 한 번 갱신.
키: 환경변수 KOBIS_API_KEY (하루 3,000번 무료)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

API = "https://www.kobis.or.kr/kobisopenapi/webservice/rest"
MAX_WEEKS = 16
RECHECK_SEC = 24 * 3600
_lock = threading.Lock()


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s or "").lower()


def _get(key: str, path: str, **params) -> dict:
    r = requests.get(f"{API}/{path}", params=dict(params, key=key), timeout=10)
    r.raise_for_status()
    data = r.json()
    if "faultInfo" in data:
        raise RuntimeError(data["faultInfo"].get("message") or "KOBIS 오류")
    return data


def find_movie(key: str, title: str, year: int | None) -> dict | None:
    """국내 개봉 기록이 있는 영화 {movieCd, openDt(date)} - 제목이 같고 개봉 연도가 TMDB 연도 ±1"""
    names = [title] + [p for p in re.split(r"\s*[:：]\s*|\s+-\s+", title)[:1] if p != title]
    for name in names:
        rows = _get(key, "movie/searchMovieList.json", movieNm=name, itemPerPage=20)["movieListResult"]["movieList"]
        cands = []
        for m in rows:
            od = m.get("openDt") or ""
            if not re.fullmatch(r"\d{8}", od):
                continue
            od = datetime.strptime(od, "%Y%m%d").date()
            if year and abs(od.year - year) > 1:
                continue
            exact = _norm(m.get("movieNm")) == _norm(title)
            cands.append((not exact, abs(od.year - (year or od.year)), {"movieCd": m["movieCd"], "openDt": od}))
        if cands:
            return sorted(cands, key=lambda c: (c[0], c[1]))[0][2]
    return None


def audience_of(key: str, movie_cd: str, open_dt: date, today: date) -> tuple[int | None, date | None]:
    """주간 박스오피스(월~일)를 개봉 주부터 넘겨 보며 마지막으로 10위 안에 있던 주의 누적 관객수"""
    aud, last = None, None
    for k in range(MAX_WEEKS):
        t = open_dt + timedelta(days=7 * k)
        if t > today - timedelta(days=7):   # 아직 안 끝난 주는 집계가 없다
            break
        rows = _get(key, "boxoffice/searchWeeklyBoxOfficeList.json",
                    targetDt=t.strftime("%Y%m%d"), weekGb="0")["boxOfficeResult"]["weeklyBoxOfficeList"]
        hit = next((r for r in rows if r.get("movieCd") == movie_cd), None)
        if hit:
            aud, last = int(hit["audiAcc"]), t
        elif last is not None or k >= 2:    # 순위 밖으로 나갔거나, 개봉 2주 넘게 한 번도 못 들었음
            break
    return aud, last


def audience(key: str, title: str, year: int | None, cache_path: Path, now: float | None = None) -> int | None:
    if not key or not title:
        return None
    now = now or time.time()
    ck = f"{_norm(title)}|{year or ''}"
    with _lock:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    hit = cache.get(ck)
    if hit and (hit.get("final") or now - hit.get("checked", 0) < RECHECK_SEC):
        return hit.get("audience")
    try:
        mv = find_movie(key, title, year)
        today = date.fromtimestamp(now)
        aud, last = audience_of(key, mv["movieCd"], mv["openDt"], today) if mv else (None, None)
    except Exception:
        return hit.get("audience") if hit else None   # 잠시 안 되면 예전 값이라도
    # 순위에서 빠진 지 3주가 지났거나 국내 개봉 기록이 없으면 다시 묻지 않는다
    final = mv is None or last is None and mv["openDt"] < today - timedelta(days=21) \
        or last is not None and last < today - timedelta(days=28)
    with _lock:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        cache[ck] = {"audience": aud, "checked": int(now), "final": bool(final)}
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cache_path)
    return aud


def fmt(n: int | None) -> str:
    """683364 → '68만', 1578959 → '158만', 8500 → '8,500명'"""
    if not n:
        return ""
    return f"{round(n / 10000):,}만" if n >= 10000 else f"{n:,}명"
