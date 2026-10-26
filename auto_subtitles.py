"""애니 자동 한국어 자막 (2026-10-19).

코난·원피스 SubsPlease 릴에는 영어 자막(ASS)이 들어 있다. 새 회차에 한국어 자막이 없으면
그 영어 자막을 꺼내 DeepL(무료 API)로 번역해 "<영상이름>.ko.ass"로 둔다. 타이밍·스타일은 원본 그대로.
먼작귀(ToonsHub, 10-26)처럼 영어 자막이 SRT면 "<영상이름>.ko.srt"로 둔다. 영어 트랙이 없으면
중국어 트랙(먼작귀 VIU판 등)을 번역한다. 둘 다 없으면 만들지 않는다.
나중에 팬 자막(사용자가 PC/폰에서 받아 Synology Drive → media_router → aniname 경로로 들어옴)이
오면 자동 자막을 지우고 팬 자막을 쓴다.

팬 자막과 만나는 경우 (그래서 자동 자막은 '이름'이 아니라 '내용 해시'로 추적한다):
  - aniname이 같은 이름으로 rename하면서 자동 자막을 덮어씀 → 해시가 달라졌으니 교체 완료로 본다
  - media_router가 이름 충돌로 팬 자막을 "<이름>.ko_1.ass"로 둠 → 자동 자막을 지우고 팬 자막을 원래 이름으로
  - 팬 자막이 다른 형식(.ko.srt 등) → 자동 자막만 지운다
  - aniname이 제목을 붙이며 자동 자막 이름을 바꿈 → 해시로 다시 찾는다

실행: run_all.sh에서 aniname 다음 (호스트, root, 표준 라이브러리만 사용 — DSM 기본 python3.8)
  python auto_subtitles.py "<코난 폴더>" "<원피스 시즌 폴더>"
DeepL 키: 환경변수 DEEPL_API_KEY 또는 이 폴더 .env 의 DEEPL_API_KEY (무료 키는 끝이 ':fx')
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE_PATH = Path(os.getenv("AUTO_SUBS_STATE", str(BASE / "data" / "auto_subs.json")))
FFMPEG = os.getenv("FFMPEG_BIN", "/bin/ffmpeg")
MAX_AGE_DAYS = float(os.getenv("AUTO_SUBS_MAX_AGE_DAYS", "7"))   # 이보다 오래된 회차는 건드리지 않음 (무료 한도 보호)
MIN_STABLE_SEC = 120                                             # 막 옮겨진 파일은 다음 실행에
VIDEO_EXTS = (".mkv", ".mp4")
SUB_EXTS = (".ass", ".ssa", ".srt", ".smi", ".vtt", ".sub")
# '코난.S01.E1217' 과 '[Gecko] Chiikawa - S01E382' 둘 다
EP_RE = re.compile(r"(?:\.|S\d{1,2})E(\d{1,5})\b")
KO_RE = re.compile(r"\.(ko|kor|kr)(_\d+)?\.[^.]+$", re.I)
MARKER = "; Media Router 자동 번역 자막 (영어 자막 → DeepL). 팬 자막이 들어오면 자동으로 교체됩니다."


def log(msg: str) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), "auto_subtitles:", msg, flush=True)


def deepl_key() -> str:
    key = os.getenv("DEEPL_API_KEY", "").strip()
    if key:
        return key
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("DEEPL_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_PATH)


# ── ASS 번역 ───────────────────────────────────────────────
TAG_RE = re.compile(r"\{[^}]*\}")


class DeepLError(Exception):
    pass


def deepl_translate(texts: list[str], key: str, context: str, translate=None, source: str = "EN") -> list[str]:
    """texts → 한국어. 중복 문장은 한 번만 보낸다. 50개씩 나눠 보낸다(DeepL 한 요청 최대 50개)."""
    if translate is not None:          # 테스트용 주입
        return translate(texts)
    url = "https://api-free.deepl.com/v2/translate" if key.endswith(":fx") else "https://api.deepl.com/v2/translate"
    uniq = list(dict.fromkeys(texts))
    out: dict[str, str] = {}
    for i in range(0, len(uniq), 50):
        chunk = uniq[i:i + 50]
        body = json.dumps({"text": chunk, "source_lang": source, "target_lang": "KO",
                           "context": context, "preserve_formatting": True}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"DeepL-Auth-Key {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            reason = {403: "API 키가 올바르지 않음", 456: "이번 달 무료 번역 한도 초과", 429: "요청이 너무 많음"}.get(e.code, "")
            raise DeepLError(f"DeepL 오류 {e.code} {reason}".strip())
        for src, t in zip(chunk, data.get("translations", [])):
            out[src] = t.get("text", src)
    return [out.get(t, t) for t in texts]


def _has_text(s: str, source: str) -> bool:
    """번역할 글자가 있는지 (영어는 알파벳, 중국어는 한자)"""
    return bool(re.search(r"[\u4e00-\u9fff]" if source == "ZH" else r"[A-Za-z]", s))


def translate_ass(ass_text: str, key: str, context: str, translate=None, source: str = "EN") -> tuple[str, int]:
    """Dialogue의 대사만 번역. 맨 앞 스타일 태그({\\an8} 등)는 남기고, 대사 중간 태그는 뺀다.
    도형(\\p1)·빈 줄은 그대로. 반환: (번역된 ASS, 보낸 글자 수)"""
    lines = ass_text.splitlines()
    jobs = []   # (줄 번호, 앞부분, 맨 앞 태그, 번역할 문장)
    for i, line in enumerate(lines):
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        text = parts[9]
        if re.search(r"\\p[1-9]", text):
            continue
        lead = re.match(r"^(\{[^}]*\})*", text).group(0)
        plain = TAG_RE.sub("", text[len(lead):]).replace("\\N", "\n").replace("\\n", "\n").strip()
        if not plain or not _has_text(plain, source):
            continue
        jobs.append((i, ",".join(parts[:9]) + ",", lead, plain))
    if not jobs:
        return ass_text, 0
    translated = deepl_translate([j[3] for j in jobs], key, context, translate, source)
    for (i, head, lead, _), ko in zip(jobs, translated):
        lines[i] = head + lead + ko.replace("\r", "").replace("\n", "\\N")
    out = "\n".join(lines) + "\n"
    marker = MARKER if source == "EN" else MARKER.replace("영어 자막", "중국어 자막")
    out = out.replace("[Script Info]", "[Script Info]\n" + marker, 1)
    out = re.sub(r"^Title:.*$", "Title: 한국어 자동 번역 (DeepL)", out, count=1, flags=re.M)
    return out, sum(len(j[3]) for j in jobs)


SUB_STREAM_RE = re.compile(r"Stream #\d+:\d+(?:\((\w+)\))?(?:\[[^\]]*\])?: Subtitle: (\w+)")


def subtitle_tracks(video: Path) -> list[tuple[str, str]]:
    """[(언어, 코덱)] - 호스트엔 ffprobe가 없어서 ffmpeg -i 출력에서 읽는다"""
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video)], capture_output=True, timeout=60)
    text = r.stderr.decode("utf-8", errors="replace")
    return [((m.group(1) or "").lower(), m.group(2).lower()) for m in SUB_STREAM_RE.finditer(text)]


def pick_english(tracks: list[tuple[str, str]]) -> int | None:
    """영어 트랙 번호. 언어 표시가 없으면(und 포함) 첫 트랙을 영어로 본다. 다른 언어뿐이면 None."""
    for i, (lang, _) in enumerate(tracks):
        if lang in ("eng", "en"):
            return i
    if tracks and tracks[0][0] in ("", "und"):
        return 0
    return None


def pick_source(tracks: list[tuple[str, str]]) -> tuple[int, str] | None:
    """번역할 트랙 (번호, DeepL 원문 언어). 영어 우선, 없으면 중국어(간·번체 모두 ZH)."""
    idx = pick_english(tracks)
    if idx is not None:
        return idx, "EN"
    for i, (lang, _) in enumerate(tracks):
        if lang in ("chi", "zho", "zh"):
            return i, "ZH"
    return None


def extract_english_sub(video: Path) -> tuple[str, str, str] | None:
    """영상 속 영어(없으면 중국어) 자막 → ('ass'|'srt', 내용, 'EN'|'ZH'). 없으면 None."""
    tracks = subtitle_tracks(video)
    picked = pick_source(tracks)
    if picked is None:
        return None
    idx, source = picked
    fmt = "srt" if tracks[idx][1] in ("subrip", "srt", "mov_text", "webvtt") else "ass"
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / f"en.{fmt}"
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(video), "-map", f"0:s:{idx}", "-c:s", fmt, str(out)],
                           capture_output=True, timeout=300)
        if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            return None
        return fmt, out.read_text(encoding="utf-8-sig", errors="replace"), source


SRT_TAG_RE = re.compile(r"<[^>]+>|\{[^}]*\}")


def translate_srt(srt_text: str, key: str, context: str, translate=None, source: str = "EN") -> tuple[str, int]:
    """SRT 블록(번호, 시간, 대사)의 대사만 번역. 반환: (번역된 SRT, 보낸 글자 수)"""
    blocks = [b for b in re.split(r"\r?\n\s*\r?\n", srt_text.strip()) if b.strip()]
    parsed, jobs = [], []
    for b in blocks:
        lines = b.splitlines()
        t = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if t is None:
            continue
        text = SRT_TAG_RE.sub("", "\n".join(lines[t + 1:])).strip()
        parsed.append([lines[t], text])
        if text and _has_text(text, source):
            jobs.append(len(parsed) - 1)
    chars = sum(len(parsed[i][1]) for i in jobs)
    if jobs:
        translated = deepl_translate([parsed[i][1] for i in jobs], key, context, translate, source)
        for i, ko in zip(jobs, translated):
            parsed[i][1] = ko.replace("\r", "").strip()
    out = "\n\n".join(f"{n}\n{timing}\n{text}" for n, (timing, text) in enumerate(parsed, 1) if text) + "\n"
    return out, chars


# ── 폴더 처리 ──────────────────────────────────────────────
def episode_of(name: str) -> str | None:
    m = EP_RE.search(name)
    if not m:
        return None
    return m.group(1).lstrip("0") or "0"


def korean_subs(folder: Path, ep: str) -> list[Path]:
    return [p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in SUB_EXTS and KO_RE.search(p.name) and episode_of(p.name) == ep]


def copy_owner(src: Path, dst: Path) -> None:
    """Plex·Jellyfin이 읽을 수 있게 권한은 항상 664로. 소유자 맞추기는 root일 때만 되므로 따로 시도한다
    (2026-10-19 수동 실행 때 chown이 실패하면서 chmod까지 건너뛰어 -rw------- 자막이 생겼었다)."""
    try:
        os.chmod(dst, 0o664)
    except Exception:
        pass
    try:
        st = src.stat()
        os.chown(dst, st.st_uid, st.st_gid)
    except Exception:
        pass


def reconcile(folder: Path, key_prefix: str, state: dict) -> None:
    """이미 만든 자동 자막: 팬 자막이 들어왔으면 지우고(필요하면 팬 자막 이름 정리), 이름이 바뀌었으면 따라간다."""
    for skey, rec in list(state.items()):
        if not skey.startswith(key_prefix) or rec.get("status") != "auto":
            continue
        ep = rec["ep"]
        subs = korean_subs(folder, ep)
        mine = [p for p in subs if sha256(p) == rec["sha256"]]
        others = [p for p in subs if p not in mine]
        if not mine:
            # aniname이 같은 이름으로 덮어썼거나 누가 지움 → 팬 자막(있다면)이 자리를 차지한 것
            rec.update(status="replaced", replaced_at=time.time())
            log(f"E{ep}: 자동 자막이 팬 자막으로 바뀜(덮어쓰기) — {[p.name for p in others]}")
            continue
        if others:
            for p in mine:
                p.unlink()
            # media_router가 충돌로 "_1"을 붙였으면 원래 이름으로
            for p in others:
                clean = re.sub(r"\.(ko|kor|kr)_\d+(\.[^.]+)$", r".\1\2", p.name)
                if clean != p.name and not (folder / clean).exists():
                    p.rename(folder / clean)
                    log(f"E{ep}: 팬 자막 이름 정리 {p.name} → {clean}")
            rec.update(status="replaced", replaced_at=time.time())
            log(f"E{ep}: 팬 자막이 들어와 자동 자막 삭제 — {[p.name for p in mine]}")
        else:
            rec["path"] = str(mine[0])


def process_folder(folder: Path, state: dict, key: str, translate=None, now: float | None = None) -> dict:
    now = now or time.time()
    stats = {"created": 0, "chars": 0, "skipped_no_track": 0}
    if not folder.is_dir():
        log(f"폴더 없음: {folder}")
        return stats
    prefix = str(folder) + "|"
    reconcile(folder, prefix, state)
    # '먼작귀 (2022)/Season 1'이면 시즌 폴더 위의 작품 이름을 번역 맥락으로
    show = folder.parent.name if re.match(r"(?i)season\s*\d+$", folder.name) else folder.name
    for video in sorted(folder.iterdir()):
        if not (video.is_file() and video.suffix.lower() in VIDEO_EXTS):
            continue
        ep = episode_of(video.name)
        if ep is None:
            continue
        age = now - video.stat().st_mtime
        if age > MAX_AGE_DAYS * 86400 or age < MIN_STABLE_SEC:
            continue
        skey = prefix + ep
        if skey in state:              # 이미 만들었거나(auto/replaced) 만들 수 없음(no_track)
            continue
        if korean_subs(folder, ep):    # 팬 자막이 먼저 와 있음
            continue
        if not key and translate is None:
            log(f"E{ep}: 한국어 자막 없음 — DEEPL_API_KEY가 없어 건너뜀")
            continue
        sub = extract_english_sub(video)
        if sub is None:
            state[skey] = {"ep": ep, "status": "no_track", "at": now}
            stats["skipped_no_track"] += 1
            log(f"E{ep}: 영상에 영어·중국어 자막 트랙이 없어 만들 수 없음 — {video.name}")
            continue
        fmt, en, source = sub if len(sub) == 3 else (*sub, "EN")
        context = f"Subtitles of the anime '{show}'. Translate into natural spoken Korean."
        if fmt == "srt":
            ko, chars = translate_srt(en, key, context, translate, source)
        else:
            ko, chars = translate_ass(en, key, context, translate, source)
        dst = video.with_name(video.stem + f".ko.{fmt}")
        if dst.exists():               # 혹시 모를 경쟁 상황: 절대 덮어쓰지 않음
            continue
        dst.write_text(ko, encoding="utf-8")
        copy_owner(video, dst)
        state[skey] = {"ep": ep, "status": "auto", "path": str(dst), "sha256": sha256(dst), "at": now, "chars": chars,
                       "source": source}
        stats["created"] += 1
        stats["chars"] += chars
        log(f"E{ep}: 자동 자막 생성 {dst.name} ({chars}자{', 중국어 원문' if source == 'ZH' else ''})")
    return stats


def main(folders: list[str]) -> int:
    state = load_state()
    key = deepl_key()
    total = {"created": 0, "chars": 0}
    try:
        for f in folders:
            s = process_folder(Path(f), state, key)
            total["created"] += s["created"]
            total["chars"] += s["chars"]
    except DeepLError as e:
        log(str(e))
        save_state(state)
        return 2
    save_state(state)
    if total["created"]:
        log(f"완료: 자동 자막 {total['created']}개, DeepL {total['chars']}자")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
