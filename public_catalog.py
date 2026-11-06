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
    return {"made": int(time.time()), "items": items, "recent": recent}


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


def find_title(d: dict | None, title: str) -> dict | None:
    """자동 받기 목록의 제목에 맞는 시리즈(띄어쓰기·기호 무시, 같은 이름 우선)"""
    nt = norm(title)
    if not nt or not d:
        return None
    best = None
    for it in d.get("items") or []:
        n = norm(it["name"])
        if n == nt and it["type"] == "series":
            return it
        if best is None and (n == nt or (len(nt) >= 3 and n.startswith(nt))):
            best = it
    return best


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
