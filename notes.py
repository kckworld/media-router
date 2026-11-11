# -*- coding: utf-8 -*-
"""메모장 (2026-11-11 사용자): 나만 쓰는 웹 동기화 메모 — 제목·본문·사진, 고정, 복사, 공유(폰 공유 창 + 읽기 전용 링크).

- 저장: data/notes/notes.json {"notes": [{id, title, body, pinned, created, updated, share}]} (저장 전 notes.json.bak 1개)
- 사진: data/notes/img/<16자리>.jpg — 긴 쪽 1600px JPEG로 다시 저장(EXIF·위치 정보 없어짐). 본문에는 한 줄 [[사진:이름]]로 넣는다.
- 공유 링크: share = 비밀값(22자). /pub/n/<비밀값>에서 그 메모만 읽기 전용, 사진도 그 메모에 들어 있는 것만. 끄면 바로 안 열림.
- 다른 기기에서 그 사이 고쳤으면(updated가 다르면) 덮어쓰지 않는다(conflict).
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
TITLE_MAX = 120
BODY_MAX = 50000
NOTES_MAX = 3000
IMG_MAX_BYTES = 10 * 1024 * 1024      # MR 전체 업로드 한도(MAX_CONTENT_LENGTH)와 같게 — 화면이 미리 줄여서 올린다
IMG_SIDE = 1600
IMG_RE = re.compile(r"\[\[사진:([0-9a-f]{16}\.jpg)\]\]")
_ID = re.compile(r"^[0-9a-f]{12}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,40}$")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def _file(d: Path) -> Path:
    return Path(d) / "notes.json"


def load(d: Path) -> list[dict]:
    try:
        x = json.loads(_file(d).read_text(encoding="utf-8"))
        return [n for n in x.get("notes", []) if isinstance(n, dict) and _ID.match(str(n.get("id", "")))]
    except (OSError, ValueError, AttributeError):
        return []


def _save(d: Path, notes: list[dict]) -> None:
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    f = _file(d)
    if f.exists():
        try:
            (d / "notes.json.bak").write_bytes(f.read_bytes())
        except OSError:
            pass
    tmp = d / "notes.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        json.dump({"notes": notes}, fp, ensure_ascii=False, indent=1)
    os.replace(tmp, f)


def sorted_notes(notes: list[dict]) -> list[dict]:
    """고정 먼저, 그다음 최근에 고친 순"""
    by_time = sorted(notes, key=lambda n: n.get("updated", ""), reverse=True)
    return [n for n in by_time if n.get("pinned")] + [n for n in by_time if not n.get("pinned")]


def _clean(title, body) -> tuple[str, str, str]:
    t = str(title or "").replace("\r", "").replace("\n", " ").strip()[:TITLE_MAX]
    b = str(body or "").replace("\r\n", "\n")
    if len(b) > BODY_MAX:
        return t, b, f"본문이 너무 길어요({BODY_MAX:,}자까지)."
    return t, b, ""


def create(d: Path, title="", body="") -> tuple[dict | None, str]:
    t, b, err = _clean(title, body)
    if err:
        return None, err
    with _lock:
        notes = load(d)
        if len(notes) >= NOTES_MAX:
            return None, "메모가 너무 많아요."
        now = _now()
        n = {"id": secrets.token_hex(6), "title": t, "body": b, "pinned": False, "created": now, "updated": now, "share": "",
             "rev": 1}
        notes.append(n)
        _save(d, notes)
    return n, ""


def update(d: Path, nid: str, base: str, title=None, body=None, pinned=None) -> tuple[dict | None, str]:
    """base = 화면이 받은 updated. 그 사이 바뀌었으면 ('conflict' 오류, 현재 메모)"""
    with _lock:
        notes = load(d)
        n = next((x for x in notes if x["id"] == nid), None)
        if not n:
            return None, "메모를 찾지 못했어요."
        # base = 화면이 받은 rev(고칠 때마다 1씩). 초 단위 시각은 같은 1초 안의 두 번 저장을 못 가려서 rev로 비교
        if base and str(base) != str(n.get("rev", 1)) and (title is not None or body is not None):
            return n, "conflict"
        if title is not None or body is not None:
            t, b, err = _clean(n["title"] if title is None else title, n["body"] if body is None else body)
            if err:
                return None, err
            if t == n["title"] and b == n["body"] and pinned is None:
                return n, ""
            n["title"], n["body"] = t, b
        if pinned is not None:
            n["pinned"] = bool(pinned)
        n["updated"] = _now()
        n["rev"] = int(n.get("rev", 1)) + 1
        _save(d, notes)
    return n, ""


def delete(d: Path, nid: str) -> bool:
    with _lock:
        notes = load(d)
        n = next((x for x in notes if x["id"] == nid), None)
        if not n:
            return False
        notes.remove(n)
        _save(d, notes)
        used = {m for x in notes for m in IMG_RE.findall(x.get("body", ""))}
    for name in set(IMG_RE.findall(n.get("body", ""))) - used:      # 다른 메모가 안 쓰는 사진만 지움
        try:
            (Path(d) / "img" / name).unlink()
        except OSError:
            pass
    return True


def set_share(d: Path, nid: str, on: bool) -> tuple[dict | None, str]:
    with _lock:
        notes = load(d)
        n = next((x for x in notes if x["id"] == nid), None)
        if not n:
            return None, "메모를 찾지 못했어요."
        if on and not n.get("share"):
            n["share"] = secrets.token_urlsafe(16)
        elif not on:
            n["share"] = ""
        _save(d, notes)
    return n, ""


def by_token(d: Path, token: str) -> dict | None:
    if not _TOKEN.match(token or ""):
        return None
    return next((n for n in load(d) if n.get("share") and secrets.compare_digest(n["share"], token)), None)


def save_image(d: Path, data: bytes) -> tuple[str, str]:
    """(이름, 오류). 긴 쪽 1600px JPEG로 다시 저장 — 사진 정보(EXIF·위치)는 남지 않는다"""
    if not data or len(data) > IMG_MAX_BYTES:
        return "", "사진이 없거나 너무 커요(10MB까지)."
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im)               # 폰 사진 회전 맞추기
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((IMG_SIDE, IMG_SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85, optimize=True)
    except Exception:
        return "", "사진으로 읽을 수 없는 파일이에요."
    img = Path(d) / "img"
    img.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(8) + ".jpg"
    (img / name).write_bytes(out.getvalue())
    return name, ""


def image_path(d: Path, name: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{16}\.jpg", name or ""):
        return None
    p = Path(d) / "img" / name
    return p if p.is_file() else None


def plain_text(n: dict) -> str:
    """복사·폰 공유용 글: 제목 + 본문(사진 자리는 '(사진)')"""
    body = IMG_RE.sub("(사진)", n.get("body", "")).strip()
    return (n.get("title", "").strip() + ("\n\n" if n.get("title") and body else "") + body).strip()


def parts(body: str) -> list[dict]:
    """본문을 [{'t': 글} | {'img': 이름}] 조각으로 (공유 화면이 서버에서 그림)"""
    out, pos = [], 0
    for m in IMG_RE.finditer(body or ""):
        if m.start() > pos:
            out.append({"t": body[pos:m.start()]})
        out.append({"img": m.group(1)})
        pos = m.end()
    if pos < len(body or ""):
        out.append({"t": body[pos:]})
    return out
