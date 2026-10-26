"""텔레그램에서 고른 영화가 다 받아지면 "제목 (연도)."를 붙여 영화 라이브러리로 옮긴다 (2026-10-26)

run_all.sh가 media_router.py보다 먼저 부른다 (호스트에서 실행, /volume1/video 쓰기 가능).
작업 목록은 data/movie_jobs.json (web_admin의 start_movie_download가 추가).
"""
from __future__ import annotations

import shutil
import sys
import time
from datetime import datetime

import requests

import movies
from db import load_cfg, DATA_DIR

JOBS_PATH = DATA_DIR / "movie_jobs.json"
STALL_SEC = 7 * 24 * 3600  # 일주일 지나도 안 받아지면 한 번 알림


def log(msg: str) -> None:
    print(f"[movie_mover {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def tg_send(text: str) -> None:
    tg = load_cfg().get("telegram") or {}
    if not tg.get("enabled") or not tg.get("bot_token") or not tg.get("chat_id"):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
                      data={"chat_id": tg["chat_id"], "text": text}, timeout=10)
    except Exception as e:
        log(f"텔레그램 전송 실패: {type(e).__name__}")


def process(jobs: list[dict], host_dir=movies.HOST_MOVIE_DIR, library=movies.MOVIE_LIBRARY,
            notify=tg_send, now=None) -> dict[str, dict]:
    """받기가 끝난 작업의 파일을 옮기고 {btih: 바뀐 필드}를 돌려준다 (jobs는 건드리지 않음)"""
    now = now or time.time()
    changes: dict[str, dict] = {}
    for job in jobs:
        if job.get("status") != "downloading":
            continue
        job_dir = host_dir / job["btih"]
        if not movies.is_complete(job_dir):
            if now - job.get("added", now) > STALL_SEC:
                changes[job["btih"]] = {"status": "stalled"}
                notify(f"⚠ '{job['prefix']}'이(가) 일주일째 다 안 받아졌어요. qBittorrent에서 확인해 주세요.")
            continue
        dest_dir = library / job["folder"]
        try:
            placed = movies.place_files(job_dir, job["prefix"], dest_dir)
        except Exception as e:
            log(f"{job['prefix']} 옮기기 실패: {type(e).__name__}: {e}")
            continue
        shutil.rmtree(job_dir, ignore_errors=True)  # 남은 nfo·샘플 등
        changes[job["btih"]] = {"status": "done", "done": int(now), "files": [p.name for p in placed]}
        log(f"{job['prefix']} → {dest_dir} ({len(placed)}개)")
        names = "\n".join(f"• {p.name}" for p in placed[:5])
        notify(f"✅ '{job['prefix']}' 받아서 영화/{job['folder']} 폴더에 넣었어요.\n{names}")
    return changes


def main() -> int:
    jobs = movies.load_jobs(JOBS_PATH)
    if not any(j.get("status") == "downloading" for j in jobs):
        return 0
    # 볼륨을 넘는 복사는 몇 분 걸리므로 잠금 밖에서 옮기고, 결과만 잠금 안에서 저장한다
    changes = process(jobs)
    if changes:
        def apply(current):
            for j in current:
                if j.get("btih") in changes and j.get("status") == "downloading":
                    j.update(changes[j["btih"]])
        movies.update_jobs(JOBS_PATH, apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
