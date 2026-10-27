"""예능 일괄 재인코딩(/volume1/docker/reencode, 미디어 정리 세션이 만든 reencode.py) 상태·조작 (2026-10-27)

컨테이너(web_admin)에서 쓴다. queue.json은 읽기만 하고, 일시정지·재개는 STOP 파일만 만들고 지운다.
Drive 파일을 지우는 '비우기'는 컨테이너에서 하면 PC 동기화가 깨지므로 호스트에서 돈다
(web_admin이 SSH로 run_all.sh에 'reencode-clear'를 보내면 run_all.sh가 reencode.py clear를 실행).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE = Path(os.getenv("REENCODE_DIR", "/volume1/docker/reencode"))
QUEUE = BASE / "queue.json"
STOP = BASE / "STOP"
LOG = BASE / "reencode.log"
PREFIX = "[재인코딩] "
TRASH_WARN = 100 * 2 ** 30   # reencode.py와 같게: 이만큼 쌓이면 Drive 휴지통을 비우라고 안내


def gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}GB"


def available() -> bool:
    return QUEUE.exists()


def paused() -> bool:
    return STOP.exists()


def set_paused(on: bool) -> None:
    if on:
        STOP.touch()
        try:
            os.chmod(STOP, 0o666)   # 호스트의 reencode.py resume(kck9010)도 지울 수 있게
        except OSError:
            pass
    elif STOP.exists():
        STOP.unlink()


def state() -> dict:
    """{counts, total, saved, expect, paused, failed:[{id,name,error}], summary}"""
    try:
        q = json.loads(QUEUE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False}
    items = q.get("items") or []
    counts: dict[str, int] = {}
    for it in items:
        counts[it.get("status", "?")] = counts.get(it.get("status", "?"), 0) + 1
    saved = sum(it.get("saved", 0) for it in items if it.get("status") == "done")
    expect = sum(it.get("expect_save", 0) for it in items)
    failed = [{"id": it.get("id"), "name": Path(it.get("src", "")).name,
               "error": it.get("msg") or ""} for it in items if it.get("status") == "failed"]
    is_paused = paused()
    # Drive 폴더에서 지운 재인코딩 파일은 Drive 휴지통에 남아 공간을 붙잡는다 (10-27 +79GB 회수)
    trash = q.get("trash_bytes", 0)
    since = q.get("trash_emptied_at", "")
    # reencode.py summary()와 같은 모양
    summary = (f"완료 {counts.get('done', 0)}/{len(items)} · 넣어 둠 {counts.get('fed', 0)} · 대기 {counts.get('pending', 0)}"
               f" · 실패 {counts.get('failed', 0)}\n줄인 용량 {gb(saved)} / 예상 {gb(expect)}"
               + ("\n⏸ 일시정지 중(STOP)" if is_paused else "")
               + f"\nDrive 휴지통에 쌓인 재인코딩 파일 약 {gb(trash)}" + (f" ({since} 비운 뒤)" if since else "")
               + ("\n🗑 Drive 휴지통을 비워 주세요 (Synology Drive 웹 → 휴지통 → 비우기). 비우기 전엔 이만큼 공간이 안 돌아와요."
                  if trash >= TRASH_WARN else ""))
    return {"available": True, "counts": counts, "total": len(items), "saved": saved, "expect": expect,
            "paused": is_paused, "failed": failed, "summary": summary,
            "trash_bytes": trash, "trash_emptied_at": since, "trash_warn": trash >= TRASH_WARN}


def log_tail(lines: int = 300) -> str:
    try:
        with LOG.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def waiting_files(encode_dir: Path) -> list[Path]:
    """Drive/인코딩에 넣어 둔 재인코딩 원본 (.part 같은 받는 중 파일 제외)"""
    try:
        return [p for p in encode_dir.iterdir() if p.is_file() and p.name.startswith(PREFIX)
                and not p.name.lower().endswith((".part", ".tmp", ".crdownload"))]
    except OSError:
        return []
