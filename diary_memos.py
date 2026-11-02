# -*- coding: utf-8 -*-
"""일기 메모 (2026-11-02 사용자 요청): MR /diary에서 적는 사진 일기용 메모.

저장: data/diary_memos/YYYY-MM-DD.jsonl, 한 줄 {"id", "time": ISO+09:00, "text", "src": "web"}.
사진 일기(/volume1/docker/photo-diary, kck9010 계정)가 텔레그램 메모와 같이 읽는다 → 디렉터리 755·파일 644,
소유자는 data 폴더 주인(kck9010)으로 맞춘다(컨테이너는 root).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path

MAX_LEN = 500
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_lock = threading.Lock()


def _own(p: Path, mode: int, ref: Path) -> None:
    try:
        os.chmod(p, mode)
        st = ref.stat()
        os.chown(p, st.st_uid, st.st_gid)
    except OSError:
        pass


def valid_date(d: str) -> bool:
    if not _DATE.match(d or ""):
        return False
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def path_for(base: Path, day: str) -> Path:
    return base / f"{day}.jsonl"


def load(base: Path, day: str) -> list[dict]:
    p = path_for(base, day)
    out = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("text"):
            out.append(d)
    return sorted(out, key=lambda d: d.get("time", ""))


def clean(text: str) -> str:
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(text or "")).replace("\r\n", "\n").strip()
    return t


def add(base: Path, text: str, now: datetime) -> tuple[dict | None, str | None]:
    t = clean(text)
    if not t:
        return None, "적을 내용을 써 주세요."
    if len(t) > MAX_LEN:
        return None, f"{MAX_LEN}자까지 적을 수 있어요."
    item = {"id": secrets.token_hex(4), "time": now.isoformat(timespec="seconds"), "text": t, "src": "web"}
    with _lock:
        if not base.exists():
            base.mkdir(parents=True, exist_ok=True)
            _own(base, 0o755, base.parent)
        p = path_for(base, now.strftime("%Y-%m-%d"))
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        _own(p, 0o644, base.parent)
    return item, None


def delete(base: Path, day: str, memo_id: str) -> bool:
    """웹에서 적은 메모만 지운다(그 줄 빼고 파일 다시 쓰기)"""
    if not valid_date(day):
        return False
    p = path_for(base, day)
    with _lock:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        kept, hit = [], False
        for line in lines:
            try:
                d = json.loads(line)
            except ValueError:
                kept.append(line)
                continue
            if isinstance(d, dict) and d.get("id") == memo_id and d.get("src") == "web":
                hit = True
                continue
            kept.append(line)
        if not hit:
            return False
        tmp = p.with_name("." + p.name + ".tmp")
        tmp.write_text("".join(x + "\n" for x in kept), encoding="utf-8")
        _own(tmp, 0o644, base.parent)
        os.replace(tmp, p)
    return True


def shift(day: str, days: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
