# -*- coding: utf-8 -*-
"""다운로드 탭: 다 받았는데 아직 라이브러리로 안 옮겨진 파일 (2026-11-01 사용자 요청).

media_router.py가 정리하는 받은 폴더(설정의 paths.sources)를 훑어서 파일마다
이름·크기·도착 시각과 '왜 아직 여기 있는지' 짐작을 붙인다. 읽기만 한다.
규칙 매칭은 media_router.find_matches와 같은 조건(그쪽은 import하면 로그 파일을 열어서 따로 둠).
"""
from __future__ import annotations

import time
from pathlib import Path

from movies import MOVIE_DIR_NAME

# 컨테이너 안에서 보이는 경로 (docker-compose.yml 마운트)
CONTAINER_PATHS = {"/volume2/torrent/download": "/app/torrent_dl"}
INCOMPLETE_EXTENSIONS = {".!qb", ".part", ".crdownload", ".tmp"}
SKIP_DIRS = {"incomplete", "@eaDir", "#recycle", ".recycle"}
STALE_SECONDS = 3600        # 규칙에 맞는데 이만큼 넘게 남아 있으면 '안 옮겨짐'으로 본다
LIMIT = 300


def _matches_any(filename: str, pattern_str: str | None) -> bool:
    if not pattern_str:
        return False
    return any(p.strip() in filename for p in pattern_str.split("|") if p.strip())


def _and_group(filename: str, a: str | None, b: str | None) -> bool:
    if not a and not b:
        return False
    if a and not _matches_any(filename, a):
        return False
    if b and not _matches_any(filename, b):
        return False
    return True


def match_rule(filename: str, rules: list[dict]) -> dict | None:
    """media_router.find_matches와 같은 조건으로 맞는 첫 규칙"""
    for r in rules:
        if not ((r.get("category") or "").strip() and (r.get("pattern") or "").strip()
                and (r.get("subfolder") or "").strip()):
            continue
        g = lambda k: (r.get(k) or "").strip() or None
        if not (_and_group(filename, g("pattern"), g("pattern2")) or _and_group(filename, g("pattern_or"), g("pattern2_or"))):
            continue
        if g("exclude_pattern") and _matches_any(filename, g("exclude_pattern")):
            continue
        return r
    return None


def scan(sources: list[str], rules: list[dict], base_paths: dict, paused: bool = False,
         now: float | None = None, path_map: dict | None = None) -> list[dict]:
    now = now or time.time()
    path_map = CONTAINER_PATHS if path_map is None else path_map
    out = []
    for src in sources:
        root = Path(path_map.get(src.rstrip("/"), src))
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
                continue
            if not p.is_file() or p.suffix.lower() in INCOMPLETE_EXTENSIONS:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            age = now - st.st_mtime
            if MOVIE_DIR_NAME in rel.parts:
                reason, kind = "영화 받기로 고른 영화 - 영화 정리(movie_mover)가 옮길 차례", "movie"
            else:
                r = match_rule(p.name, rules)
                if not r:
                    reason, kind = "맞는 분류 규칙 없음 - 손으로 옮기거나 분류 규칙을 추가하세요", "norule"
                elif not base_paths.get((r.get("category") or "").strip()):
                    reason, kind = f"규칙 '{r['subfolder']}'의 분류({r['category']}) 경로가 설정에 없음", "norule"
                elif paused:
                    reason, kind = f"자동 정리 일시정지 중 (규칙 '{r['subfolder']}')", "paused"
                elif age > STALE_SECONDS:
                    reason, kind = f"규칙 '{r['subfolder']}'에 맞는데 1시간 넘게 안 옮겨짐 - 정리 로그 확인", "stuck"
                else:
                    reason, kind = f"규칙 '{r['subfolder']}'에 맞음 - 다음 정리 때 옮겨짐", "soon"
            out.append({"name": p.name, "folder": src.rstrip("/"), "rel": str(rel), "size": st.st_size,
                        "mtime": int(st.st_mtime), "reason": reason, "kind": kind})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:LIMIT]


# ── 시놀로지 드라이브 (2026-11-07): PC(D:\SynologyDrive)에서 받은 영상이 Drive에 올라오면 호스트 예약작업
#    /volume1/web/video_auto/drama_name.sh가 'Drive 전체(인코딩·보관 제외)'의 아래 확장자를 /volume1/video/임시/download로 옮긴다.
#    그 전까지 남아 있는 파일을 보여 준다. 컨테이너는 Drive를 읽기만(/app/drive_root:ro) — 만들거나 지우면 PC와 어긋남.
DRIVE_LABEL = "드라이브"
DRIVE_MOVED_EXT = {".mp4", ".mkv", ".srt", ".smi", ".ass", ".mp44"}        # drama_name.sh exts
DRIVE_OTHER_VIDEO = {".avi", ".ts", ".m4v", ".webm", ".mov", ".wmv", ".flv", ".m2ts"}
DRIVE_SKIP_TOP = {"인코딩", "보관"}                                          # drama_name.sh가 건너뛰는 폴더
DRIVE_STABLE_SECONDS = 60                                                     # drama_name.sh STABLE_MTIME_SEC


def scan_drive(root: Path, now: float | None = None) -> list[dict]:
    now = now or time.time()
    out = []
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] in DRIVE_SKIP_TOP:
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        ext = p.suffix.lower()
        if ext not in DRIVE_MOVED_EXT and ext not in DRIVE_OTHER_VIDEO:
            continue
        try:
            if not p.is_file():
                continue
            st = p.stat()
        except OSError:
            continue
        age = now - st.st_mtime
        if ext in DRIVE_OTHER_VIDEO:
            reason, kind = f"드라이브 정리(drama_name.sh)가 안 옮기는 확장자({ext}) - 손으로 옮기세요", "norule"
        elif age < DRIVE_STABLE_SECONDS:
            reason, kind = "드라이브에 막 올라옴 - 1분 뒤 다음 드라이브 정리 때 옮겨짐", "soon"
        elif age > STALE_SECONDS:
            reason, kind = "드라이브에 1시간 넘게 남음 - drama_name.sh 예약작업·로그 확인", "stuck"
        else:
            reason, kind = "드라이브 → 임시/download, 다음 드라이브 정리 때 옮겨짐", "soon"
        out.append({"name": p.name, "folder": DRIVE_LABEL, "rel": str(rel), "size": st.st_size,
                    "mtime": int(st.st_mtime), "reason": reason, "kind": kind, "source": "drive"})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:LIMIT]
