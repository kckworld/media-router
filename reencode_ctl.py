"""예능 일괄 재인코딩 v2 상태 읽기 (2026-10-27, /volume1/docker/reencode/reencode.py — 미디어 정리 세션)

v2는 Drive를 안 쓴다. PC 워커(작업 스케줄러 "ReencodeWorker")가 \\\\NAS\\video\\임시\\재인코딩의 todo에서
하나씩 집어 인코딩하고 out·done에 쓴다. NAS의 reencode.py run(collect+feed)이 결과를 원본 자리로 바꿔 넣는다.
컨테이너는 /volume1/video가 읽기 전용이라 여기서는 읽기만 한다. 시작·종료(control.json)와 collect는
호스트에서(web_admin이 SSH로 run_all.sh에 'reencode-<명령>'을 보냄).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE = Path(os.getenv("REENCODE_DIR", "/volume1/docker/reencode"))
QUEUE = BASE / "queue.json"
STOP = BASE / "STOP"
LOG = BASE / "reencode.log"
WORK = Path(os.getenv("REENCODE_WORK", "/volume1/video/임시/재인코딩"))
TRASH_WARN = 1 * 2 ** 30   # v1(Drive 방식)이 남긴 휴지통 양 - reencode.py trash_line과 같게


def gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}GB"


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def available() -> bool:
    return QUEUE.exists()


def running() -> bool:
    return bool((_read(WORK / "control.json", {}) or {}).get("run")) and not STOP.exists()


def worker() -> dict:
    """PC 워커 하트비트 {state: encoding|waiting|shutting_down, current, updated}"""
    return _read(WORK / "status.json", {}) or {}


def has_work() -> bool:
    q = _read(QUEUE, {}) or {}
    return any(it.get("status") in ("pending", "fed") for it in q.get("items") or [])


def needs_collect() -> bool:
    """PC가 결과를 남겼으면(done/*.json) 호스트에서 collect를 돌려야 한다"""
    try:
        return any(p.suffix == ".json" for p in (WORK / "done").iterdir())
    except OSError:
        return False


def state() -> dict:
    q = _read(QUEUE)
    if not q:
        return {"available": False}
    items = q.get("items") or []
    counts: dict[str, int] = {}
    tiers: dict[int, dict] = {}
    for it in items:
        st = it.get("status", "?")
        counts[st] = counts.get(st, 0) + 1
        t = tiers.setdefault(it.get("tier") or 0, {"done": 0, "todo": 0, "failed": 0, "saved": 0, "expect": 0})
        t["done" if st == "done" else "failed" if st == "failed" else "todo"] += 1
        t["saved"] += it.get("saved", 0) if st == "done" else 0
        t["expect"] += it.get("expect_save", 0)
    saved = sum(it.get("saved", 0) for it in items if it.get("status") == "done")
    expect = sum(it.get("expect_save", 0) for it in items)
    by_id = {it.get("id"): it for it in items}
    w = worker()
    cur = by_id.get(w.get("current")) if w.get("current") else None
    is_running = running()
    trash = q.get("trash_bytes", 0)
    summary = (f"완료 {counts.get('done', 0)}/{len(items)} · 진행 {counts.get('fed', 0)} · 대기 {counts.get('pending', 0)}"
               f" · 실패 {counts.get('failed', 0)}\n줄인 용량 {gb(saved)} / 예상 {gb(expect)}"
               + ("\n▶ 실행 중" if is_running else "\n⏹ 멈춤")
               + (f"\nPC: {w.get('state', '?')}" + (f" — #{w['current']}" if w.get("current") else "")
                  + f" ({str(w.get('updated', ''))[5:16]})" if w else "")
               + (f"\n🗑 Drive 휴지통에 예전 방식 재인코딩 파일 약 {gb(trash)} — 비워 주세요" if trash >= TRASH_WARN else ""))

    def row(it):
        return {"id": it.get("id"), "name": Path(it.get("src", "")).name, "tier": it.get("tier"),
                "size": it.get("size", 0), "new_size": it.get("new_size"), "saved": it.get("saved"),
                "mbps": it.get("mbps"), "res": it.get("res"), "dur": it.get("dur"), "status": it.get("status"),
                "expect_save": it.get("expect_save", 0), "done_at": it.get("done_at"), "msg": it.get("msg") or ""}
    done = sorted((it for it in items if it.get("status") == "done"), key=lambda it: it.get("done_at") or "", reverse=True)
    waiting = [it for it in items if it.get("status") == "fed"] + [it for it in items if it.get("status") == "pending"]
    return {"available": True, "counts": counts, "total": len(items), "saved": saved, "expect": expect,
            "tiers": {str(k): v for k, v in sorted(tiers.items())}, "running": is_running, "worker": w,
            "current": row(cur) if cur else None, "summary": summary,
            "failed": [row(it) for it in items if it.get("status") == "failed"],
            "done_list": [row(it) for it in done[:30]], "waiting_list": [row(it) for it in waiting[:20]],
            "waiting_total": len(waiting), "trash_bytes": trash, "trash_warn": trash >= TRASH_WARN}


def log_tail(path: Path, lines: int = 200) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
