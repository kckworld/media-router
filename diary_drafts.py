# -*- coding: utf-8 -*-
"""생활 > 일기 (2026-11-03 사용자 요청): 사진 일기 초안을 날짜별로 보고, 복사하고, 고쳐 저장한다.

폴더 data/diary_drafts/ (호스트 /volume1/docker/media-router/data/diary_drafts, 755/644, kck9010 소유로)
- YYYY-MM-DD.draft.json   팀장(photo-diary)이 씀. MR은 읽기만. {"date","title","body","made_at","photos":[...],"memo_count"}
- YYYY-MM-DD.final.json   MR이 씀(사용자 [저장]). {"date","title","body","saved_at"}. 직전 것은 .final.prev.json 하나만
- YYYY-MM-DD.review.json  팀장이 씀(저장 뒤 1~2분). {"made_at","points":[...],"final_saved_at"}
- lessons.json            팀장이 씀(누적 교훈). {"updated","rules":[{"text","from","count"}]}
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.draft\.json$")
MAX_TITLE, MAX_BODY = 200, 20000
_lock = threading.Lock()


def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _own(p: Path, mode: int, ref: Path) -> None:
    try:
        os.chmod(p, mode)
        st = ref.stat()
        os.chown(p, st.st_uid, st.st_gid)
    except OSError:
        pass


def dates(base: Path) -> list[str]:
    try:
        return sorted((m.group(1) for p in base.iterdir() for m in [_DATE.match(p.name)] if m), reverse=True)
    except OSError:
        return []


def load(base: Path, day: str) -> dict:
    draft = _read(base / f"{day}.draft.json")
    final = _read(base / f"{day}.final.json")
    review = _read(base / f"{day}.review.json")
    # 리뷰는 지금 저장본에 대한 것만 보여 준다(전에 저장한 것에 대한 리뷰면 '학습 중')
    if review and final and review.get("final_saved_at") != final.get("saved_at"):
        review = None
    return {"date": day, "draft": draft if isinstance(draft, dict) else None,
            "final": final if isinstance(final, dict) else None,
            "review": review if isinstance(review, dict) else None,
            "lessons": _read(base / "lessons.json") or {"rules": []}}


def save_final(base: Path, day: str, title: str, body: str, now: datetime) -> tuple[dict | None, str | None]:
    if day not in dates(base):
        return None, "그 날 초안이 없어요."
    title, body = (title or "").strip(), (body or "").replace("\r\n", "\n").strip()
    if not title or not body:
        return None, "제목과 본문을 채워 주세요."
    if len(title) > MAX_TITLE or len(body) > MAX_BODY:
        return None, "글이 너무 길어요."
    item = {"date": day, "title": title, "body": body, "saved_at": now.isoformat(timespec="seconds")}
    with _lock:
        p = base / f"{day}.final.json"
        if p.exists():
            prev = base / f"{day}.final.prev.json"
            os.replace(p, prev)
            _own(prev, 0o644, base)
        tmp = base / f".{day}.final.tmp"
        tmp.write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")
        _own(tmp, 0o644, base)
        os.replace(tmp, p)
    return item, None
