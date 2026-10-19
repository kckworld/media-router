# -*- coding: utf-8 -*-
"""유튜브 다운로드 (2026-10-19).

휴대폰 유튜브 앱에서 공유한 링크(텔레그램 봇) 또는 웹 화면에서 넣은 링크를
yt-dlp로 받아 Drive 폴더에 넣는다. 영상은 Drive 루트에 두면 기존 파이프라인
(drama_name.sh → media_router.py → 인코딩)이 그대로 이어받는다.

- 영상: 1080p H.264 + AAC(128k) mp4. 파일명은 그동안 쓰던 다운로더 형식
  "제목 ¦ 채널 (1080p_30fps_H264-128kbit_AAC).mp4"에 맞춘다 - 불꽃야구
  파일명 치환 규칙(rename_rules.conf)이 이 형식을 전제로 한다. 한국어 자막이
  올라와 있으면 "<같은 이름>.ko.srt"도 같이 받는다.
- 음악: mp3(최고 음질, 썸네일·태그 포함) → Drive/음악 (배치는 mp3를 건드리지 않음)

받는 동안은 Drive 밖의 임시 폴더(~/.ytdl-tmp)에 두고, 다 받은 뒤 같은 공유폴더
안에서 rename으로 한 번에 옮긴다. 배치가 받는 중인 파일을 집어가지 않게 하고,
Synology Drive가 조각 파일을 PC로 동기화하지 않게 하려는 것.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

HOME_ROOT = Path(os.environ.get("YTDL_HOME_ROOT", "/app/kck_home"))
DRIVE_DIR = HOME_ROOT / "Drive"
MUSIC_DIR = DRIVE_DIR / "음악"
TMP_ROOT = HOME_ROOT / ".ytdl-tmp"
# Drive에 넣는 파일은 kck9010 소유 + 666이어야 Synology Drive가 PC로 동기화한다
# (2026-10-14 root 소유로 옮겼다가 동기화가 안 된 사고)
OWNER_UID, OWNER_GID = 1026, 100

MAX_JOBS_KEPT = 50
# YouTube가 자주 바뀌어서 yt-dlp가 오래되면 금방 못 받게 된다 - 하루 한 번 최신으로
UPDATE_INTERVAL_SEC = 24 * 3600

_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|shorts/|live/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")


def extract_video_id(text: str) -> str | None:
    """공유 메시지(링크 앞뒤에 제목 등이 붙을 수 있음)에서 유튜브 영상 ID를 찾는다."""
    m = _YT_ID_RE.search(text or "")
    return m.group(1) if m else None


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


_VCODEC_NAMES = (("avc1", "H264"), ("vp09", "VP9"), ("vp9", "VP9"), ("av01", "AV1"), ("hev1", "H265"), ("hvc1", "H265"))
_ACODEC_NAMES = (("mp4a", "AAC"), ("opus", "OPUS"))


def quality_suffix(height, fps, vcodec: str, acodec: str, abr) -> str:
    """기존 다운로더 표기 "(1080p_30fps_H264-128kbit_AAC)"를 실제 받은 포맷으로 만든다."""
    v = next((name for prefix, name in _VCODEC_NAMES if (vcodec or "").startswith(prefix)), (vcodec or "?").split(".")[0].upper())
    a = next((name for prefix, name in _ACODEC_NAMES if (acodec or "").startswith(prefix)), (acodec or "?").split(".")[0].upper())
    try:
        kbit = round(float(abr))
    except (TypeError, ValueError):
        kbit = 0
    # 유튜브 m4a(140)는 129~130k로 표시되지만 128k 인코딩이다 - 기존 파일명과 같게
    if 120 <= kbit <= 135:
        kbit = 128
    try:
        fps = round(float(fps))
    except (TypeError, ValueError):
        fps = 0
    return f"({height}p_{fps}fps_{v}-{kbit}kbit_{a})"


def unique_path(path: Path) -> Path:
    """같은 이름이 있으면 덮어쓰지 않고 " (1)"을 붙인다 (2026-09-30 mv 덮어쓰기 유실 사고)."""
    if not path.exists():
        return path
    stem, suffix = path.name, ""
    # "이름.ko.srt"처럼 확장자가 둘인 자막도 이름 쪽에 번호를 붙인다
    for ext in (".ko.srt", path.suffix):
        if ext and stem.endswith(ext):
            stem, suffix = stem[: -len(ext)], ext
            break
    n = 1
    while True:
        cand = path.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists():
            return cand
        n += 1


def build_command(mode: str, url: str) -> list[str]:
    common = [
        "yt-dlp", "--no-playlist", "--newline", "--no-colors",
        # 기존 파일명처럼 제목의 | 를 ¦ 로. 나머지 윈도우 금지 문자는 전각으로(PC로 동기화되므로)
        "--replace-in-metadata", "title", r"\|", "¦", "--windows-filenames",
        "--progress-template", "download:PROG %(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "--print", "before_dl:TITLE %(title)s",
    ]
    if mode == "audio":
        return common + [
            "-f", "bestaudio", "-x", "--audio-format", "mp3", "--audio-quality", "0",
            "--embed-thumbnail", "--embed-metadata",
            "-o", "%(title)s.%(ext)s",
            "--print", "after_move:FILE %(filepath)s",
            url,
        ]
    return common + [
        # 1080p H.264 + AAC를 우선(기존 파일과 같은 조합 → 이후 인코딩 흐름도 동일). 없으면 가능한 최선
        "-f", "bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/b[vcodec^=avc1][height<=1080]/bv*[height<=1080]+ba/b",
        "--merge-output-format", "mp4",
        # 불꽃야구는 올린 자막이 없고 자동 생성 자막뿐이다(기존 .ko.srt도 자동 자막). 둘 다 있으면 올린 자막이 우선
        "--write-subs", "--write-auto-subs", "--sub-langs", "ko", "--convert-subs", "srt",
        "-o", "%(title)s.%(ext)s",
        "--print", "after_move:FILE %(filepath)s",
        "--print", "after_move:INFO %(height)s|%(fps)s|%(vcodec)s|%(acodec)s|%(abr)s",
        url,
    ]


class Downloader:
    """한 번에 하나씩 순서대로 받는다(회선이 하나라 동시에 받아도 빨라지지 않음)."""

    def __init__(self, state_path: Path, notify=None, log=None):
        self.state_path = state_path
        self.notify = notify or (lambda text: None)
        self.log = log or (lambda msg: None)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.jobs: list[dict] = self._load()
        self.last_update = 0.0
        # 컨테이너가 재시작되면 받던 작업은 끊긴 것 - 다시 대기열에 넣지 않고 실패로 표시
        for j in self.jobs:
            if j["status"] in ("queued", "downloading"):
                j.update(status="error", error="서버가 재시작돼서 중단됐어요. 다시 넣어 주세요.")
        self._save()
        threading.Thread(target=self._worker, daemon=True).start()

    # ── 상태 저장 ──
    def _load(self) -> list[dict]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self) -> None:
        try:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.jobs[-MAX_JOBS_KEPT:], ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError as e:
            self.log(f"유튜브 작업 상태 저장 실패: {e}")

    def list_jobs(self) -> list[dict]:
        with self.lock:
            return [dict(j) for j in reversed(self.jobs)]

    def enqueue(self, url: str, mode: str, source: str) -> dict:
        vid = extract_video_id(url)
        if not vid:
            raise ValueError("유튜브 링크를 찾지 못했어요.")
        if mode not in ("video", "audio"):
            raise ValueError("영상 또는 음악만 고를 수 있어요.")
        with self.lock:
            for j in self.jobs:
                if j["video_id"] == vid and j["mode"] == mode and j["status"] in ("queued", "downloading"):
                    raise ValueError("이미 받는 중인 영상이에요.")
            job = {"id": uuid.uuid4().hex[:10], "video_id": vid, "url": video_url(vid), "mode": mode,
                   "source": source, "status": "queued", "title": "", "percent": 0.0, "speed": "", "eta": "",
                   "files": [], "error": "", "created": time.time(), "finished": None}
            self.jobs.append(job)
            del self.jobs[:-MAX_JOBS_KEPT]
            self._save()
        self.wake.set()
        return dict(job)

    # ── 작업 실행 ──
    def _worker(self) -> None:
        while True:
            self.wake.wait(30)
            self.wake.clear()
            while True:
                with self.lock:
                    job = next((j for j in self.jobs if j["status"] == "queued"), None)
                if not job:
                    break
                self._run(job)

    def _set(self, job: dict, **kw) -> None:
        with self.lock:
            job.update(kw)

    def _maybe_update(self) -> None:
        if time.time() - self.last_update < UPDATE_INTERVAL_SEC:
            return
        self.last_update = time.time()
        try:
            r = subprocess.run(["python", "-m", "pip", "install", "-q", "--root-user-action=ignore",
                                "-U", "yt-dlp[default]"], capture_output=True, text=True, timeout=180)
            if r.returncode != 0:
                self.log(f"yt-dlp 업데이트 실패: {r.stderr.strip()[-300:]}")
        except Exception as e:
            self.log(f"yt-dlp 업데이트 실패: {type(e).__name__}")

    def _run(self, job: dict) -> None:
        self._maybe_update()
        self._set(job, status="downloading")
        self._save()
        work = TMP_ROOT / job["id"]
        try:
            work.mkdir(parents=True, exist_ok=True)
            produced, info = self._download(job, work)
            dest_dir = MUSIC_DIR if job["mode"] == "audio" else DRIVE_DIR
            final = self._place(job, produced, info, dest_dir, work)
            self._set(job, status="done", percent=100.0, files=[p.name for p in final],
                      finished=time.time(), speed="", eta="")
            size = sum(p.stat().st_size for p in final if p.exists())
            where = "Drive/음악" if job["mode"] == "audio" else "Drive"
            size_text = f"{size / 1e9:.2f}GB" if size >= 1e9 else f"{size / 1e6:.0f}MB"
            self.notify(f"✅ 유튜브 {('음악' if job['mode'] == 'audio' else '영상')} 받았어요 ({size_text} → {where})\n{final[0].name}")
        except Exception as e:
            msg = str(e) or type(e).__name__
            self._set(job, status="error", error=msg[-500:], finished=time.time(), speed="", eta="")
            self.notify(f"❌ 유튜브 받기 실패: {job.get('title') or job['url']}\n{msg[-300:]}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
            self._save()

    def _download(self, job: dict, work: Path) -> tuple[list[Path], dict]:
        cmd = build_command(job["mode"], job["url"])
        proc = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
        produced: list[Path] = []
        info: dict = {}
        tail: list[str] = []
        last_save = 0.0
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("PROG "):
                pct, _, rest = line[5:].partition("|")
                speed, _, eta = rest.partition("|")
                try:
                    p = float(pct.strip().rstrip("%"))
                except ValueError:
                    p = job["percent"]
                self._set(job, percent=p, speed=speed.strip(), eta=eta.strip())
                if time.time() - last_save > 5:
                    last_save = time.time()
                    self._save()
            elif line.startswith("TITLE "):
                self._set(job, title=line[6:])
                self._save()
            elif line.startswith("FILE "):
                produced.append(Path(line[5:]))
            elif line.startswith("INFO "):
                h, fps, vc, ac, abr = (line[5:].split("|") + [""] * 5)[:5]
                info = {"height": h, "fps": fps, "vcodec": vc, "acodec": ac, "abr": abr}
            else:
                tail.append(line)
                del tail[:-15]
        rc = proc.wait()
        if rc != 0 or not produced:
            err = "\n".join(l for l in tail if "ERROR" in l) or "\n".join(tail[-5:])
            raise RuntimeError(err or f"yt-dlp 종료 코드 {rc}")
        # 자막은 --print에 안 나오므로 작업 폴더에서 찾는다
        produced += [p for p in work.glob("*.srt") if p not in produced]
        return produced, info

    def _place(self, job: dict, produced: list[Path], info: dict, dest_dir: Path, work: Path) -> list[Path]:
        if not dest_dir.exists():
            dest_dir.mkdir(parents=True)
            try:
                os.chown(dest_dir, OWNER_UID, OWNER_GID)
                os.chmod(dest_dir, 0o777)
            except OSError:
                pass
        main =next((p for p in produced if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mp3")), produced[0])
        base = main.stem
        if job["mode"] == "video" and info.get("height"):
            base = f"{base} {quality_suffix(info['height'], info['fps'], info['vcodec'], info['acodec'], info['abr'])}"
        final: list[Path] = []
        # 영상을 먼저 옮기고 자막은 그 이름을 따라간다(영상 이름이 " (1)"로 바뀌면 자막도 같이)
        target = unique_path(dest_dir / f"{base}{main.suffix}")
        for p in [main] + [x for x in produced if x != main]:
            if p == main:
                dst = target
            else:
                lang_ext = "".join(p.suffixes[-2:]) if p.name.endswith(".ko.srt") else p.suffix
                dst = unique_path(dest_dir / f"{target.stem}{lang_ext}")
            src = p if p.is_absolute() else work / p
            try:
                os.chown(src, OWNER_UID, OWNER_GID)
                os.chmod(src, 0o666)
            except OSError:
                pass
            # 같은 공유폴더(homes) 안이라 rename은 즉시·원자적이다 - 반쯤 옮겨진 파일이 Drive에 보일 일이 없다
            os.rename(src, dst)
            final.append(dst)
        return final
