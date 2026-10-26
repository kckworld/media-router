"""단톡방 영상 요청 (2026-10-26)

로그인 없이 여는 /request 페이지에서 단톡방 사람들이 영화·드라마·예능·애니메이션을 요청한다.
TMDB에서 찾아 제목·연도를 채우고, 요청자 이름·분류는 필수, 플랫폼·메모는 선택.
단톡방에 공지한 코드를 넣어야 요청이 들어간다. 저장은 data/media_requests.json.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

import requests

CATEGORIES = ["드라마", "예능", "영화", "애니메이션"]
PLATFORMS = ["넷플릭스", "티빙", "디즈니+", "쿠팡플레이", "웨이브", "애플TV+", "왓챠", "방송(지상파·케이블)", "기타"]
STATUSES = {"new": "새 요청", "done": "완료", "hold": "보류"}
NAME_MAX, MEMO_MAX, TITLE_MAX = 30, 300, 100
TMDB_IMG = "https://image.tmdb.org/t/p"

# TMDB 장르: 16 애니메이션, 10764 리얼리티, 10767 토크, 10763 뉴스
_VARIETY_GENRES = {10764, 10767}

_lock = threading.Lock()


class RateLimiter:
    """접속지(IP)별로 window 초 동안 limit번까지"""

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now or time.time()
        with self.lock:
            hits = [t for t in self.hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self.hits[key] = hits
                return False
            hits.append(now)
            self.hits[key] = hits
            if len(self.hits) > 5000:  # 오래된 접속지 정리
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.window}
            return True


def guess_category(media_type: str, genre_ids: list[int]) -> str:
    if 16 in (genre_ids or []):
        return "애니메이션"
    if media_type == "movie":
        return "영화"
    if set(genre_ids or []) & _VARIETY_GENRES:
        return "예능"
    return "드라마"


def _item(m: dict, media_type: str) -> dict:
    date = m.get("release_date") if media_type == "movie" else m.get("first_air_date")
    title = m.get("title") if media_type == "movie" else m.get("name")
    genres = m.get("genre_ids") or [g.get("id") for g in m.get("genres") or []]
    return {
        "tmdb_id": m.get("id"),
        "media_type": media_type,
        "title": (title or "").strip(),
        "year": int(date[:4]) if (date or "")[:4].isdigit() else None,
        "poster": f"{TMDB_IMG}/w185{m['poster_path']}" if m.get("poster_path") else None,
        "overview": (m.get("overview") or "").strip()[:200],
        "category": guess_category(media_type, genres),
        "rating": round(m["vote_average"], 1) if m.get("vote_count") else None,
    }


def tmdb_search(api_key: str, query: str, limit: int = 8) -> list[dict]:
    """영화·TV를 같이 찾는다 (사람 결과는 뺌)"""
    query = (query or "").strip()[:TITLE_MAX]
    if not api_key or not query:
        return []
    res = requests.get("https://api.themoviedb.org/3/search/multi",
                       params={"api_key": api_key, "query": query, "language": "ko-KR", "include_adult": "false"},
                       timeout=8)
    res.raise_for_status()
    out = [_item(m, m["media_type"]) for m in res.json().get("results") or []
           if m.get("media_type") in ("movie", "tv")]
    return [m for m in out if m["title"]][:limit]


def tmdb_detail(api_key: str, media_type: str, tmdb_id: int) -> dict | None:
    """요청에 적힌 TMDB 번호로 서버에서 다시 찾는다 (페이지에서 보낸 제목·포스터는 믿지 않음)"""
    if not api_key or media_type not in ("movie", "tv"):
        return None
    res = requests.get(f"https://api.themoviedb.org/3/{media_type}/{int(tmdb_id)}",
                       params={"api_key": api_key, "language": "ko-KR"}, timeout=8)
    if res.status_code != 200:
        return None
    return _item(res.json(), media_type)


# ── 저장 ──────────────────────────────────────────────

def load(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(path: Path, items: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def add(path: Path, item: dict) -> dict:
    item = dict(item, id=secrets.token_hex(4), created=int(time.time()), status="new")
    with _lock:
        items = load(path)
        items.append(item)
        _save(path, items[-1000:])
    return item


def update(path: Path, req_id: str, **fields) -> dict | None:
    with _lock:
        items = load(path)
        for it in items:
            if it.get("id") == req_id:
                it.update(fields)
                _save(path, items)
                return it
    return None


def delete(path: Path, req_id: str) -> bool:
    with _lock:
        items = load(path)
        kept = [it for it in items if it.get("id") != req_id]
        if len(kept) == len(items):
            return False
        _save(path, kept)
        return True


# ── 완료 알림: 라이브러리에 받은 내용 한 줄 ─────────────────
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov"}
SUB_EXTS = {".srt", ".smi", ".ass", ".ssa", ".vtt"}
_EP_RE = re.compile(r"(?:^|[.\s_-])(?:S\d{1,2}[.\s]?)?E(\d{1,5})(?=[.\s\[\]_-]|$)", re.I)
_RES_RE = re.compile(r"(2160p|1080p|720p|4K)", re.I)
_SRC_RE = re.compile(r"(WEB-DL|WEBRip|BluRay|BDRip|HDTV|FHDRip|HDRip)", re.I)


def norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", re.sub(r"\s*\(\d{4}\)\s*$", "", s or "")).lower()


def _videos(folder: Path, depth: int = 2) -> list[Path]:
    out = []
    try:
        for p in folder.iterdir():
            if p.name.startswith(("@", ".")):
                continue
            if p.is_dir() and depth > 1:
                out += _videos(p, depth - 1)
            elif p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                out.append(p)
    except OSError:
        pass
    return out


def _episodes_text(eps: list[int]) -> str:
    eps = sorted(set(eps))
    if not eps:
        return ""
    if eps == list(range(eps[0], eps[-1] + 1)):
        return f"{eps[0]}~{eps[-1]}화" if len(eps) > 1 else f"{eps[0]}화"
    return f"{len(eps)}개 회차 (최신 {eps[-1]}화)"


def _quality_text(video: Path) -> str:
    parts = [m.group(1) for m in (_RES_RE.search(video.name), _SRC_RE.search(video.name)) if m]
    stem = video.stem
    has_sub = any(p.is_file() and p.suffix.lower() in SUB_EXTS and p.name.startswith(stem)
                  for p in video.parent.iterdir())
    if has_sub:
        parts.append("한국어 자막")
    return " · ".join(parts)


def find_series_folder(it: dict, rules: list[dict], base_paths: dict) -> Path | None:
    """분류 규칙의 폴더 중 요청 제목과 맞는 것, 없으면 분류 폴더 바로 아래에서 이름으로 찾는다"""
    key = norm(it.get("title", ""))
    if not key:
        return None
    base = base_paths.get(it.get("category"))
    for r in rules:
        if r.get("category") != it.get("category") or not base:
            continue
        sub = Path(r.get("subfolder") or "")
        # '먼작귀 (2022)/Season 1'이면 시즌 폴더 위 이름으로 비교
        name = norm(sub.parent.name if re.match(r"(?i)season\s*\d+$", sub.name) else sub.name)
        if name and (key in name or name in key):
            return Path(base) / sub
    if base and Path(base).is_dir():
        for d in Path(base).iterdir():
            if d.is_dir() and norm(d.name) and key in norm(d.name):
                return d
    return None


def library_line(it: dict, rules: list[dict], base_paths: dict, movie_root: Path, movie_jobs: list[dict]) -> str:
    """'📺 1~6화 · 1080p WEB-DL · 한국어 자막' / '🎞 1080p WEB-DL · 한국어 자막'. 못 찾으면 ''"""
    if it.get("category") == "영화":
        video = None
        for j in movie_jobs:   # 영화 받기로 받은 것 (TMDB 번호로 정확히)
            if it.get("tmdb_id") and j.get("tmdb_id") == it["tmdb_id"] and j.get("status") == "done" and j.get("video"):
                p = movie_root / j["folder"] / j["video"]
                if p.exists():
                    video = p
        if video is None and it.get("year"):
            from movies import initial_folder, safe_name
            title = safe_name(it["title"])
            cands = [p for p in _videos(movie_root / initial_folder(it["title"]), 1)
                     if p.name.startswith(title) and f"({it['year']})" in p.name]
            video = max(cands, key=lambda p: p.stat().st_size) if cands else None
        q = _quality_text(video) if video else ""
        return f"🎞 {q}" if q else ""
    folder = find_series_folder(it, rules, base_paths)
    if not folder or not folder.is_dir():
        return ""
    vids = _videos(folder)
    eps = [int(m.group(1)) for v in vids for m in [_EP_RE.search(v.name)] if m]
    text = _episodes_text(eps)
    if not text:
        return ""
    latest = max(vids, key=lambda v: v.stat().st_mtime)
    q = _quality_text(latest)
    return f"📺 {text}" + (f" · {q}" if q else "")


def done_caption(it: dict, detail: dict | None, library: str) -> str:
    """카톡방에 그대로 붙여 넣을 완료 안내"""
    head = (f"{it['title']} ({it['year']})" if it.get("year") else it["title"]) + f" · {it['category']}"
    if detail and detail.get("rating"):
        head += f" · ★{detail['rating']}"
    lines = ["🎬 요청하신 작품이 올라왔어요!", head]
    overview = (detail or {}).get("overview") or ""
    if overview:
        lines.append(overview if len(overview) <= 150 else overview[:149] + "…")
    if library:
        lines.append(library)
    lines.append(f"요청: {it['name']}님")
    return "\n".join(lines)


def validate(form: dict) -> tuple[dict | None, str | None]:
    """제출 값 검사 → (정리된 값, 오류 메시지)"""
    name = str(form.get("name") or "").strip()
    category = str(form.get("category") or "").strip()
    platform = str(form.get("platform") or "").strip()
    memo = str(form.get("memo") or "").strip()
    title = str(form.get("title") or "").strip()
    if not name:
        return None, "요청자 이름을 적어 주세요."
    if len(name) > NAME_MAX:
        return None, f"이름은 {NAME_MAX}자까지 적을 수 있어요."
    if category not in CATEGORIES:
        return None, "드라마·예능·영화·애니메이션 중에서 골라 주세요."
    if platform and platform not in PLATFORMS:
        return None, "플랫폼을 목록에서 골라 주세요."
    if len(memo) > MEMO_MAX:
        return None, f"메모는 {MEMO_MAX}자까지 적을 수 있어요."
    tmdb_id = form.get("tmdb_id")
    media_type = str(form.get("media_type") or "")
    if tmdb_id not in (None, ""):
        if not str(tmdb_id).isdigit() or media_type not in ("movie", "tv"):
            return None, "검색 결과를 다시 골라 주세요."
        tmdb_id = int(tmdb_id)
    else:
        tmdb_id, media_type = None, ""
        if not title:
            return None, "제목을 검색해서 고르거나 직접 적어 주세요."
    if len(title) > TITLE_MAX:
        return None, f"제목은 {TITLE_MAX}자까지 적을 수 있어요."
    return {"name": name, "category": category, "platform": platform, "memo": memo,
            "title": title, "tmdb_id": tmdb_id, "media_type": media_type}, None
