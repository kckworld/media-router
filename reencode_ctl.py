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
    """reencode.py running()과 같게 세 가지를 다 본다. PC 워커는 Windows SMB 캐시 때문에 control.json을
    몇 분씩 옛 값으로 읽어서(10-27) 작업 폴더의 STOP 파일만 본다 — 그래서 시작·종료는 꼭 reencode.py로."""
    return (bool((_read(WORK / "control.json", {}) or {}).get("run"))
            and not STOP.exists() and not (WORK / "STOP").exists())


def worker() -> dict:
    """PC 워커 하트비트 {state: encoding|waiting|shutting_down, current, updated}"""
    return _read(WORK / "status.json", {}) or {}


_OPEN = ("pending", "fed", "collecting")


def batch_has_work() -> bool:
    """일괄 재인코딩(items)에만 남은 작업이 있는지 - 새 예능만 남았으면 일괄 쪽은 켜지 않는다"""
    q = _read(QUEUE, {}) or {}
    return any(it.get("status") in _OPEN for it in q.get("items") or [])


def has_work() -> bool:
    """일괄 재인코딩이나 새로 받은 예능(new_items, 10-27)에 남은 작업이 있는지"""
    q = _read(QUEUE, {}) or {}
    return any(it.get("status") in _OPEN for it in (q.get("items") or []) + (q.get("new_items") or []))


def needs_collect() -> bool:
    """PC가 결과를 남겼으면(done/*.json) 호스트에서 collect를 돌려야 한다"""
    try:
        return any(p.suffix == ".json" for p in (WORK / "done").iterdir())
    except OSError:
        return False


_VIDEO_EXTS = (".mp4", ".mkv", ".ts", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".webm")   # reencode.py VIDEO_EXTS와 같게


def held_new(new_items: list) -> list:
    """media_router가 new/(새 예능)·new/drama/(새 드라마)에 보류했지만 reencode.py feed_new가 아직 작업(N…)으로
    만들지 않은 파일(10-31). feed_new는 run(배치 20분 주기, 결과가 있으면 1분마다)에서 돌아서 그 사이엔 큐에 없다."""
    known = {it.get("src") for it in new_items if it.get("status") in _OPEN}
    out = []
    for d, profile in ((WORK / "new", "variety"), (WORK / "new" / "drama", "drama")):
        try:
            files = sorted(d.iterdir())
        except OSError:
            continue
        for p in files:
            if (p.is_file() and not p.name.startswith((".", "[손상]")) and p.suffix.lower() in _VIDEO_EXTS
                    and str(p) not in known):
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                out.append({"id": "", "kind": "new", "profile": profile, "src": str(p), "size": size, "status": "held"})
    return out


