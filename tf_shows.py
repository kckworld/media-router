"""티프리카 드라마·예능 게시판 (2026-10-30, 작업 > 드라마·예능 탭)

영화 탭(movies.py)과 같은 방식: 일간 베스트(5개)·주간 베스트·최신 목록을 읽고 TMDB(TV)로 포스터를 붙인다.
버튼 두 가지는 web_admin에서 처리한다.
  - 이 회차만 받기: 글의 마그넷을 qBittorrent에 추가(저장 위치는 qBit RSS 규칙 "예능"/"드라마"와 같게)
    → 다 받으면 media_router.py 분류 규칙이 정리한다.
  - 계속 받기: bt4g/drama_keywords.txt(키워드 | 릴) 또는 bt4g_keywords.txt에 키워드 추가
    → 다음 회차부터 tfreeca.py RSS가 자동으로 받는다.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
from pathlib import Path

import requests

import movies

BOARDS = {"drama": {"b_id": "tdrama", "label": "드라마", "category": "드라마", "rule": "드라마"},
          "variety": {"b_id": "tent", "label": "예능", "category": "예능", "rule": "예능"}}
WEEK_LIMIT = 15
_cache_lock = threading.Lock()


# ── 티프리카 ──────────────────────────────────────────

def _links(part: str, b_id: str, title_re: str) -> list[dict]:
    out, seen = [], set()
    for pid, title in re.findall(rf'b_id={b_id}&id=(\d+)[^"]*"\s*' + title_re, part):
        if pid not in seen:
            seen.add(pid)
            out.append({"id": pid, "title": html.unescape(title).strip()})
    return out


def parse_daily_best(list_html: str, b_id: str) -> list[dict]:
    i = list_html.find("<!--베스트-->")
    j = list_html.find("주간 베스트", i)
    if i < 0 or j < 0:
        return []
    return _links(list_html[i:j], b_id, r'title="([^"]*)"')


def parse_list(list_html: str, b_id: str) -> list[dict]:
    out = []
    for pid, title, when in re.findall(
            rf'mode=view&b_id={b_id}&id=(\d+)[^"]*" class="stitle\d">\s*(.*?)\s*</a>.*?class="datetime">([^<]*)<',
            list_html, re.S):
        out.append({"id": pid, "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(), "when": when.strip()})
    return out


def fetch_list(kind: str, page: int = 1) -> list[dict]:
    b = BOARDS[kind]["b_id"]
    return parse_list(movies._fetch(f"{movies.TFREECA_BASE}/board.php?mode=lists&b_id={b}&page={page}"), b)


def fetch_best(kind: str) -> dict:
    """{'day', 'week'} - 같은 프로그램은 순위가 가장 높은 글 하나만(주간에 회차가 여러 개 올라옴)"""
    b = BOARDS[kind]["b_id"]
    day = parse_daily_best(movies._fetch(f"{movies.TFREECA_BASE}/board.php?mode=lists&b_id={b}"), b)
    week = _links(movies._fetch(f"{movies.TFREECA_BASE}/top100.php?b_id={b}&hit=Week"), b,
                  r'class="[^"]*">\s*([^<]+?)\s*</a>')
    seen: set = set()

    def keep(items, limit=None):
        out = []
        for m in items:
            k = show_key(m["title"])
            if k in seen:
                continue
            seen.add(k)
            out.append(m)
        return out[:limit] if limit else out
    day = keep(day)
    return {"day": day, "week": keep(week, WEEK_LIMIT)}


def fetch_magnet(kind: str, post_id: str) -> tuple[str | None, str]:
    """(btih, 글 제목)"""
    b = BOARDS[kind]["b_id"]
    view = movies.parse_view(movies._fetch(f"{movies.TFREECA_BASE}/board.php?mode=view&b_id={b}&id={post_id}"))
    title = re.sub(r"^토렌트\s*-\s*", "", view["page_title"]).strip()
    return view["btih"], title


# ── 글 제목 풀기 ────────────────────────────────────────

_EP_RE = re.compile(r"[.\s](?:S\d{1,2})?E(\d{1,4})(?=[.\s]|$)", re.I)


def parse_title(title: str) -> dict:
    """'닥터X - 하얀 마피아의 시대.E05.261023.720p-NEXT' → show, ep, air, release
    '나의 유죄인간.Love In Disguise.E04.1080p.NF.WEB-DL...-MrHulk' → show '나의 유죄인간'"""
    t = title.strip()
    m = _EP_RE.search(t)
    head = t[:m.start()] if m else t
    show = head.split(".")[0].strip() or head.strip()
    air = re.search(r"\.(\d{6})\.", t)
    rel = re.search(r"-([A-Za-z][A-Za-z0-9]+)\s*$", re.sub(r"\.(mkv|mp4)$", "", t, flags=re.I))
    return {"show": show, "ep": int(m.group(1)) if m else None, "air": air.group(1) if air else None,
            "release": rel.group(1) if rel else ""}


def norm(s: str) -> str:
    """tfreeca.py normalize와 같게: 띄어쓰기·기호 무시"""
    return re.sub(r"[\W_]+", "", s).lower()


def show_key(title: str) -> str:
    return norm(parse_title(title)["show"])


# ── 계속 받기(키워드)·분류 규칙 상태 ───────────────────────

def keyword_lines(path: Path) -> list[str]:
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def subscribed(title: str, lines: list[str]) -> str | None:
    """이 글을 잡는 키워드 줄(없으면 None). 예능 키워드의 ' 720p'와 드라마 '| 릴'은 떼고 비교"""
    t = norm(title)
    for line in lines:
        name = re.sub(r"\s*720p\s*$", "", line.partition("|")[0].strip(), flags=re.I)
        if norm(name) and norm(name) in t:
            return line
    return None


def _matches_any(filename: str, pattern: str) -> bool:
    # media_router.matches_any와 같게 ('|'로 나뉜 것 중 하나라도 들어 있으면)
    return bool(pattern) and any(p.strip() in filename for p in pattern.split("|") if p.strip())


def rule_for(title: str, rules: list[dict], category: str) -> dict | None:
    """media_router.find_matches 조건(패턴 A그룹 또는 B그룹, 제외 패턴)으로 이 글 이름을 옮길 규칙"""
    for r in rules:
        if (r.get("category") or "") != category:
            continue
        a, a2 = (r.get("pattern") or "").strip(), (r.get("pattern2") or "").strip()
        b, b2 = (r.get("pattern_or") or "").strip(), (r.get("pattern2_or") or "").strip()
        ex = (r.get("exclude_pattern") or "").strip()

        def group(x, y):
            return bool(x or y) and (not x or _matches_any(title, x)) and (not y or _matches_any(title, y))
        if (group(a, a2) or group(b, b2)) and not (ex and _matches_any(title, ex)):
            return r
    return None


# ── TMDB(TV) ──────────────────────────────────────────

def tmdb_tv(api_key: str, show: str) -> dict | None:
    if not (api_key and show):
        return None
    try:
        res = requests.get("https://api.themoviedb.org/3/search/tv", timeout=8,
                           params={"api_key": api_key, "query": show, "language": "ko-KR"}).json()
    except Exception:
        return None
    results = res.get("results") or []
    exact = [r for r in results if norm(r.get("name") or "") == norm(show)]
    m = (exact or results or [None])[0]
    if not m:
        return None
    return {"id": m["id"], "name": m.get("name") or show, "year": int(m["first_air_date"][:4]) if (m.get("first_air_date") or "")[:4].isdigit() else None,
            "poster": f"{movies.TMDB_IMG}/w342{m['poster_path']}" if m.get("poster_path") else None,
            "rating": round(m["vote_average"], 1) if m.get("vote_count") else None,
            "overview": (m.get("overview") or "").strip()}


def enrich(items: list[dict], api_key: str, cache_path: Path, workers: int = 6) -> list[dict]:
    """프로그램 이름별 TMDB 캐시(data/tf_tmdb_cache.json). 못 찾은 것도 캐시해서 다시 묻지 않는다."""
    from concurrent.futures import ThreadPoolExecutor
    with _cache_lock:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    shows = {parse_title(m["title"])["show"] for m in items}
    todo = [s for s in shows if norm(s) and norm(s) not in cache]
    if todo and api_key:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for s, info in zip(todo, ex.map(lambda s: tmdb_tv(api_key, s), todo)):
                cache[norm(s)] = info or {}
        with _cache_lock:
            if len(cache) > 3000:
                cache = dict(list(cache.items())[-2000:])
            tmp = cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cache_path)
    out = []
    for m in items:
        p = parse_title(m["title"])
        info = cache.get(norm(p["show"])) or {}
        out.append(dict(m, show=p["show"], ep=p["ep"], air=p["air"], release=p["release"],
                        poster=info.get("poster"), rating=info.get("rating"), overview=info.get("overview") or "",
                        year=info.get("year"), namu="https://namu.wiki/Search?q=" + requests.utils.quote(p["show"])))
    return out
