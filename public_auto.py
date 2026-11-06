# -*- coding: utf-8 -*-
"""공개 사이트 '자동으로 받는 것' (2026-11-07): 지금 자동으로 새 회차를 받는 프로그램 목록(제목·종류·요일·포스터만).

- 예능: bt4g/bt4g_keywords.txt(한 줄에 키워드, ' 720p' 뗌) — tfreeca.py RSS가 받음
- 드라마: bt4g/drama_keywords.txt('키워드 | 릴'에서 키워드만)
- 애니 등: qBittorrent RSS 자동 받기 규칙 중 '예능'·'드라마' 말고 켜진 것(규칙 이름 = 제목, 예: 먼작귀·원피스·코난)
- 포스터: NAS 목록(public_catalog)에 있으면 그 포스터, 없으면 TMDB 포스터 주소(이름으로 검색, data/pub_tmdb.json에 캐시)
- 규칙 내용(검색어 패턴·폴더)·릴 이름은 공개로 안 내보냄.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import public_catalog

GENERIC_RULES = {"예능", "드라마"}
DAYS_KO = {"Monday": "월", "Tuesday": "화", "Wednesday": "수", "Thursday": "목", "Friday": "금", "Saturday": "토", "Sunday": "일"}
TMDB_RETRY_SECONDS = 7 * 86400
_tmdb_lock = threading.Lock()
_tmdb_state = {"running": False}


def _lines(path: Path) -> list[str]:
    try:
        return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.strip().startswith("#")]
    except OSError:
        return []


def titles(variety_path: Path, drama_path: Path, rules: dict | None) -> list[dict]:
    out, seen = [], set()

    def add(title: str, kind: str):
        t = re.sub(r"\s*720p\s*$", "", title.partition("|")[0].strip(), flags=re.I).strip()[:60]
        k = public_catalog.norm(t)
        if t and k not in seen:
            seen.add(k)
            out.append({"title": t, "kind": kind})
    for ln in _lines(variety_path):
        add(ln, "예능")
    for ln in _lines(drama_path):
        add(ln, "드라마")
    for name, r in sorted((rules or {}).items()):
        if name in GENERIC_RULES or not isinstance(r, dict) or not r.get("enabled"):
            continue
        add(str(name), "애니")
    return out


def _load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def tmdb_fill(cache_path: Path, names: list[str], lookup) -> None:
    """lookup(name) → {"poster": url|None} | None. 이미 있거나 7일 안에 못 찾은 이름은 건너뜀"""
    cache = _load(cache_path)
    now = int(time.time())
    changed = False
    for n in names:
        hit = cache.get(n)
        if hit and (hit.get("poster") or now - int(hit.get("at") or 0) < TMDB_RETRY_SECONDS):
            continue
        try:
            m = lookup(n) or {}
        except Exception:
            m = {}
        poster = m.get("poster")
        cache[n] = {"poster": poster if isinstance(poster, str) and poster.startswith("https://image.tmdb.org/") else None, "at": now}
        changed = True
    if changed:
        tmp = cache_path.with_name(cache_path.name + ".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, cache_path)


def build(entries: list[dict], catalog: dict | None, cache_path: Path, lookup=None, background: bool = True) -> list[dict]:
    if lookup and not background:                             # (시험·처음 한 번) 그 자리에서 채운 뒤 그림
        need = [e["title"] for e in entries if not (public_catalog.find_title(catalog, e["title"]) or {}).get("poster")]
        tmdb_fill(cache_path, need, lookup)
        lookup = None
    tm = _load(cache_path)
    out, missing = [], []
    for e in entries:
        it = public_catalog.find_title(catalog, e["title"])
        row = {"name": e["title"], "kind": e["kind"], "type": "series", "poster": False, "poster_url": None, "in_nas": False,
               "days": "", "episodes": None, "seasons": None, "status": "", "id": ""}
        if it:
            row.update(id=it["id"], poster=bool(it.get("poster")), in_nas=True, episodes=it.get("episodes"), seasons=it.get("seasons"),
                       status=it.get("status") or "", days="·".join(DAYS_KO.get(x, "") for x in it.get("air_days") or [] if x in DAYS_KO))
        if not row["poster"]:
            row["poster_url"] = (tm.get(e["title"]) or {}).get("poster")
            if row["poster_url"] is None and e["title"] not in tm:
                missing.append(e["title"])
        out.append(row)
    if missing and lookup:
        if background:
            with _tmdb_lock:
                start = not _tmdb_state["running"]
                _tmdb_state["running"] = True
            if start:
                def run():
                    try:
                        tmdb_fill(cache_path, missing, lookup)
                    finally:
                        with _tmdb_lock:
                            _tmdb_state["running"] = False
                threading.Thread(target=run, daemon=True).start()
        else:
            tmdb_fill(cache_path, missing, lookup)
    order = {"예능": 0, "드라마": 1, "애니": 2}
    out.sort(key=lambda r: (order.get(r["kind"], 9), r["name"]))
    return out
