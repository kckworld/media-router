# -*- coding: utf-8 -*-
"""라이브러리 mkv 내장 ASS → SRT 일괄 변환 (2026-11-02). 한 번 부를 때 BUDGET초 안에서 한 파일씩.

Media Router가 밤(NIGHT_FROM~NIGHT_TO시)에 1분마다 SSH 'ass2srt-step'으로 부른다(run_all.sh 앞 분기).
켜고 끄기: WORK/enabled 파일이 있을 때만 한다(기본 꺼짐 — 팀장·사용자가 일정 정한 뒤 켬).
  python3 ass2srt_bulk.py on|off|status|step [--force]
대상은 scan.tsv(내장 ASS 트랙 수 > 0). 재인코딩 대기 중인 파일은 건너뛴다(재인코딩 결과 처리 때 같이 바뀜).
원본은 /volume1/video/임시/ass원본/에 원래 폴더 구조로 보관(같은 공유폴더라 즉시 이동). 지우는 건 따로 확인 뒤.
"""
from __future__ import annotations

import fcntl
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ass2srt  # noqa: E402

WORK = Path("/volume1/docker/ass2srt")
SCAN = WORK / "scan.tsv"
STATE = WORK / "bulk_state.json"
ENABLED = WORK / "enabled"
LOCK = WORK / ".bulk.lock"
QUEUE = Path("/volume1/docker/reencode/queue.json")
BUDGET = 50            # 한 번에 이 시간(초)까지만 새 파일을 시작
NIGHT_FROM, NIGHT_TO = 1, 7


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(STATE)


def targets() -> list[tuple[str, int]]:
    out = []
    try:
        lines = SCAN.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) > 0:
            out.append((parts[0], int(parts[1])))
    return out


def reencode_pending() -> set[str]:
    try:
        q = json.loads(QUEUE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {it["src"] for it in q.get("items", []) if it.get("status") in ("pending", "fed", "collecting")}


def status() -> dict:
    st, tg = load_state(), targets()
    c = {"ok": 0, "fail": 0, "skip": 0}
    for v in st.values():
        c[v.get("status", "fail")] = c.get(v.get("status", "fail"), 0) + 1
    return {"enabled": ENABLED.exists(), "total": len(tg), "total_gb": round(sum(s for _, s in tg) / 1e9, 1),
            "done": c["ok"], "failed": c["fail"], "skipped": c["skip"], "left": len(tg) - c["ok"] - c["fail"] - c["skip"],
            "last": max((v.get("at", "") for v in st.values()), default="")}


def step(force: bool = False, now: datetime | None = None) -> int:
    now = now or datetime.now()
    if not force and (not ENABLED.exists() or not (NIGHT_FROM <= now.hour < NIGHT_TO)):
        return 0
    lockf = open(LOCK, "a")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    st = load_state()
    skip_re = reencode_pending()
    start, n = time.time(), 0
    for path, _size in targets():
        if time.time() - start > BUDGET:
            break
        if path in st:
            continue
        p = Path(path)
        if not p.exists():
            st[path] = {"status": "skip", "msg": "파일 없음(옮겨지거나 지워짐)", "at": now.strftime("%m-%d %H:%M")}
        elif path in skip_re:
            continue                      # 재인코딩이 끝나면 결과 처리 때 바뀜. 여기선 다음에 다시 봄
        else:
            ok, msg = ass2srt.convert_mkv(p)
            ass2srt.log(f"{'OK' if ok else '실패'} bulk {p}: {msg}")
            st[path] = {"status": "ok" if ok else ("skip" if msg == "내장 ASS 없음" else "fail"), "msg": msg[:300],
                        "at": datetime.now().strftime("%m-%d %H:%M")}
        save_state(st)
        n += 1
    return n


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "on":
        ENABLED.touch()
    elif cmd == "off":
        ENABLED.unlink(missing_ok=True) if ENABLED.exists() else None
    elif cmd == "step":
        step(force="--force" in argv)
        return 0
    print(json.dumps(status(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
