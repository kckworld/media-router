"""티프리카 영화 베스트 → 텔레그램에서 골라 받기 (2026-10-26)

텔레그램 "🎞 영화 베스트" 버튼 → 일간·주간 베스트 목록(인라인 버튼) → 누르면
TMDB에서 한글 제목·연도를 찾아 qBittorrent에 /downloads/_movies/<해시>로 넣는다.
받은 파일은 호스트 배치(movie_mover.py)가 "제목 (연도)."를 앞에 붙여
02.영화-정리/<첫 글자 자음 폴더>로 옮긴다. 이 파일은 web_admin(컨테이너)과
movie_mover(호스트) 양쪽에서 쓰므로 requests 말고는 의존하지 않는다.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from pathlib import Path

import requests

TFREECA_BASE = os.getenv("TFREECA_BASE", "https://www.tfreeca22.com").rstrip("/")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
WEEK_LIMIT = 10

MOVIE_LIBRARY = Path("/volume1/video/02.영화-정리")
# qBittorrent 컨테이너 기준 경로와 호스트 경로 (/volume2/torrent/download -> /downloads)
QBIT_MOVIE_DIR = "/downloads/_movies"
HOST_MOVIE_DIR = Path("/volume2/torrent/download/_movies")
# 컨테이너에서는 같은 폴더를 읽기 전용으로 붙여서 "다 받고 옮기기 대기 중"인지 본다
MOVIE_DL_DIR = Path(os.getenv("MOVIE_DL_DIR", str(HOST_MOVIE_DIR)))
MOVIE_DIR_NAME = "_movies"  # media_router.py가 이 폴더는 건드리지 않는다

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov"}
SUB_EXTS = {".srt", ".smi", ".ass", ".ssa", ".sub", ".idx", ".vtt"}

_jobs_lock = threading.Lock()


# ── 티프리카 ──────────────────────────────────────────

def _fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA, "Referer": TFREECA_BASE + "/"}, timeout=15)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def parse_daily_best(list_html: str) -> list[dict]:
    """영화 목록 페이지 위쪽 '일간 베스트' 상자 (5개)"""
    i = list_html.find("<!--베스트-->")
    j = list_html.find("주간 베스트", i)
    if i < 0 or j < 0:
        return []
    return _parse_links(list_html[i:j], r'title="([^"]*)"')


def parse_week_best(top_html: str, limit: int = WEEK_LIMIT) -> list[dict]:
    """top100.php?b_id=tmovie&hit=Week"""
    return _parse_links(top_html, r'class="[^"]*">\s*([^<]+?)\s*</a>')[:limit]


def _parse_links(part: str, title_re: str) -> list[dict]:
    out, seen = [], set()
    for pid, title in re.findall(r'b_id=tmovie&id=(\d+)[^"]*"\s*' + title_re, part):
        if pid not in seen:
            seen.add(pid)
            out.append({"id": pid, "title": html.unescape(title).strip()})
    return out


def parse_list(list_html: str) -> list[dict]:
    """영화 게시판 목록 (글 id, 제목, 올린 시각 'HH:MM' 또는 'MM-DD')"""
    out = []
    for pid, title, when in re.findall(
            r'mode=view&b_id=tmovie&id=(\d+)[^"]*" class="stitle\d">\s*(.*?)\s*</a>.*?class="datetime">([^<]*)<',
            list_html, re.S):
        out.append({"id": pid, "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(), "when": when.strip()})
    return out


def fetch_list(page: int = 1) -> list[dict]:
    # 영화만 목록 주소가 mode=lists (mode=list는 비로그인에 빈 목록)
    return parse_list(_fetch(f"{TFREECA_BASE}/board.php?mode=lists&b_id=tmovie&page={page}"))


def fetch_best() -> dict:
    """{'day': [...], 'week': [...]} - 주간에서 일간과 겹치는 글은 뺀다"""
    day = parse_daily_best(_fetch(f"{TFREECA_BASE}/board.php?mode=lists&b_id=tmovie"))
    week = parse_week_best(_fetch(f"{TFREECA_BASE}/top100.php?b_id=tmovie&hit=Week"), WEEK_LIMIT + len(day))
    ids = {m["id"] for m in day}
    return {"day": day, "week": [m for m in week if m["id"] not in ids][:WEEK_LIMIT]}


def parse_view(view_html: str) -> dict:
    """글 상세: 제목, 마그넷, 따로 붙은 자막 파일 이름"""
    m = re.search(r"magnet:\?xt=urn:btih:([A-Za-z0-9]{32,40})", view_html)
    subs = re.findall(r">\s*([^<>]+\.(?:smi|srt|ass))\s*</a>", view_html, re.I)
    t = re.search(r"<title>\s*(.*?)\s*</title>", view_html, re.S)
    return {"btih": m.group(1).lower() if m else None,
            "subs": [html.unescape(s).strip() for s in subs],
            "page_title": html.unescape(t.group(1)) if t else ""}


def fetch_view(post_id: str) -> dict:
    return parse_view(_fetch(f"{TFREECA_BASE}/board.php?mode=view&b_id=tmovie&id={post_id}"))


# ── 제목 → TMDB ──────────────────────────────────────

_YEAR_RE = re.compile(r"(?<![\dA-Za-z])((?:19|20)\d{2})(?![\dp])")


def split_post_title(title: str) -> tuple[str, str, int | None]:
    """'인턴 The.Intern.2026.1080p.FHD' → ('인턴', 'The Intern', 2026)"""
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", title)  # [무삭제] 같은 말머리
    head = re.match(r"[^A-Za-z]*", t).group(0)  # 영문이 나오기 전까지 = 한글 제목
    rest = t[len(head):]
    ym = _YEAR_RE.search(t)
    year = int(ym.group(1)) if ym else None
    if ym and ym.start() < len(head):
        # '외계+인 1부 (2022)'처럼 연도가 한글 쪽에 있으면 거기까지만 제목
        ko, en_src = head[:ym.start()], rest
    else:
        ko, en_src = head, rest[:ym.start() - len(head)] if ym else rest
    ko = re.sub(r"[\s(（\[.:：,-]+$", "", ko).strip()
    en = re.split(r"\b(?:2160p|1080p|720p|480p|WEB|BluRay|HDRip|FHD)\b", en_src, 1, flags=re.I)[0]
    en = re.sub(r"[._]+", " ", en).strip(" -([")
    return ko, en, year


def _pick(results: list[dict], year: int | None) -> dict | None:
    def y(m):
        d = m.get("release_date") or ""
        return int(d[:4]) if d[:4].isdigit() else None
    if year:
        # TMDB는 year를 줘도 다른 해 영화를 먼저 주기도 한다 ('인턴' 2026 → 2015년작이 1등)
        for delta in (0, 1):
            for m in results:
                if y(m) is not None and abs(y(m) - year) == delta:
                    return m
        return None
    return results[0] if results else None


def tmdb_lookup(api_key: str, ko: str, en: str, year: int | None) -> dict | None:
    """{'id', 'title'(한글), 'year', 'poster', 'rating', 'overview'} 또는 None"""
    if not api_key:
        return None
    for q in (ko, en):
        if not q:
            continue
        params = {"api_key": api_key, "query": q, "language": "ko-KR", "include_adult": "false"}
        if year:
            params["year"] = year
        try:
            res = requests.get("https://api.themoviedb.org/3/search/movie", params=params, timeout=8).json()
        except Exception:
            continue
        m = _pick(res.get("results") or [], year)
        if m and (m.get("release_date") or "")[:4].isdigit():
            return {"id": m["id"], "title": m.get("title") or q, "year": int(m["release_date"][:4]),
                    "poster": f"{TMDB_IMG}/w342{m['poster_path']}" if m.get("poster_path") else None,
                    "rating": round(m["vote_average"], 1) if m.get("vote_count") else None,
                    "overview": (m.get("overview") or "").strip()}
    return None


TMDB_IMG = "https://image.tmdb.org/t/p"


def namu_url(title: str) -> str:
    """나무위키는 봇 접근을 막아서 문서가 있는지 확인할 수 없으므로 검색 링크로 준다"""
    return "https://namu.wiki/Search?q=" + requests.utils.quote(f"{title} 영화")


def enrich(items: list[dict], api_key: str, cache_path: Path, workers: int = 6) -> list[dict]:
    """베스트·목록 항목에 TMDB 정보(display, poster, rating, overview)와 나무위키 링크를 붙인다.
    글 번호별로 data/movie_tmdb_cache.json에 저장해서 같은 글은 다시 찾지 않는다."""
    from concurrent.futures import ThreadPoolExecutor
    with _jobs_lock:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    todo = [m for m in items if m["id"] not in cache]

    def look(m):
        ko, en, year = split_post_title(m["title"])
        found = tmdb_lookup(api_key, ko, en, year)
        if found:
            return m["id"], found
        return m["id"], {"title": ko or en or m["title"], "year": year}

    if todo and api_key:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for pid, info in ex.map(look, todo):
                cache[pid] = info
        with _jobs_lock:
            if len(cache) > 2000:  # 오래된 글부터 버림 (글 번호가 커질수록 최신)
                cache = dict(sorted(cache.items(), key=lambda kv: int(kv[0]))[-1500:])
            tmp = cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cache_path)
    out = []
    for m in items:
        info = cache.get(m["id"]) or {}
        title = info.get("title") or split_post_title(m["title"])[0] or m["title"]
        out.append(dict(m, display=make_prefix(title, info.get("year")), poster=info.get("poster"),
                        rating=info.get("rating"), overview=info.get("overview") or "",
                        namu=namu_url(safe_name(title))))
    return out


def safe_name(title: str) -> str:
    """파일·폴더 이름에 못 쓰는 글자 정리 (콜론은 ' -'로, TMDB "'텐징' - Tenzing"의 따옴표는 뺌)"""
    t = re.sub(r"\s*[:：]\s*", " - ", title)
    t = re.sub(r'[\\/*?"<>|\'‘’“”]', "", t)
    return re.sub(r"\s+", " ", t).strip(" .")


_CHOSEONG_FOLDERS = ["가", "가", "나", "다", "다", "라", "마", "바", "바", "사", "사",
                     "아", "자", "자", "차", "카", "타", "파", "하"]


def initial_folder(title: str) -> str:
    """한글 첫 글자의 자음 폴더 (ㄲ→가처럼 된소리는 예사소리 폴더). 한글이 아니면 1-Z"""
    for ch in title:
        if ch.isspace() or ch in "[]()'\"":
            continue
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            return _CHOSEONG_FOLDERS[code // 588]
        return "1-Z"
    return "1-Z"


def make_prefix(title: str, year: int | None, tmdb_id: int | None = None) -> str:
    """'인턴 (2026) {tmdb-607833}' - Plex Movie 에이전트는 {tmdb-번호}가 있으면 검색 없이 그 영화로 매칭한다"""
    base = f"{safe_name(title)} ({year})" if year else safe_name(title)
    return f"{base} {{tmdb-{tmdb_id}}}" if tmdb_id else base


# ── 작업 목록 (data/movie_jobs.json) ───────────────────

def load_jobs(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_jobs(path: Path, jobs: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(tmp, 0o666)  # 컨테이너(root)와 호스트 배치(root/kck9010)가 같이 쓴다
    except OSError:
        pass
    os.replace(tmp, path)


def update_jobs(path: Path, fn) -> list[dict]:
    """컨테이너(텔레그램 버튼)와 호스트 배치가 같은 파일을 고치므로 파일 잠금까지 건다"""
    import fcntl
    lock_path = str(path) + ".lock"
    # 컨테이너는 root, 웹 '배치 실행' 버튼의 호스트 배치는 kck9010 → 누가 먼저 만들든 둘 다 열 수 있게 666
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        os.fchmod(fd, 0o666)
    except OSError:
        pass  # 소유자가 아니면 못 바꿈 - 이미 666이면 상관없다
    with _jobs_lock, os.fdopen(fd, "r+") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        jobs = load_jobs(path)
        fn(jobs)
        save_jobs(path, jobs)
        return jobs


# ── 받은 파일 옮기기 (호스트) ──────────────────────────

def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 1
    while True:
        cand = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if not cand.exists():
            return cand
        n += 1


def is_complete(job_dir: Path) -> bool:
    """qBittorrent는 다 받은 뒤에야 incomplete에서 저장 폴더로 옮기므로, 영상이 있고
    받는 중 표시(.!qB)가 없으면 끝난 것"""
    if not job_dir.is_dir():
        return False
    files = [p for p in job_dir.rglob("*") if p.is_file()]
    if any(p.suffix.lower() in (".!qb", ".part") for p in files):
        return False
    return any(p.suffix.lower() in VIDEO_EXTS for p in files)


# 티프리카 마그넷은 해시만 있어서 DHT로만 피어를 찾는다. VPN 재연결 뒤 UDP가 막혀 DHT가 0이면
# (2026-10-27 메이데이) 메타데이터 단계에서 멈추므로 공개 트래커를 같이 붙인다 (http 하나 포함).
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://explodie.org:6969/announce",
    "http://tracker.opentrackr.org:1337/announce",
]


def magnet(btih: str) -> str:
    from urllib.parse import quote
    return f"magnet:?xt=urn:btih:{btih}" + "".join(f"&tr={quote(t, safe='')}" for t in PUBLIC_TRACKERS)


def post_url(post_id: str) -> str:
    return f"{TFREECA_BASE}/board.php?mode=view&b_id=tmovie&id={post_id}"


# 글에 따로 붙은 자막을 사용자가 받아 Drive에 넣으면 drama_name.sh가 임시/download로 옮긴다
SUB_SEARCH_DIRS = [Path("/volume1/video/임시/download"), Path("/volume1/homes/kck9010/Drive"),
                   Path("/volume2/torrent/download")]
SUB_WAIT_SEC = 14 * 24 * 3600  # 영화를 옮긴 뒤 자막을 이만큼 기다린다


def _sub_key(name: str) -> str:
    """'프로포즈 The Proposal 2009 ... BONE (1).smi' → 비교용 (브라우저가 붙인 ' (1)'·기호·띄어쓰기 무시)"""
    stem = Path(name).stem
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    return re.sub(r"[\W_]+", "", stem).lower()


def find_subtitles(sub_names: list[str], search_dirs=None) -> list[Path]:
    """job['subs'](티프리카 첨부 자막 이름)와 같은 이름의 자막 파일을 찾는다"""
    want = {_sub_key(n) for n in sub_names}
    found = []
    for d in search_dirs or SUB_SEARCH_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if MOVIE_DIR_NAME in p.parts or "@eaDir" in p.parts:
                continue
            if p.is_file() and p.suffix.lower() in SUB_EXTS and _sub_key(p.name) in want:
                found.append(p)
    return found


def place_subtitles(subs: list[Path], video: Path, move=None) -> list[Path]:
    """자막을 영화 파일과 같은 이름(확장자만 자막)으로 영화 옆에 둔다"""
    import shutil
    move = move or shutil.move
    placed = []
    for p in subs:
        dest = _unique(video.with_suffix(p.suffix.lower()))
        move(str(p), str(dest))
        try:
            os.chmod(dest, 0o664)
        except OSError:
            pass
        placed.append(dest)
    return placed


# 토렌트 안 'Subs/English.srt' 같은 자막의 언어 → Plex가 읽는 언어 표시
_SUB_LANGS = [
    (r"simplified|chs|简体|zh-?cn|zh-?hans", "zh"),
    (r"traditional|cht|繁體|繁体|zh-?tw|zh-?hant", "zh-TW"),
    (r"korean|\bkor\b|\bko\b|한국어", "ko"),
    (r"english|\beng\b|\ben\b", "en"),
    (r"japanese|\bjpn\b|\bja\b", "ja"),
    (r"chinese|\bchi\b|\bzho\b|\bzh\b", "zh"),
]


def sub_lang(name: str) -> str | None:
    s = Path(name).stem.lower().replace("_", " ").replace(".", " ")
    return next((code for pat, code in _SUB_LANGS if re.search(pat, s)), None)


def _sample(p: Path) -> bool:
    return bool(re.search(r"(^|[.\s_-])sample([.\s_-]|$)", p.stem, re.I))


def place_files(job_dir: Path, prefix: str, dest_dir: Path, move=None) -> list[Path]:
    """영상·자막 파일 이름 앞에 'prefix.'를 붙여 dest_dir로 옮긴다. 샘플은 뺀다.
    영상과 이름이 다른 자막(YTS의 'Subs/English.srt' 등)은 '<영상 이름>.<언어>.srt'로 바꿔야
    Plex가 그 영화 자막으로 붙인다."""
    import shutil
    move = move or shutil.move
    dest_dir.mkdir(parents=True, exist_ok=True)
    files = [p for p in sorted(job_dir.rglob("*"))
             if p.is_file() and p.suffix.lower() in VIDEO_EXTS | SUB_EXTS and not _sample(p)]
    videos = [p for p in files if p.suffix.lower() in VIDEO_EXTS]
    main = max(videos, key=lambda p: p.stat().st_size) if videos else None
    main_name = (main.name if main.name.startswith(prefix + ".") else f"{prefix}.{main.name}") if main else ""
    placed = []
    for p in files:
        name = p.name if p.name.startswith(prefix + ".") else f"{prefix}.{p.name}"
        if main and p.suffix.lower() in SUB_EXTS and not p.stem.lower().startswith(main.stem.lower()):
            # 모르는 언어면 원래 이름을 꼬리로 ('Thai.srt' → '<영상>.Thai.srt')
            lang = sub_lang(p.name) or re.sub(r"[^\w-]+", "", Path(p.name).stem)[:20] or "sub"
            name = f"{Path(main_name).stem}.{lang}{p.suffix.lower()}"
        dest = _unique(dest_dir / name)
        move(str(p), str(dest))
        try:
            os.chmod(dest, 0o664)
        except OSError:
            pass
        placed.append(dest)
    return placed
