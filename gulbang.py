"""결방 자동 확인 (2026-10-16).

네이버에서 프로그램 이름을 검색하면 나오는 방송 정보 박스에 회차 목록
("823회 2026.10.18(일) …")이 있다. 다음 방송 예정일도 들어 있다.
규칙의 방송 요일상 방송했어야 하는 날짜가 이 목록에 없고, 그 앞뒤로 다른
방송일이 모두 있으면 결방으로 본다. 앞뒤 날짜가 없으면(목록이 아직 갱신 전이거나
정보 박스가 없는 프로그램) 판단하지 않고 "unknown"으로 둔다.

검증: 2026년 9월 아시안게임 중계로 런닝맨 09.20·09.27, 놀면 뭐하니 09.19~10.03이
목록에서 비어 있었고 실제 결방 기사와 일치했다.
"""
from __future__ import annotations

import html as htmllib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
SEARCH_URL = "https://search.naver.com/search.naver?query="
NEWS_URL = "https://search.naver.com/search.naver?where=news&sort=1&query="
CACHE_TTL = 3 * 3600          # 정보 박스 캐시 (초)
NEWS_TTL = 6 * 3600
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

_EPISODE_RE = re.compile(r"(\d{1,5})\s*회\s*(20\d\d)\.(\d\d)\.(\d\d)\.?\s*\(([월화수목금토일])\)")
_DATE_RE = re.compile(r"(20\d\d)\.(\d\d)\.(\d\d)\.?\s*\(([월화수목금토일])\)")

_lock = threading.Lock()
_mem: dict[str, tuple[float, dict]] = {}


def show_name(subfolder: str) -> str:
    """규칙의 저장 폴더명 → 검색할 프로그램 이름.
    "닥터X 하얀 마피아의 시대 (2026)" → "닥터X 하얀 마피아의 시대"
    "04.시리즈/명탐정 코난 (1996)" → "명탐정 코난"
    "유미의 세포들/Season03" → "유미의 세포들"
    """
    parts = [p.strip() for p in (subfolder or "").split("/") if p.strip()]
    parts = [p for p in parts if not re.match(r"^\d+\.", p)] or parts
    name = parts[0] if parts else ""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()


def _strip_tags(s: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
    s = htmllib.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s)


def parse_info_box(page: str) -> dict:
    """네이버 검색 결과 HTML → {"has_box", "episodes": [{"no", "date"}], "dates": [...]}.
    날짜는 정보 박스('편성정보') 뒤쪽 회차 목록에서만 찾는다."""
    start = page.find("편성정보")
    if start < 0:
        return {"has_box": False, "episodes": [], "dates": []}
    region = _strip_tags(page[start:start + 120_000])
    episodes, seen = [], set()
    for m in _EPISODE_RE.finditer(region):
        d = date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        if WEEKDAYS[d.weekday()] != m.group(5) or (int(m.group(1)), d) in seen:
            continue
        seen.add((int(m.group(1)), d))
        episodes.append({"no": int(m.group(1)), "date": d.isoformat()})
    dates = set(e["date"] for e in episodes)
    if not dates:
        for m in _DATE_RE.finditer(region):
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if WEEKDAYS[d.weekday()] == m.group(4):
                dates.add(d.isoformat())
    return {"has_box": True, "episodes": sorted(episodes, key=lambda e: e["date"]), "dates": sorted(dates)}


def judge(expected: date, dates: list[str], today: date) -> dict:
    """기대 방송일(규칙 요일 기준) 하나에 대한 판정.
    밤늦게 방송해서 규칙 요일이 하루 늦게 잡힌 경우도 있어 expected와 그 전날을 같이 본다.
    status: aired(방송됨) / scheduled(오늘 방송 예정) / gulbang(결방) / unknown(판단 불가)"""
    ds = sorted(date.fromisoformat(x) for x in dates)
    if len(ds) < 2:
        return {"status": "unknown", "why": "방송일 정보 없음"}
    candidates = [expected, expected - timedelta(days=1)]
    hit = next((c for c in candidates if c in ds), None)
    if hit is not None:
        if hit >= today:
            return {"status": "scheduled", "date": hit.isoformat(), "why": "오늘 방송 예정"}
        return {"status": "aired", "date": hit.isoformat(), "why": "방송됨"}
    lo, hi = min(candidates), max(candidates)
    before = [d for d in ds if d < lo]
    after = [d for d in ds if d > hi]
    if before and after:
        return {"status": "gulbang", "date": expected.isoformat(),
                "prev": before[-1].isoformat(), "next": after[0].isoformat(),
                "why": f"{before[-1].month}.{before[-1].day} 다음 방송이 {after[0].month}.{after[0].day}"}
    return {"status": "unknown", "why": "방송일 목록이 아직 갱신 전"}


