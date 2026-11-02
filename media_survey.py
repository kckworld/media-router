# -*- coding: utf-8 -*-
"""단톡방 설문 (2026-11-02 사용자 요청): 공개 /survey 페이지 + MR 관리자 집계.

1분 안에 끝나게 이름만 필수, 나머지는 칩(버튼) 위주. 같은 이름으로 다시 내면 마지막 답으로 덮어쓴다.
저장은 data/survey_answers.json (요청 기록 media_requests.json과 같은 방식).
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

APPS = ["Plex", "Jellyfin", "둘 다"]
OTTS = ["넷플릭스", "티빙", "웨이브", "쿠팡플레이", "디즈니+", "왓챠", "애플TV+", "유튜브 프리미엄", "없음"]
REASONS = ["OTT에 없는 걸 보려고", "OTT 구독 대신 여기서 다 해결", "둘 다 조금씩"]
GENRES = ["드라마", "예능", "영화", "애니", "해외드라마", "다큐", "어린이"]
PAINS = ["끊김·버퍼링", "자막", "찾기 어려움", "화질", "로그인", "없음"]

# 관리자 집계·CSV에 쓰는 항목 (키, 제목, 종류)
FIELDS = [
    ("name", "이름", "text"), ("app", "쓰는 앱", "one"), ("plex_id", "Plex 아이디", "text"),
    ("jellyfin_id", "Jellyfin 아이디", "text"), ("otts", "보고 있는 OTT", "many"), ("ott_etc", "OTT 기타", "text"),
    ("reason", "kckworld를 보는 이유", "one"), ("reason_etc", "이유 기타", "text"), ("genres", "주로 보는 것", "many"),
    ("recommend", "최근 재미있게 본 것", "text"), ("pains", "불편했던 점", "many"), ("pain_etc", "불편 기타", "text"),
    ("improve", "개선했으면 하는 것", "text"), ("say", "하고 싶은 말", "text"),
]
PRIVATE = ("name", "plex_id", "jellyfin_id")   # 보기 전용 계정에는 가린다

_lock = threading.Lock()


def _clean(v, n: int) -> str:
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(v or "")).strip()[:n]


def _pick(v, allowed: list[str]) -> list[str]:
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if x in allowed and x not in out:
            out.append(x)
    return out


def norm_name(name: str) -> str:
    return re.sub(r"\s+", "", name or "").lower()


def validate(form: dict) -> tuple[dict | None, str | None]:
    name = _clean(form.get("name"), 30)
    if not name:
        return None, "이름을 적어 주세요."
    app = form.get("app") if form.get("app") in APPS else ""
    d = {
        "name": name, "app": app,
        "plex_id": _clean(form.get("plex_id"), 60) if app in ("Plex", "둘 다") else "",
        "jellyfin_id": _clean(form.get("jellyfin_id"), 60) if app in ("Jellyfin", "둘 다") else "",
        "otts": _pick(form.get("otts"), OTTS), "ott_etc": _clean(form.get("ott_etc"), 60),
        "reason": form.get("reason") if form.get("reason") in REASONS else "",
        "reason_etc": _clean(form.get("reason_etc"), 100),
        "genres": _pick(form.get("genres"), GENRES),
        "recommend": _clean(form.get("recommend"), 500),
        "pains": _pick(form.get("pains"), PAINS), "pain_etc": _clean(form.get("pain_etc"), 100),
        "improve": _clean(form.get("improve"), 1000), "say": _clean(form.get("say"), 1000),
    }
    if "없음" in d["otts"] and len(d["otts"]) > 1:
        d["otts"] = [x for x in d["otts"] if x != "없음"]
    if "없음" in d["pains"] and (len(d["pains"]) > 1 or d["pain_etc"]):
        d["pains"] = [x for x in d["pains"] if x != "없음"]
    if d["reason_etc"]:
        d["reason"] = ""          # 기타를 적었으면 기타가 답
    return d, None


# ── 저장 ──────────────────────────────────────────────

def load(path: Path) -> list[dict]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(path: Path, items: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def upsert(path: Path, data: dict) -> tuple[dict, bool]:
    """같은 이름(띄어쓰기·대소문자 무시)이 있으면 덮어쓴다. (답, 덮어썼는지)"""
    now = int(time.time())
    with _lock:
        items = load(path)
        key = norm_name(data["name"])
        for i, it in enumerate(items):
            if norm_name(it.get("name", "")) == key:
                items[i] = dict(data, id=it["id"], created=it.get("created", now), updated=now,
                                times=it.get("times", 1) + 1)
                _save(path, items)
                return items[i], True
        item = dict(data, id=secrets.token_hex(4), created=now, updated=now, times=1)
        items.append(item)
        _save(path, items[-2000:])
        return item, False


def delete(path: Path, ids: list[str]) -> int:
    with _lock:
        items = load(path)
        kept = [it for it in items if it.get("id") not in ids]
        if len(kept) != len(items):
            _save(path, kept)
        return len(items) - len(kept)


def merge(path: Path, ids: list[str], keep: str) -> dict | None:
    """이름 오타로 갈라진 답 합치기: keep 답을 남기고, 다른 답에서 keep이 비운 칸만 채운 뒤 나머지는 지운다"""
    with _lock:
        items = load(path)
        by = {it["id"]: it for it in items}
        if keep not in by or not all(i in by for i in ids):
            return None
        base = by[keep]
        others = sorted((by[i] for i in ids if i != keep), key=lambda it: it.get("updated", 0), reverse=True)
        for o in others:
            for k, _, _ in FIELDS:
                if not base.get(k) and o.get(k):
                    base[k] = o[k]
        base["times"] = sum(by[i].get("times", 1) for i in set(ids) | {keep})
        drop = {i for i in ids if i != keep}
        _save(path, [it for it in items if it["id"] not in drop])
        return base


# ── 집계·보기 ──────────────────────────────────────────

def mask(it: dict) -> dict:
    """보기 전용 계정용: 이름은 첫 글자만, 계정 아이디는 있었는지만"""
    m = dict(it)
    n = (it.get("name") or "").strip()
    m["name"] = (n[0] + "*" * max(len(n) - 1, 1)) if n else ""
    for k in ("plex_id", "jellyfin_id"):
        if m.get(k):
            m[k] = "(있음)"
    return m


def aggregate(items: list[dict]) -> dict:
    """칩 항목은 [(값, 개수)] 많은 순, 글 항목은 (이름, 글) 모음"""
    def count(key, options, etc=None):
        c = {o: 0 for o in options}
        for it in items:
            v = it.get(key)
            for x in (v if isinstance(v, list) else [v] if v else []):
                c[x] = c.get(x, 0) + 1
            if etc and it.get(etc):
                c["기타"] = c.get("기타", 0) + 1
        return sorted(c.items(), key=lambda kv: -kv[1])
    def texts(key):
        return [{"name": it.get("name", ""), "text": it[key]} for it in sorted(items, key=lambda x: -x.get("updated", 0))
                if it.get(key)]
    return {
        "total": len(items),
        "chips": [
            {"title": "쓰는 앱", "rows": count("app", APPS)},
            {"title": "보고 있는 OTT", "rows": count("otts", OTTS, "ott_etc")},
            {"title": "kckworld를 보는 이유", "rows": count("reason", REASONS, "reason_etc")},
            {"title": "주로 보는 것", "rows": count("genres", GENRES)},
            {"title": "불편했던 점", "rows": count("pains", PAINS, "pain_etc")},
        ],
        "texts": [
            {"title": "OTT 기타", "rows": texts("ott_etc")},
            {"title": "보는 이유 기타", "rows": texts("reason_etc")},
            {"title": "최근 재미있게 본 것", "rows": texts("recommend")},
            {"title": "불편 기타", "rows": texts("pain_etc")},
            {"title": "개선했으면 하는 것", "rows": texts("improve")},
            {"title": "하고 싶은 말", "rows": texts("say")},
        ],
    }


def to_csv(items: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["제출 시각"] + [title for _, title, _ in FIELDS] + ["낸 횟수"])
    for it in sorted(items, key=lambda x: x.get("updated", 0)):
        row = [time.strftime("%Y-%m-%d %H:%M", time.localtime(it.get("updated", 0)))]
        for k, _, kind in FIELDS:
            v = it.get(k)
            row.append(", ".join(v) if isinstance(v, list) else (v or ""))
        w.writerow(row + [it.get("times", 1)])
    return "﻿" + buf.getvalue()     # 엑셀에서 한글 안 깨지게 BOM


def one_line(it: dict) -> str:
    """텔레그램 알림용 한 줄 요약"""
    parts = [it.get("app") or "앱 안 고름"]
    if it.get("otts") or it.get("ott_etc"):
        parts.append("OTT " + ", ".join(it.get("otts", []) + ([it["ott_etc"]] if it.get("ott_etc") else [])))
    if it.get("genres"):
        parts.append("주로 " + ", ".join(it["genres"]))
    pains = [p for p in it.get("pains", []) if p != "없음"] + ([it["pain_etc"]] if it.get("pain_etc") else [])
    if pains:
        parts.append("불편 " + ", ".join(pains))
    return " · ".join(parts)
