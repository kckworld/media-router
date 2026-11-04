# -*- coding: utf-8 -*-
"""생활 > 검색직 (2026-11-04 사용자 요청, 본인만): 검색직 봇(/volume1/docker/employee-bot, 인사팀 담당)과 같은 직원 검색.

데이터는 employee-bot/data/output.json 하나만 MR 컨테이너에 읽기 전용으로 붙인다(/app/ext/employees.json).
메모리에만 두고(사본·캐시 파일 없음) 파일 수정 시각이 바뀌면 다시 읽는다. 검색어·결과는 어디에도 기록하지 않는다.
검색 방식은 봇 app.py run_search와 같다(이름은 10명 넘으면 목록만 — 봇 MAX_DETAIL_RESULTS).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

MODES = [("search_name", "이름"), ("search_org", "소속"), ("search_empYmd", "입사일"), ("search_birthYear", "생년"),
         ("search_birthMonth", "생월"), ("search_jikwee", "직위"), ("search_jikchak", "직책"), ("search_jikgub", "직군")]
FIELD_BY_MODE = {"search_name": "name", "search_org": "orignOrgNm", "search_jikwee": "jikweeNm",
                 "search_jikchak": "jikchakNm", "search_jikgub": "jikgubNm"}
MAX_DETAIL = 10
MAX_LIST = 100        # 11-04 인사팀 권고·사용자 결정: 300 → 100
DETAIL_FIELDS = ("name", "orignOrgNm", "prevOrgNm", "jikweeNm", "jikchakNm", "jikgubNm", "statusNm", "sabun",
                 "empYmd", "birYmd", "handPhone", "officeTel", "mailId")
LIST_FIELDS = ("name", "orignOrgNm", "jikgubNm", "birYmd", "sabun")

_cache = {"mtime": None, "data": None}
_lock = threading.Lock()


def load(path: Path):
    """파일 수정 시각이 바뀌었을 때만 다시 읽는다. 못 읽으면 None"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _lock:
        if _cache["mtime"] != mtime:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            _cache.update(mtime=mtime, data=data if isinstance(data, list) else [])
        return _cache["data"]


def run_search(employees: list, mode: str, value: str) -> list:
    """봇 app.py run_search와 같은 규칙"""
    if mode in FIELD_BY_MODE:
        field, v = FIELD_BY_MODE[mode], value.upper()
        return [e for e in employees if v in (e.get(field) or "").upper()]
    if mode == "search_empYmd":
        return [e for e in employees if e.get("empYmd") == value]
    if mode == "search_birthYear":
        return [e for e in employees if (e.get("birYmd") or "").startswith(value)]
    if mode == "search_birthMonth":
        target = value.rjust(2, "0")
        return [e for e in employees if (e.get("birYmd") or "")[4:6] == target]
    v = value.upper()
    return [e for e in employees if v in (e.get("name") or "").upper()]


def search(employees: list, mode: str, value: str) -> dict:
    """{total, detail:[...]} (이름 검색이고 10명 이하) 또는 {total, list:[...]} (생년월일 순, 최대 MAX_LIST)
    목록 줄의 key는 사번, 사번이 없으면(조직도로 추가된 약 100명) 메일 — 눌러서 상세 볼 때 쓴다"""
    value = (value or "").strip()
    if mode not in dict(MODES):
        mode = "search_name"
    if value.startswith("이름 "):
        mode, value = "search_name", value[3:].strip()
    if not value:
        return {"total": 0, "mode": mode, "list": []}
    res = run_search(employees, mode, value)
    if mode == "search_name" and len(res) <= MAX_DETAIL:
        return {"total": len(res), "mode": mode, "detail": [{k: e.get(k) for k in DETAIL_FIELDS} for e in res]}
    res = sorted(res, key=lambda e: e.get("birYmd") or "")
    return {"total": len(res), "mode": mode,
            "list": [dict({k: e.get(k) for k in LIST_FIELDS}, key=_key(e)) for e in res[:MAX_LIST]]}


def _key(e: dict) -> str:
    return str(e.get("sabun") or e.get("mailId") or "")


def detail(employees: list, key: str) -> dict | None:
    """사번으로, 사번이 없는 사람은 메일로 찾는다"""
    key = str(key or "").strip()
    if not key:
        return None
    for e in employees:
        if _key(e) == key:
            return {k: e.get(k) for k in DETAIL_FIELDS}
    return None