def _fetch(url: str, timeout: float = 10.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _cache_get(key: str, ttl: float, cache_file: Path | None):
    now = time.time()
    with _lock:
        hit = _mem.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    if cache_file and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            ent = data.get(key)
            if ent and now - ent["t"] < ttl:
                with _lock:
                    _mem[key] = (ent["t"], ent["v"])
                return ent["v"]
        except Exception:
            pass
    return None


def _cache_put(key: str, value: dict, cache_file: Path | None):
    now = time.time()
    with _lock:
        _mem[key] = (now, value)
        if not cache_file:
            return
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
        except Exception:
            data = {}
        data = {k: v for k, v in data.items() if now - v.get("t", 0) < 2 * 86400}
        data[key] = {"t": now, "v": value}
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(cache_file)
        except Exception:
            pass


def info_box(name: str, cache_file: Path | None = None, fetch=_fetch) -> dict:
    key = "box:" + name
    hit = _cache_get(key, CACHE_TTL, cache_file)
    if hit is not None:
        return hit
    # 네이버 검색 결과는 1MB가 넘어 NAS에서 5초 넘게 걸릴 때가 있다. 한 번만 다시 시도.
    err = None
    for attempt in range(2):
        try:
            value = parse_info_box(fetch(SEARCH_URL + urllib.parse.quote(name), timeout=15.0))
            value["ok"] = True
            break
        except Exception as e:
            err = e
            time.sleep(1)
    else:
        # 실패는 캐시하지 않는다 (다음 요청에 다시 시도)
        return {"ok": False, "has_box": False, "episodes": [], "dates": [], "error": type(err).__name__}
    _cache_put(key, value, cache_file)
    return value


def news_reason(name: str, cache_file: Path | None = None, fetch=_fetch) -> str | None:
    """결방 이유가 담긴 최신 뉴스 문장 하나 (없으면 None)."""
    key = "news:" + name
    hit = _cache_get(key, NEWS_TTL, cache_file)
    if hit is not None:
        return hit.get("text")
    text = None
    try:
        page = fetch(NEWS_URL + urllib.parse.quote(f"{name} 결방"), timeout=15.0)
        short = re.sub(r"\s+", "", name)[:4]
        for m in re.finditer(r'class="[^"]*sds-comps-text-type-body1[^"]*"[^>]*>(.*?)</span>', page, re.S):
            sentence = _strip_tags(m.group(1)).strip()
            if "결방" in sentence and short and short in re.sub(r"\s+", "", sentence):
                text = sentence[:160]
                break
    except Exception:
        return None
    _cache_put(key, {"text": text}, cache_file)
    return text


def expected_date(day_name: str, today: date) -> date:
    """오늘 기준 최근 7일 안에서 그 요일의 날짜 (오늘 포함)."""
    target = WEEKDAYS.index(day_name)
    return today - timedelta(days=(today.weekday() - target) % 7)


def check_items(items: list[dict], today: date, cache_file: Path | None = None,
                fetch=_fetch, max_workers: int = 3) -> dict:
    """items: [{"rkday", "subfolder", "day"}] → {rkday: 판정}"""
    from concurrent.futures import ThreadPoolExecutor

    names = sorted({show_name(i["subfolder"]) for i in items if show_name(i["subfolder"])})
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        boxes = dict(zip(names, ex.map(lambda n: info_box(n, cache_file, fetch), names)))

    out = {}
    for it in items:
        name = show_name(it["subfolder"])
        box = boxes.get(name) or {}
        exp = expected_date(it["day"], today)
        if not box.get("ok"):
            res = {"status": "unknown", "why": f"네이버 조회 실패 ({box.get('error') or '?'})"}
        elif not box.get("has_box"):
            res = {"status": "unknown", "why": "네이버 방송 정보 없음"}
        elif not box.get("dates"):
            res = {"status": "unknown", "why": "네이버에 회차 목록 없음"}
        else:
            res = judge(exp, box.get("dates", []), today)
            if res["status"] == "gulbang":
                # 결방 다음 회차 번호(밀린 회차)를 같이 알려준다
                nxt = next((e for e in box.get("episodes", []) if e["date"] == res["next"]), None)
                if nxt:
                    res["next_no"] = nxt["no"]
                reason = news_reason(name, cache_file, fetch)
                if reason:
                    res["news"] = reason
        res["name"] = name
        res["expected"] = exp.isoformat()
        res["search_url"] = SEARCH_URL + urllib.parse.quote(f"{name} 결방")
        out[it["rkday"]] = res
    return out
