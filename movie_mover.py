"""텔레그램에서 고른 영화가 다 받아지면 "제목 (연도)."를 붙여 영화 라이브러리로 옮긴다 (2026-10-26)

run_all.sh가 media_router.py보다 먼저 부른다 (호스트에서 실행, /volume1/video 쓰기 가능).
작업 목록은 data/movie_jobs.json (web_admin의 start_movie_download가 추가).
티프리카 글에 자막이 따로 붙어 있으면(job['subs']) 사용자가 받아서 Drive에 넣은 자막을
찾아 영화 파일과 같은 이름으로 옆에 둔다.
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


def _display(job: dict) -> str:
    """알림용 이름 - prefix의 {tmdb-번호}는 빼고 보여 준다"""
    return job.get("display") or job["prefix"]


def _attach_subs(job: dict, video_path, sub_dirs, notify) -> bool:
    """기다리던 자막이 들어왔으면 영화 옆으로 옮긴다. 옮겼으면 True"""
    subs = movies.find_subtitles(job.get("subs") or [], sub_dirs)
    if not subs:
        return False
    placed = movies.place_subtitles(subs, video_path)
    log(f"{job['prefix']} 자막 {len(placed)}개 → {video_path.parent}")
    notify(f"💬 '{_display(job)}' 자막을 영화 옆에 넣었어요.\n" + "\n".join(f"• {p.name}" for p in placed))
    return True


def process(jobs: list[dict], host_dir=movies.HOST_MOVIE_DIR, library=movies.MOVIE_LIBRARY,
            notify=tg_send, now=None, sub_dirs=None) -> dict[str, dict]:
    """받기가 끝난 작업의 파일을 옮기고 {btih: 바뀐 필드}를 돌려준다 (jobs는 건드리지 않음)"""
    now = now or time.time()
    changes: dict[str, dict] = {}
    for job in jobs:
        status = job.get("status")
        dest_dir = library / job["folder"]
        if status == "done" and job.get("subs_pending"):
            video = dest_dir / job["video"]
            if _attach_subs(job, video, sub_dirs, notify):
                changes[job["btih"]] = {"subs_pending": False}
            elif now - job.get("done", now) > movies.SUB_WAIT_SEC:
                changes[job["btih"]] = {"subs_pending": False}  # 그만 기다림
            continue
        if status != "downloading":
            continue
        job_dir = host_dir / job["btih"]
        if not movies.is_complete(job_dir):
            if now - job.get("added", now) > STALL_SEC:
                changes[job["btih"]] = {"status": "stalled"}
                notify(f"⚠ '{_display(job)}'이(가) 일주일째 다 안 받아졌어요. qBittorrent에서 확인해 주세요.")
            continue
        try:
            placed = movies.place_files(job_dir, job["prefix"], dest_dir)
        except Exception as e:
            log(f"{job['prefix']} 옮기기 실패: {type(e).__name__}: {e}")
            continue
        shutil.rmtree(job_dir, ignore_errors=True)  # 남은 nfo·샘플 등
        videos = sorted((p for p in placed if p.suffix.lower() in movies.VIDEO_EXTS),
                        key=lambda p: p.stat().st_size, reverse=True)
        change = {"status": "done", "done": int(now), "files": [p.name for p in placed]}
        if videos:
            change["video"] = videos[0].name
        log(f"{job['prefix']} → {dest_dir} ({len(placed)}개)")
        names = "\n".join(f"• {p.name}" for p in placed[:5])
        msg = f"✅ '{_display(job)}' 받아서 영화/{job['folder']} 폴더에 넣었어요.\n{names}"
        if job.get("subs") and videos:
            if _attach_subs(job, videos[0], sub_dirs, lambda t: None):
                msg += "\n💬 미리 받아 둔 자막도 같은 이름으로 옆에 넣었어요."
            else:
                change["subs_pending"] = True
                msg += (f"\n💬 따로 붙은 자막을 받아서 Drive에 넣어 주시면 영화 이름에 맞춰 옮길게요.\n"
                        f"{movies.post_url(job['post_id'])}")
        changes[job["btih"]] = change
        notify(msg)
    return changes


def main() -> int:
    jobs = movies.load_jobs(JOBS_PATH)
    if not any(j.get("status") == "downloading" or j.get("subs_pending") for j in jobs):
        return 0
    # 볼륨을 넘는 복사는 몇 분 걸리므로 잠금 밖에서 옮기고, 결과만 잠금 안에서 저장한다
    changes = process(jobs)
    if changes:
        def apply(current):
            for j in current:
                # 같은 영화를 취소했다가 다시 받으면 해시가 같은 옛 기록(취소됨)이 남아 있다
                if j.get("btih") in changes and j.get("status") != "canceled":
                    j.update(changes[j["btih"]])
        movies.update_jobs(JOBS_PATH, apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
