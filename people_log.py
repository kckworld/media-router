# -*- coding: utf-8 -*-
"""검색직 검색 로그 (2026-11-07, 사용자 결정 11-05: "누가 무슨 검색어로 언제 검색했는지").

- MR 검색은 data/people_search_log.jsonl(600)에 한 줄씩. 봇 로그(employee-bot state/search_log/search_log.jsonl)와 같은 형식:
  {"time": "YYYY-MM-DD HH:MM:SS"(한국 시간), "source": "MR"|"bot", "user_id", "name", "mode"(한글), "query",
   "result_count", "allowed", "note"?}
- 결과 내용(전화·메일·사번 등)은 절대 안 남긴다 — 이 모듈은 위 필드만 받는다.
- 보관: 180일 넘은 줄은 하루 한 번 지우고, 20MB 넘으면 오래된 줄부터 16MB까지 줄임(봇과 같은 규칙).
- 읽기는 두 파일을 합쳐 최근 순. 봇 파일은 읽기 전용 마운트라 읽기만.
"""
from __future__ import annotations

import csv
import io
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

KEEP_DAYS = 180
MAX_BYTES = 20 * 1024 * 1024
TRIM_TO = 16 * 1024 * 1024
MAX_QUERY = 100
FIELDS = ("time", "source", "user_id", "name", "username", "mode", "query", "result_count", "allowed", "note")
NOTE_LABELS = {"not_approved": "승인 안 됨", "rate_limited": "횟수 제한", "data_error": "데이터 못 읽음"}

_lock = threading.Lock()
_pruned_day = {"day": ""}


def _clean(rec: dict) -> dict:
    out = {}
    for k in FIELDS:
        v = rec.get(k)
        if v is None or v == "":
            continue
        if k == "query":
            v = str(v)[:MAX_QUERY]
        elif k in ("result_count",):
            v = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
            if v is None:
                continue
        elif k == "allowed":
            v = bool(v)
        else:
            v = str(v)[:60]
        out[k] = v
    return out


def _prune(path: Path, now: datetime) -> None:
    """180일 넘은 줄 지우기 + 크기 상한. 같은 폴더 임시 파일에 쓰고 교체(600 유지)"""
    try:
        size = path.stat().st_size
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    cut = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    keep = []
    for ln in lines:
        try:
            t = str(json.loads(ln).get("time") or "")
        except ValueError:
            continue
        if t >= cut:
            keep.append(ln)
    total = sum(len(ln.encode("utf-8")) + 1 for ln in keep)
    if size > MAX_BYTES or total > MAX_BYTES:
        while keep and total > TRIM_TO:
            total -= len(keep.pop(0).encode("utf-8")) + 1
    if len(keep) == len(lines) and size <= MAX_BYTES:
        return
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(ln + "\n" for ln in keep))
    os.replace(tmp, path)


def append(path: Path, rec: dict, now: datetime) -> None:
    """한 줄 추가(파일 600). 하루 처음 쓸 때·20MB 넘을 때 정리"""
    row = _clean(dict(rec, time=now.strftime("%Y-%m-%d %H:%M:%S")))
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        day = now.strftime("%Y-%m-%d")
        try:
            big = path.stat().st_size > MAX_BYTES
        except OSError:
            big = False
        if _pruned_day["day"] != day or big:
            _pruned_day["day"] = day
            _prune(path, now)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _read(path: Path, source: str) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for ln in text.splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("time"):
            r = _clean(r)
            r.setdefault("source", source)
            out.append(r)
    return out


def read_all(mr_path: Path, bot_path: Path | None) -> tuple[list[dict], dict]:
    """두 파일 합쳐 최근 순. 두 번째 값은 각 파일 상태(있음/없음) — 화면에 '봇 로그 아직 없음' 알리려고"""
    rows = _read(mr_path, "MR")
    st = {"mr": mr_path.exists(), "bot": bool(bot_path and bot_path.exists())}
    if bot_path:
        rows += _read(bot_path, "bot")
    rows.sort(key=lambda r: r.get("time", ""), reverse=True)
    return rows, st


def filter_rows(rows: list[dict], date_from: str = "", date_to: str = "", person: str = "", query: str = "",
                source: str = "") -> list[dict]:
    date_from, date_to = (date_from or "")[:10], (date_to or "")[:10]
    person, query = (person or "").strip().lower(), (query or "").strip().lower()
    out = []
    for r in rows:
        day = str(r.get("time", ""))[:10]
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        if source and r.get("source") != source:
            continue
        if person and person not in " ".join(str(r.get(k, "")) for k in ("name", "username", "user_id")).lower():
            continue
        if query and query not in str(r.get("query", "")).lower():
            continue
        out.append(r)
    return out


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s       # 엑셀 수식 끼워넣기 막기


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["시각", "출처", "사람", "@아이디", "ID(텔레그램·MR 계정)", "종류", "검색어", "결과 수", "허용", "비고"])
    for r in rows:
        w.writerow([_cell(r.get("time")), "봇" if r.get("source") == "bot" else "MR", _cell(r.get("name")),
                    _cell(r.get("username")), _cell(r.get("user_id")), _cell(r.get("mode")), _cell(r.get("query")),
                    "" if r.get("result_count") is None else r.get("result_count"),
                    "예" if r.get("allowed", True) else "아니오", NOTE_LABELS.get(r.get("note"), _cell(r.get("note")))])
    return "﻿" + buf.getvalue()                                           # 엑셀에서 한글 안 깨지게 BOM
