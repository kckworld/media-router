# -*- coding: utf-8 -*-
"""공개 사이트 '계정 요청' (2026-11-07): Plex·Jellyfin 새 계정을 달라는 요청을 받아 둔다(계정은 자동으로 안 만듦).

data/account_requests.json(600): {"items": [{"id", "at", "name", "contact", "services", "memo", "status", "decided_at"}]}
status: 대기 · 승인 · 거절. 승인해도 계정 생성은 사용자가 직접(다음 단계).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

SERVICES = {"plex": "Plex", "jellyfin": "Jellyfin"}
STATUSES = ("대기", "승인", "거절")
MAX_ITEMS = 500
_lock = threading.Lock()


def _load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("items"), list) else {"items": []}
    except (OSError, ValueError):
        return {"items": []}


def _save(path: Path, d: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+$")
JF_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,20}$")


def validate(form) -> tuple[dict | None, str]:
    """11-07(W-15): 서비스 하나만 — Plex는 직접 가입한 계정 이메일(라이브러리 공유 요청), Jellyfin은 희망 아이디(관리자가 만듦)"""
    name = " ".join(str(form.get("name") or "").split())[:20]
    contact = " ".join(str(form.get("contact") or "").split())[:50]
    memo = str(form.get("memo") or "").strip()[:200]
    service = str(form.get("service") or "").strip()
    if not name:
        return None, "이름(닉네임)을 적어 주세요."
    if service not in SERVICES:
        return None, "Plex·Jellyfin 중 하나를 골라 주세요."
    data = {"name": name, "contact": contact, "memo": memo, "service": service, "services": [service]}
    if service == "plex":
        email = str(form.get("email") or "").strip()[:120]
        if not EMAIL_RE.match(email):
            return None, "Plex 계정 이메일을 정확히 적어 주세요(예: name@example.com)."
        data["email"] = email
    else:
        uid = str(form.get("username") or "").strip()
        if not JF_ID_RE.match(uid):
            return None, "희망 아이디는 영문·숫자·_·- 로 3~20자예요."
        data["username"] = uid
    return data, ""


def add(path: Path, data: dict, now: datetime) -> dict:
    with _lock:
        d = _load(path)
        item = dict(data, id="A" + secrets.token_hex(4), at=now.strftime("%Y-%m-%d %H:%M"), status="대기", decided_at="")
        d["items"] = ([item] + d["items"])[:MAX_ITEMS]
        _save(path, d)
    return item


def listing(path: Path) -> list[dict]:
    return _load(path)["items"]


def set_status(path: Path, item_id: str, status: str, now: datetime) -> dict | None:
    if status not in STATUSES:
        return None
    with _lock:
        d = _load(path)
        it = next((x for x in d["items"] if x.get("id") == item_id), None)
        if not it:
            return None
        it.update(status=status, decided_at=now.strftime("%Y-%m-%d %H:%M") if status != "대기" else "")
        _save(path, d)
    return it


def label(services: list[str]) -> str:
    return "·".join(SERVICES.get(s, s) for s in services)
