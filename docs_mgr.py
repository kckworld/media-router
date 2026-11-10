# -*- coding: utf-8 -*-
"""설정 > Claude > [문서] (2026-11-10 사용자): 규칙·기록·보류·작업 파일·Claude 메모리(.md)를 보고 고치기. 소유자만.

- 다루는 곳은 정해진 폴더(ROOTS)의 바로 아래 *.md만. 하위 폴더·숨김 파일·심볼릭 링크·다른 확장자 거부.
- 저장 전 바로 전 내용을 <폴더>/.history/<이름>.<시각>로 남긴다(파일마다 최근 20개).
- 화면이 읽은 뒤 세션이 파일을 바꿨으면(sha 다름) 덮어쓰지 않는다(409).
- 세션들은 단일 파일을 rename하지 않고 쓰기도 하므로 여기서도 제자리 덮어쓰기(같은 inode, 단일 파일 마운트에 안전).
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

MAX_BYTES = 400 * 1024
KEEP_HISTORY = 20
_NAME = re.compile(r"^[^/\\\x00.][^/\\\x00]{0,150}\.md$")


OWNER_UID, OWNER_GID = 1026, 100      # kck9010:users — MR 컨테이너(root)가 새로 만든 파일도 세션이 고칠 수 있게


def own(p: Path) -> None:
    try:
        os.chown(p, OWNER_UID, OWNER_GID)
    except OSError:
        pass


def _root(roots: dict, key: str) -> Path | None:
    p = roots.get(key)
    return Path(p) if p else None


def _path(roots: dict, key: str, name: str) -> Path | None:
    root = _root(roots, key)
    if root is None or not _NAME.match(name or ""):
        return None
    p = root / name
    try:
        if p.is_symlink() or p.resolve().parent != root.resolve():
            return None
    except OSError:
        return None
    return p


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def listing(roots: dict, labels: dict) -> list[dict]:
    out = []
    for key, root in roots.items():
        root = Path(root)
        files = []
        try:
            for p in root.iterdir():
                if p.is_file() and not p.is_symlink() and _NAME.match(p.name):
                    st = p.stat()
                    files.append({"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime)})
        except OSError:
            pass
        files.sort(key=lambda f: -f["mtime"])
        out.append({"key": key, "label": labels.get(key, key), "files": files})
    return out


def read(roots: dict, key: str, name: str) -> dict | None:
    p = _path(roots, key, name)
    if p is None or not p.is_file():
        return None
    if p.stat().st_size > MAX_BYTES:
        return {"name": name, "text": "", "sha": "", "too_big": True}
    text = p.read_text(encoding="utf-8", errors="replace")
    return {"name": name, "text": text, "sha": sha(text), "mtime": int(p.stat().st_mtime), "too_big": False}


def write(roots: dict, key: str, name: str, text: str, base_sha: str) -> tuple[bool, str, str]:
    """(성공, 오류, 새 sha). 새 파일은 base_sha=''로 만든다"""
    p = _path(roots, key, name)
    if p is None:
        return False, "다룰 수 없는 파일이에요(.md만, 폴더 바로 아래만).", ""
    data = (text or "").replace("\r\n", "\n")
    if len(data.encode("utf-8")) > MAX_BYTES:
        return False, "파일이 너무 커요(400KB까지).", ""
    if p.exists():
        cur = p.read_text(encoding="utf-8", errors="replace")
        if sha(cur) != base_sha:
            return False, "그 사이 다른 곳(세션)에서 이 파일이 바뀌었어요. 다시 불러온 뒤 고쳐 주세요.", ""
        if cur == data:
            return True, "", sha(cur)
        hist = p.parent / ".history"
        hist.mkdir(exist_ok=True)
        own(hist)
        hp = hist / f"{p.name}.{time.strftime('%Y%m%d-%H%M%S')}"
        hp.write_text(cur, encoding="utf-8")
        own(hp)
        olds = sorted(hist.glob(p.name + ".*"))
        for o in olds[:-KEEP_HISTORY]:
            try:
                o.unlink()
            except OSError:
                pass
        with open(p, "r+", encoding="utf-8") as f:          # 제자리 덮어쓰기
            f.seek(0)
            f.truncate()
            f.write(data)
    else:
        if base_sha:
            return False, "파일이 없어졌어요. 목록을 다시 불러와 주세요.", ""
        p.write_text(data, encoding="utf-8")
        own(p)
    try:
        os.chmod(p, 0o664)
    except OSError:
        pass
    return True, "", sha(data)
