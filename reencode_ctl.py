"""예능 일괄 재인코딩 v2 상태 읽기 (2026-10-27, /volume1/docker/reencode/reencode.py — 미디어 정리 세션)

v2는 Drive를 안 쓴다. PC 워커(작업 스케줄러 "ReencodeWorker")가 \\\\NAS\\video\\임시\\재인코딩의 todo에서
하나씩 집어 인코딩하고 out·done에 쓴다. NAS의 reencode.py run(collect+feed)이 결과를 원본 자리로 바꿔 넣는다.
컨테이너는 /volume1/video가 읽기 전용이라 여기서는 읽기만 한다. 시작·종료(control.json)와 collect는
호스트에서(web_admin이 SSH로 run_all.sh에 'reencode-<명령>'을 보냄).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
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


SESSION_GAP = 30 * 60      # 완료 사이가 이보다 길면 PC가 꺼져 있던 것(새 가동)으로 본다
ETA_WINDOW_DAYS = 7


_KST = timezone(timedelta(hours=9))   # 기록(queue.json·worker.log)은 모두 한국 시각 - 컨테이너 TZ와 상관없이


def _ts(v) -> float | None:
    try:
        return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_KST).timestamp()
    except (TypeError, ValueError):
        return None


def _kst(t: float, fmt: str) -> str:
    return datetime.fromtimestamp(t, _KST).strftime(fmt)


def eta(q: dict, held: list, w: dict, night_at: str = "03:00", now: float | None = None) -> dict:
    """남은 시간·종료 예상 (10-31 사용자). 벽시계가 아니라 워커가 실제로 돈 시간 기준:
    최근 7일 완료 시각을 이어 보고, 30분 넘게 비면 PC가 꺼져 있던 것으로 보고 그 틈은 뺀다.
    한 건에 걸린 시간(앞 완료와의 간격 = 복사·인코딩·올리기)을 원본 길이로 나눈 '영상 1초당 처리 초'로 남은 일을 잰다.
    드라마는 아직 일괄 기록이 거의 없어서, 샘플·새 드라마의 인코딩 시간 비율로 예능 값을 늘려 쓴다."""
    now = now or time.time()
    items, new_items, samples = q.get("items") or [], q.get("new_items") or [], q.get("samples") or []
    done = sorted(((t, it) for it in items + new_items if it.get("status") == "done"
                   for t in [_ts(it.get("done_at"))] if t and now - t < ETA_WINDOW_DAYS * 86400), key=lambda x: x[0])
    active, wall_per_src, per_day, prev = 0.0, [], {}, None
    for t, it in done:
        if prev is not None and t - prev <= SESSION_GAP and it.get("dur"):
            gap = t - prev
            active += gap
            wall_per_src.append(gap / it["dur"])
            day = _kst(t, "%Y-%m-%d")
            per_day[day] = per_day.get(day, 0) + gap
        prev = t
    n = len(wall_per_src)
    counts = {}
    for t, _ in done:
        day = _kst(t, "%Y-%m-%d")
        counts[day] = counts.get(day, 0) + 1
    daily = [{"day": _kst(now - i * 86400, "%Y-%m-%d"),
              "count": counts.get(_kst(now - i * 86400, "%Y-%m-%d"), 0),
              "active_h": round(per_day.get(_kst(now - i * 86400, "%Y-%m-%d"), 0) / 3600, 1)}
             for i in range(ETA_WINDOW_DAYS - 1, -1, -1)]
    out = {"window_days": ETA_WINDOW_DAYS, "counted": n, "active_h": round(active / 3600, 1), "daily": daily}
    if n < 3:
        return out
    ratio = sorted(wall_per_src)[n // 2]                         # 중앙값(한 건 튀는 것 무시)

    def enc_ratio(rows):
        r = sorted(it["encode_sec"] / it["dur"] for it in rows if it.get("encode_sec") and it.get("dur"))
        return r[len(r) // 2] if r else None
    v_enc = enc_ratio([it for _, it in done if it.get("profile") != "drama"])
    d_enc = enc_ratio([it for it in samples + items + new_items if it.get("profile") == "drama" and it.get("status") == "done"])
    d_ratio = ratio * d_enc / v_enc if v_enc and d_enc else ratio
    out.update(per_hour=round(3600 / (active / n), 1), sec_per_job=round(active / n))

    groups = {}

    def add(key, label, dur, r):
        g = groups.setdefault(key, {"label": label, "count": 0, "sec": 0.0})
        g["count"] += 1
        g["sec"] += (dur or 0) * r

    avg_dur = sum(it.get("dur") or 0 for _, it in done) / len(done)
    for it in new_items:
        if it.get("status") in _OPEN:
            d = it.get("profile") == "drama"
            add("new_drama" if d else "new_variety", "새 드라마" if d else "새 예능", it.get("dur"), d_ratio if d else ratio)
    for it in held:                                              # 아직 길이를 모름 → 평균 길이로
        d = it.get("profile") == "drama"
        add("new_drama" if d else "new_variety", "새 드라마" if d else "새 예능", avg_dur, d_ratio if d else ratio)
    for it in items:
        if it.get("status") in _OPEN:
            tier = it.get("tier")
            d = it.get("profile") == "drama"
            label = f"드라마 {str(tier)[1:]}순위" if d else f"예능 {tier}순위"
            add(f"t{tier}", label, it.get("dur"), d_ratio if d else ratio)
    remain = sum(g["sec"] for g in groups.values())
    out["groups"] = [dict(g, hours=round(g["sec"] / 3600, 1)) for g in groups.values()]
    out["remain_count"] = sum(g["count"] for g in groups.values())
    out["remain_h"] = round(remain / 3600, 1)
    out["end_continuous"] = _kst(now + remain, "%Y-%m-%d %H:%M")
    days = sorted(per_day)
    daily = active / max(1, len(days))                           # 가동한 날 하루 평균
    out["daily_active_h"] = round(daily / 3600, 1)
    if daily > 0:
        out["end_schedule"] = _kst(now + remain / daily * 86400, "%Y-%m-%d")
        out["schedule_days"] = round(remain / daily, 1)
    out["night_at"] = night_at

    # 지금 파일: worker.log의 시작 시각과 예상 처리 시간으로 대충의 진행률
    cur_id = w.get("current")
    cur = next((it for it in items + new_items + samples if it.get("id") == cur_id), None) if cur_id else None
    if cur and cur.get("dur"):
        started = None
        for line in reversed(log_tail(WORK / "worker.log", 300).splitlines()):
            if f"#{cur_id} " in line and "시작 (" in line:
                started = _ts(line[1:20])
                break
        if started:
            expect = cur["dur"] * (d_ratio if cur.get("profile") == "drama" else ratio)
            el = now - started
            if el < 0 or el > expect * 3:          # 시각이 안 맞거나 너무 오래 걸리면 진행률은 안 보여 준다
                return out
            out["current"] = {"id": cur_id, "elapsed_min": round(el / 60), "expect_min": round(expect / 60),
                              "pct": min(99, round(el / expect * 100)), "left_min": max(0, round((expect - el) / 60))}
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
            "waiting_total": len(waiting), "eta": eta(q, held, w), "trash_bytes": trash, "trash_warn": trash >= TRASH_WARN}


def log_tail(path: Path, lines: int = 200) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