def _new_label(new_items: list) -> str:
    """새로 받은 영상(new_items) 줄 머리 - 드라마도 들어온다(10-28, profile=drama)"""
    drama = sum(1 for it in new_items if it.get("profile") == "drama")
    if not drama:
        return "새 예능"
    return "새 드라마" if drama == len(new_items) else f"새 영상(예능 {len(new_items) - drama}·드라마 {drama})"


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
        t = tiers.setdefault(it.get("tier") or 0, {"done": 0, "todo": 0, "failed": 0, "hold": 0, "saved": 0, "expect": 0})
        # hold = 드라마(tier D1~D3, 10-28) 샘플 확인 전 보류 - feed가 건너뛴다
        t["done" if st == "done" else "failed" if st == "failed" else "hold" if st == "hold" else "todo"] += 1
        t["saved"] += it.get("saved", 0) if st == "done" else 0
        t["expect"] += it.get("expect_save", 0)
    saved = sum(it.get("saved", 0) for it in items if it.get("status") == "done")
    expect = sum(it.get("expect_save", 0) for it in items)
    new_items = q.get("new_items") or []      # media_router가 보류한 새 예능 (id N0001…)
    new_open = [it for it in new_items if it.get("status") in _OPEN]
    new_failed = [it for it in new_items if it.get("status") == "failed"]
    held = held_new(new_items)
    samples = q.get("samples") or []          # 드라마 샘플 (id T0001…, 원본은 그대로 두고 sample/에 결과)
    samples_open = [it for it in samples if it.get("status") in _OPEN]
    by_id = {it.get("id"): it for it in items + new_items + samples}
    w = worker()
    cur = by_id.get(w.get("current")) if w.get("current") else None
    is_running = running()
    trash = q.get("trash_bytes", 0)
    summary = (f"완료 {counts.get('done', 0)}/{len(items)} · 진행 {counts.get('fed', 0)} · 대기 {counts.get('pending', 0)}"
               f" · 실패 {counts.get('failed', 0)}" + (f" · 보류(드라마) {counts['hold']}" if counts.get("hold") else "")
               + f"\n줄인 용량 {gb(saved)} / 예상 {gb(expect)}"
               + ("\n▶ 실행 중" if is_running else "\n⏹ 멈춤")
               + (f"\n🆕 {_new_label(new_open + held + new_failed)} " + " · ".join(([f"인코딩 대기 {len(new_open)}개"] if new_open else [])
                                             + ([f"대기 등록 전 {len(held)}개"] if held else [])
                                             + ([f"실패 {len(new_failed)}개(new 폴더에 [손상] 표시)"] if new_failed else []))
                  if new_open or held or new_failed else "")
               + (f"\nPC: {w.get('state', '?')}" + (f" — #{w['current']}" if w.get("current") else "")
                  + (f" · 올리기 대기 {w['pending_upload']}개" if w.get("pending_upload") else "")
                  + f" ({str(w.get('updated', ''))[5:16]})" if w else "")
               + (f"\n🗑 Drive 휴지통에 예전 방식 재인코딩 파일 약 {gb(trash)} — 비워 주세요" if trash >= TRASH_WARN else ""))

    def row(it):
        return {"id": it.get("id"), "name": Path(it.get("src", "")).name,
                "tier": ("새 드라마" if it.get("profile") == "drama" else "새 예능") if it.get("kind") == "new" else "샘플" if it.get("kind") == "sample" else it.get("tier"),
                "size": it.get("size", 0), "new_size": it.get("new_size"), "saved": it.get("saved"),
                # 새 영상(N…)엔 mbps가 없어서 크기·길이로 계산
                "mbps": it.get("mbps") or (round(it["size"] * 8 / it["dur"] / 1e6, 2) if it.get("size") and it.get("dur") else None),
                "res": it.get("res"), "dur": it.get("dur"), "status": it.get("status"),
                "expect_save": it.get("expect_save", 0), "done_at": it.get("done_at"), "msg": it.get("msg") or "",
                "out_mbps": it.get("out_mbps"), "final": it.get("final")}
    # 새 예능·새 드라마(N…) 완료분도 같이 (10-31: 일괄 재인코딩만 나와서 안 보였다)
    done = sorted((it for it in items + new_items if it.get("status") == "done"),
                  key=lambda it: it.get("done_at") or "", reverse=True)
    # 새 예능은 일괄 재인코딩보다 먼저 집히므로 대기 목록 맨 앞
    # 워커는 새 예능 → 드라마 샘플 → 일괄 순서로 집는다
    waiting = new_open + held + samples_open + [it for it in items if it.get("status") == "fed"] + [it for it in items if it.get("status") == "pending"]
    return {"available": True, "counts": counts, "total": len(items), "saved": saved, "expect": expect,
            # 예능 1~3(숫자) 다음 드라마 D1~D3(글자) - 섞어서 sorted하면 TypeError
            "tiers": {str(k): v for k, v in sorted(tiers.items(), key=lambda kv: (isinstance(kv[0], str), str(kv[0])))},
            "running": is_running, "worker": w,
            "samples": [row(it) for it in reversed(samples)],
            "current": row(cur) if cur else None, "summary": summary,
            "failed": [row(it) for it in new_failed + [it for it in items if it.get("status") == "failed"]],
            "new_waiting": len(new_open),
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
