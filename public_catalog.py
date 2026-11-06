# -*- coding: utf-8 -*-
"""공개 사이트(request.kckworld.com, 2026-11-07): NAS 미디어 목록을 Jellyfin에서 가져와 캐시한다.

- 공개로 나가는 건 제목·종류·연도·시즌 수·화수·상태·추가일·포스터 유무뿐. 파일 경로·서버 주소·사용자 이름·토큰은 없음.
- Jellyfin 키는 서버(.env)에만. 포스터도 서버가 받아 JPEG로 다시 저장해 /pub/poster/<id>로 준다(메타데이터 제거).
- 목록은 3시간마다 새로 받음(뒤에서). 재시작하면 data/pub_catalog.json에서 바로 읽음.
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REFRESH_SECONDS = 3 * 3600
TIMEOUT = 30
RECENT_DAYS = 14
ID_RE = re.compile(r"^[0-9a-f]{32}$")
KINDS = [("1.", "영화"), ("2.", "드라마"), ("3.", "예능"), ("4", "애니"), ("5.", "어린이"), ("6.", "다큐")]
STATUS_KO = {"Continuing": "방영 중", "Ended": "완결"}

_state = {"data": None, "loading": False, "error": ""}
_lock = threading.Lock()


class CatalogError(Exception):
    pass


def configured() -> bool:
    return bool(os.getenv("JELLYFIN_URL") and os.getenv("JELLYFIN_API_KEY"))


def _get(path: str, raw: bool = False, **q):
    q["api_key"] = os.environ["JELLYFIN_API_KEY"]
    url = os.environ["JELLYFIN_URL"].rstrip("/") + path + "?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "media-router"}), timeout=TIMEOUT) as r:
            body = r.read()
    except Exception as e:                                   # URL엔 키가 있어 종류만
        raise CatalogError(type(e).__name__) from None
    if raw:
        return body
    try:
        return json.loads(body)
    except ValueError:
        raise CatalogError("응답 형식") from None


def kind_of(view_name: str) -> str:
    for prefix, label in KINDS:
        if (view_name or "").startswith(prefix):
            return label
    return "기타"


def norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def _admin_uid() -> str:
    users = _get("/Users")
    admins = [u for u in users if (u.get("Policy") or {}).get("IsAdministrator")]
    if not (admins or users):
        raise CatalogError("사용자 없음")
    return (admins or users)[0]["Id"]


def fetch() -> dict:
    uid = _admin_uid()
    items, ids = [], set()
    for v in _get(f"/Users/{uid}/Views").get("Items") or []:
        kind = kind_of(v.get("Name") or "")
        d = _get(f"/Users/{uid}/Items", ParentId=v["Id"], Recursive="true", IncludeItemTypes="Movie,Series",
                 Fields="ChildCount,RecursiveItemCount,ProductionYear,DateCreated,Status,AirDays,SortName")
        for it in d.get("Items") or []:
            iid = str(it.get("Id") or "")
            if not ID_RE.match(iid) or iid in ids:
                continue
            ids.add(iid)
            series = it.get("Type") == "Series"
            items.append({"id": iid, "name": str(it.get("Name") or "")[:120], "kind": kind,
                          "type": "series" if series else "movie", "year": it.get("ProductionYear"),
                          "seasons": it.get("ChildCount") if series else None,
                          "episodes": it.get("RecursiveItemCount") if series else None,
                          "status": STATUS_KO.get(it.get("Status") or "", "") if series else "",
                          "air_days": [str(x) for x in (it.get("AirDays") or [])][:7],
                          "added": str(it.get("DateCreated") or "")[:10],
                          "poster": "Primary" in (it.get("ImageTags") or {})})
    # 최근 추가: 에피소드·영화를 추가일 순으로 받아 시리즈별로 묶음
    since = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    lat = _get(f"/Users/{uid}/Items", Recursive="true", IncludeItemTypes="Episode,Movie", SortBy="DateCreated",
               SortOrder="Descending", Limit=600, Fields="DateCreated")
    recent, seen = [], {}
    for it in lat.get("Items") or []:
        day = str(it.get("DateCreated") or "")[:10]
        if day < since:
            break
        key = str(it.get("SeriesId") or it.get("Id") or "")
        if not ID_RE.match(key) or key not in ids:
            continue
        if key in seen:
            seen[key]["count"] += 1
            continue
        seen[key] = {"id": key, "date": day, "count": 1}
        recent.append(seen[key])
    d = {"made": int(time.time()), "items": items, "recent": recent, "recent_src": "jellyfin"}
    try:                                                      # 11-07: 최근 추가는 Plex addedAt 기준(재인코딩으로 파일만 바뀐 옛 회차가 안 올라오게)
        pr = plex_recent(d)
        if pr is not None:
            d.update(recent=pr, recent_src="plex")
    except CatalogError:
        pass
    return d


def _plex_kind(section_title: str, section_type: str) -> str:
    t = section_title or ""
    for word, label in (("애니", "애니"), ("드라마", "드라마"), ("예능", "예능"), ("다큐", "다큐"), ("어린이", "어린이")):
        if word in t:
            return label
    return "영화" if section_type == "movie" else ""


def plex_recent(d: dict) -> list[dict] | None:
    """Plex 구역마다 addedAt 최신순 회차·영화 → 14일 안, 시리즈별로 묶어(가장 최근 추가일·회차) Jellyfin 목록 항목(포스터용)에 이어 붙임.
    Plex 설정이 없거나 못 부르면 None(그러면 Jellyfin 날짜로)"""
    if not (os.getenv("PLEX_URL") and os.getenv("PLEX_TOKEN")):
        return None
    import xml.etree.ElementTree as ET

    def get(path, **q):
        q["X-Plex-Token"] = os.environ["PLEX_TOKEN"]
        url = os.environ["PLEX_URL"].rstrip("/") + path + "?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/xml"}), timeout=TIMEOUT) as r:
                return ET.fromstring(r.read())
        except Exception as e:
            raise CatalogError("plex " + type(e).__name__) from None
    since = time.time() - RECENT_DAYS * 86400
    rows = []
    for s in get("/library/sections"):
        typ = {"show": 4, "movie": 1}.get(s.get("type") or "")
        if not typ:
            continue
        kind = _plex_kind(s.get("title") or "", s.get("type") or "")
        root = get(f"/library/sections/{s.get('key')}/all", type=typ, sort="addedAt:desc",
                   **{"X-Plex-Container-Start": 0, "X-Plex-Container-Size": 400 if typ == 4 else 100})
        for v in root:
            added = int(v.get("addedAt") or 0)
            if added < since:
                break
            show = typ == 4
            rows.append({"title": v.get("grandparentTitle") if show else v.get("title"), "kind": kind, "added": added,
                         "key": (v.get("grandparentRatingKey") if show else v.get("ratingKey")) or "",
                         "season": v.get("parentIndex"), "ep": v.get("index")})
    rows.sort(key=lambda r: -r["added"])
    out, seen = [], {}
    for r in rows:
        k = (r["key"], r["kind"])
        if k in seen:
            seen[k]["count"] += 1
            continue
        it = find_title(d, r["title"] or "", r["kind"])
        if not it or norm(it["name"]) != norm(r["title"] or ""):    # 이름이 정확히 같은 것만(엉뚱한 포스터 방지)
            seen[k] = {"count": 0}
            continue
        last = f"{int(r['ep'])}화" if (r.get("ep") or "").isdigit() else ""
        seen[k] = {"id": it["id"], "date": datetime.fromtimestamp(r["added"], timezone(timedelta(hours=9))).strftime("%Y-%m-%d"), "count": 1, "last": last}
        out.append(seen[k])
    return [x for x in out if x.get("id")]


def load_file(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("items"), list) else None
    except (OSError, ValueError):
        return None


def _save(path: Path, d: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def refresh(path: Path) -> None:
    try:
        d = fetch()
        _save(path, d)
        with _lock:
            _state.update(data=d, error="")
    except CatalogError as e:
        with _lock:
            _state["error"] = str(e)
    finally:
        with _lock:
            _state["loading"] = False


def get(path: Path, background: bool = True) -> dict | None:
    """캐시된 목록. 없거나 3시간 지났으면 뒤에서 새로 받기 시작(첫 요청은 파일 또는 None)"""
    with _lock:
        d = _state["data"]
    if d is None:
        d = load_file(path)
        with _lock:
            _state["data"] = _state["data"] or d
    stale = d is None or time.time() - int(d.get("made") or 0) > REFRESH_SECONDS
    if stale and configured() and background:
        with _lock:
            start = not _state["loading"]
            _state["loading"] = True
        if start:
            threading.Thread(target=refresh, args=(path,), daemon=True).start()
    return d


def by_id(d: dict | None) -> dict:
    return {it["id"]: it for it in (d or {}).get("items") or []}


def search(d: dict | None, q: str, limit: int = 40) -> list[dict]:
    nq = norm(q)
    if not nq or not d:
        return []
    hits = [it for it in d.get("items") or [] if nq in norm(it["name"])]
    hits.sort(key=lambda it: (norm(it["name"]) != nq, not norm(it["name"]).startswith(nq), len(it["name"]), it["name"]))
    return hits[:limit]


def find_title(d: dict | None, title: str, kind: str = "") -> dict | None:
    """제목에 맞는 작품(띄어쓰기·기호 무시). kind를 주면 그 종류를 먼저 본다.
    11-07: '원피스'(애니 규칙)가 드라마 '원피스'(실사 8화)에, '코난'은 '명탐정 코난'을 못 찾던 것 고침 —
    ① 같은 이름·같은 종류 시리즈 ② 같은 이름·같은 종류 ③ (종류를 줬으면) 같은 종류 시리즈 중 이름에 제목이 든 것, 화수 가장 많은 것
    ④ 같은 이름 시리즈(아무 종류) ⑤ 이름이 제목으로 시작(3자 이상)"""
    nt = norm(title)
    if not nt or not d:
        return None
    items = d.get("items") or []
    exact = [it for it in items if norm(it["name"]) == nt]
    for pick in ([it for it in exact if it["kind"] == kind and it["type"] == "series"], [it for it in exact if it["kind"] == kind]):
        if kind and pick:
            return pick[0]
    if kind:
        part = [it for it in items if it["kind"] == kind and it["type"] == "series" and nt in norm(it["name"])]
        if part:
            return max(part, key=lambda it: (it.get("episodes") or 0, -len(it["name"])))
    for it in exact:
        if it["type"] == "series":
            return it
    if exact:
        return exact[0]
    if len(nt) >= 3:
        return next((it for it in items if norm(it["name"]).startswith(nt)), None)
    return None


def poster_file(cache_dir: Path, d: dict | None, item_id: str) -> Path | None:
    """목록에 있는 id만. 처음이면 Jellyfin에서 받아 JPEG(최대 높이 450)로 다시 저장"""
    if not ID_RE.match(item_id or "") or item_id not in by_id(d) or not by_id(d)[item_id].get("poster"):
        return None
    p = cache_dir / f"{item_id}.jpg"
    if p.exists() and time.time() - p.stat().st_mtime < 7 * 86400:
        return p
    from PIL import Image
    raw = _get(f"/Items/{item_id}/Images/Primary", raw=True, maxHeight=450, quality=85)
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        if im.width * im.height > 4000 * 4000:
            return None
        im = im.convert("RGB")
        im.thumbnail((600, 450))
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        im.save(tmp, "JPEG", quality=82, optimize=True)
        os.replace(tmp, p)
    except Exception:
        return None
    return p
