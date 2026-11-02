# -*- coding: utf-8 -*-
"""ASS/SSA 자막 → SRT (2026-11-02 사용자 결정: Plex 실시간 변환·버퍼링 줄이려고 ASS는 전부 SRT로).

위치·색·효과는 버린다(사용자가 알고 정함). 표준 라이브러리만 쓴다(호스트 파이썬 3.8/3.9에서도 돈다).
- 외부 파일:    python3 ass2srt.py file <a.ass> [...]     → 같은 이름 .srt(언어 꼬리표 유지), 원본은 보관 폴더로
- mkv 내장 트랙: python3 ass2srt.py mkv <a.mkv> [...]     → ASS 트랙만 SRT로 다시 묶기(영상·음성 복사)
- 확인만:       python3 ass2srt.py check <a.mkv|a.ass>
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

FFMPEG = os.getenv("FFMPEG_BIN", "/bin/ffmpeg")
HOLD = Path(os.getenv("ASS2SRT_HOLD", "/volume1/video/임시/ass원본"))   # 바꾼 mkv 원본 잠깐 보관(같은 공유폴더라 즉시 이동)
LOG = Path(os.getenv("ASS2SRT_LOG", "/volume1/docker/ass2srt/ass2srt.log"))

_TAG = re.compile(r"\{[^}]*\}")
_DRAW = re.compile(r"\{[^}]*\\p[1-9][^}]*\}")       # 그림 그리기 명령(간판 도형) - 글자가 아니라 좌표라 버린다
_HTML_OK = re.compile(r"</?(?:i|b|u|font)(?:\s[^>]*)?>", re.I)


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def decode(data: bytes) -> str:
    """UTF-8(BOM 포함) → UTF-16 → CP949 순으로"""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _ts(s: str) -> int:
    """'0:00:15.05' → 밀리초"""
    h, m, rest = s.strip().split(":")
    sec, _, cs = rest.partition(".")
    return ((int(h) * 60 + int(m)) * 60 + int(sec)) * 1000 + int((cs + "00")[:2]) * 10


def _srt_ts(ms: int) -> str:
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def clean_text(raw: str) -> str:
    if _DRAW.search(raw):
        return ""
    t = _TAG.sub("", raw)
    t = t.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    # SRT에서 Plex가 아는 <i><b><u><font>만 남기고 다른 꺾쇠 태그는 지운다
    t = re.sub(r"<[^>]+>", lambda m: m.group(0) if _HTML_OK.fullmatch(m.group(0)) else "", t)
    t = re.sub(r'\s+face="[^"]*"', "", t, flags=re.I)                 # 글꼴 지정은 SRT에서 의미 없음
    lines = [ln.strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def parse_ass(text: str) -> list[tuple[int, int, str]]:
    """[(시작ms, 끝ms, 글)] - Dialogue만, Comment는 버림"""
    fmt = None
    cues = []
    in_events = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_events = s.lower() == "[events]"
            continue
        if not in_events:
            continue
        if s.lower().startswith("format:"):
            fmt = [x.strip().lower() for x in s[7:].split(",")]
            continue
        if not s.lower().startswith("dialogue:") or not fmt:
            continue
        parts = s[9:].split(",", len(fmt) - 1)
        if len(parts) < len(fmt):
            continue
        row = dict(zip(fmt, parts))
        try:
            start, end = _ts(row["start"]), _ts(row["end"])
        except (KeyError, ValueError):
            continue
        body = clean_text(row.get("text", ""))
        if body and end > start:
            cues.append((start, end, body))
    return cues


def _plain(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t)


def merge_cues(cues: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """- 같은 시각에 같은 글(색만 다른 그림자 줄 포함)은 하나로(나중 줄 = 위 레이어를 남김)
    - 같은 글이 끊김 없이 이어지면 한 줄로
    - 노래 가사 글자별 효과(W → We → We A …)처럼 앞 줄에 글자가 붙어 이어지면 마지막 모양 하나로"""
    by_slot: dict = {}
    for st, en, tx in cues:
        by_slot[(st, en, _plain(tx))] = (st, en, tx)
    out: list[list] = []
    for st, en, tx in sorted(by_slot.values(), key=lambda c: (c[0], c[1])):
        p = _plain(tx)
        for c in reversed(out[-6:]):
            cp = _plain(c[2])
            if 0 <= st - c[1] <= 40 and (cp == p or (p.startswith(cp) and len(p) > len(cp))):
                c[1] = max(c[1], en)
                c[2] = tx
                break
        else:
            out.append([st, en, tx])
    return [tuple(c) for c in out]


def ass_to_srt(text: str) -> tuple[str, int]:
    cues = merge_cues(parse_ass(text))
    blocks = [f"{i}\n{_srt_ts(st)} --> {_srt_ts(en)}\n{tx}\n" for i, (st, en, tx) in enumerate(cues, 1)]
    return "\n".join(blocks), len(cues)


def srt_name(p: Path) -> Path:
    """a.ko.ass → a.ko.srt (언어 꼬리표 유지)"""
    return p.with_suffix(".srt")


def copy_owner(src: Path, dst: Path) -> None:
    """Plex·Jellyfin이 읽게 권한 664, 소유자는 원본과 같게(root로 돌 때만 됨)"""
    try:
        st = src.stat()
        os.chmod(dst, 0o664)
        os.chown(dst, st.st_uid, st.st_gid)
    except OSError:
        pass


def hold_path(p: Path, hold: Path | None = None) -> Path:
    hold = hold or HOLD
    try:
        return hold / p.relative_to("/volume1/video")   # 원래 폴더 구조 그대로 보관
    except ValueError:
        return hold / p.name


def convert_file(p: Path, hold: Path | None = None) -> tuple[bool, str]:
    """외부 .ass/.ssa → 같은 이름 .srt. 같은 이름 .srt가 있으면 건드리지 않는다.
    원본은 바로 지우지 않고 보관 폴더로 옮긴다(같은 공유폴더라 즉시) - 며칠 뒤 확인하고 지움."""
    dst = srt_name(p)
    if dst.exists():
        return False, f"같은 이름 SRT가 이미 있음: {dst.name}"
    srt, n = ass_to_srt(decode(p.read_bytes()))
    if n == 0:
        return False, "대사가 0줄(변환할 게 없음)"
    keep = hold_path(p, hold)
    if keep.exists():
        return False, f"보관 폴더에 같은 이름이 있음: {keep}"
    tmp = dst.with_name("." + dst.name + ".tmp")
    tmp.write_text(srt, encoding="utf-8")
    copy_owner(p, tmp)
    os.replace(tmp, dst)
    keep.parent.mkdir(parents=True, exist_ok=True)
    os.replace(p, keep)
    return True, f"{n}줄, 원본 보관: {keep}"


# ── mkv 내장 ASS ──────────────────────────────────────

_STREAM = re.compile(r"Stream #0:(\d+)(?:\((\w+)\))?(?:\[[^\]]*\])?: (\w+): (\w+)([^\n]*)")


def streams(video: Path) -> list[dict]:
    """ffmpeg -i 출력에서 [{index, lang, type, codec, default, title}] (호스트엔 ffprobe가 없음)"""
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video)], capture_output=True, timeout=120)
    text = r.stderr.decode("utf-8", errors="replace")
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _STREAM.search(line)
        if not m:
            continue
        title = ""
        for nxt in lines[i + 1:i + 8]:
            if "Stream #" in nxt:
                break
            tm = re.match(r"\s+title\s*:\s*(.*)$", nxt)
            if tm:
                title = tm.group(1).strip()
                break
        out.append({"index": int(m.group(1)), "lang": (m.group(2) or "").lower(), "type": m.group(3).lower(),
                    "codec": m.group(4).lower(), "default": "(default)" in m.group(5), "title": title})
    return out


def duration(video: Path) -> float:
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(video)], capture_output=True, timeout=120)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr.decode("utf-8", errors="replace"))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def has_embedded_ass(video: Path) -> bool:
    return any(s["type"] == "subtitle" and s["codec"] in ("ass", "ssa") for s in streams(video))


def convert_mkv(video: Path, hold: Path | None = None, discard: bool = False) -> tuple[bool, str]:
    """mkv 안의 ASS 트랙만 SRT로 바꿔 다시 묶는다. 영상·음성·다른 자막은 그대로 복사, 첨부 폰트는 뺀다.
    결과는 같은 폴더 숨김 임시 이름 → 길이·트랙 수 확인 → 원본은 hold로 옮기고 결과를 원래 이름으로.
    discard=True면 원본을 보관하지 않고 바로 바꾼다(재인코딩 결과물처럼 원본이 따로 있는 중간 파일용)."""
    st = streams(video)
    subs = [s for s in st if s["type"] == "subtitle"]
    ass = [s for s in subs if s["codec"] in ("ass", "ssa")]
    if not ass:
        return False, "내장 ASS 없음"
    work = video.with_name("." + video.stem + ".srtwork")
    work.mkdir(exist_ok=True)
    try:
        srts = []
        for s in ass:
            raw = work / f"{s['index']}.ass"
            r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                                "-map", f"0:{s['index']}", "-c:s", "copy", str(raw)], capture_output=True, timeout=600)
            if r.returncode != 0 or not raw.exists():
                return False, f"트랙 {s['index']} 꺼내기 실패: {r.stderr.decode(errors='replace')[-200:]}"
            srt, n = ass_to_srt(decode(raw.read_bytes()))
            if n == 0:
                return False, f"트랙 {s['index']} 대사 0줄"
            sp = work / f"{s['index']}.srt"
            sp.write_text(srt, encoding="utf-8")
            srts.append((s, sp, n))
        out = video.with_name("." + video.stem + ".srt.mkv")
        args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video)]
        for _, sp, _ in srts:
            args += ["-sub_charenc", "UTF-8", "-i", str(sp)]
        # 원래 트랙 순서 유지: 영상·음성 → (원래 자리에) 자막(ASS 자리는 새 SRT)
        args += ["-map_metadata", "0", "-map_chapters", "0"]
        ass_pos = {s["index"]: k for k, (s, _, _) in enumerate(srts)}
        out_subs = []
        for s in st:
            if s["type"] in ("attachment", "data"):
                continue
            if s["index"] in ass_pos:
                args += ["-map", f"{ass_pos[s['index']] + 1}:0"]
            else:
                args += ["-map", f"0:{s['index']}"]
            if s["type"] == "subtitle":
                out_subs.append(s)
        args += ["-c", "copy"]
        for k, s in enumerate(out_subs):
            if s["index"] in ass_pos:
                args += [f"-c:s:{k}", "srt"]
            if s["lang"]:
                args += [f"-metadata:s:s:{k}", f"language={s['lang']}"]
            if s["title"]:
                args += [f"-metadata:s:s:{k}", f"title={s['title']}"]
            args += [f"-disposition:s:{k}", "default" if s["default"] else "0"]
        args += [str(out)]
        r = subprocess.run(args, capture_output=True, timeout=3600)
        if r.returncode != 0 or not out.exists():
            out.unlink(missing_ok=True) if out.exists() else None
            return False, f"다시 묶기 실패: {r.stderr.decode(errors='replace')[-300:]}"
        # 확인: 길이 ±2초, 영상·음성 수 같음, 자막 수 같음, ASS 0개
        new = streams(out)
        cnt = lambda ss, t: sum(1 for x in ss if x["type"] == t)
        problems = []
        if abs(duration(out) - duration(video)) > 2:
            problems.append("길이가 다름")
        for t in ("video", "audio", "subtitle"):
            if cnt(new, t) != cnt(st, t):
                problems.append(f"{t} 수 다름({cnt(st, t)}→{cnt(new, t)})")
        if any(x["type"] == "subtitle" and x["codec"] in ("ass", "ssa") for x in new):
            problems.append("ASS가 남음")
        if problems:
            out.unlink()
            return False, "확인 실패: " + ", ".join(problems)
        lines = ", ".join(f"{s['lang'] or '?'} {n}줄" for s, _, n in srts)
        if discard:
            copy_owner(video, out)
            os.replace(out, video)
            return True, f"ASS {len(srts)}개 → SRT ({lines})"
        dst_hold = hold_path(video, hold)
        dst_hold.parent.mkdir(parents=True, exist_ok=True)
        if dst_hold.exists():
            out.unlink()
            return False, f"보관 폴더에 같은 이름이 있음: {dst_hold}"
        copy_owner(video, out)
        os.replace(video, dst_hold)
        os.replace(out, video)
        lines = ", ".join(f"{s['lang'] or '?'} {n}줄" for s, _, n in srts)
        return True, f"ASS {len(srts)}개 → SRT ({lines}), 원본 보관: {dst_hold}"
    finally:
        for p in work.glob("*"):
            p.unlink()
        work.rmdir()


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, paths = argv[1], [Path(a) for a in argv[2:]]
    bad = 0
    for p in paths:
        try:
            if cmd == "file":
                ok, msg = convert_file(p)
            elif cmd == "mkv":
                ok, msg = convert_mkv(p)
            elif cmd == "check":
                if p.suffix.lower() in (".ass", ".ssa"):
                    srt, n = ass_to_srt(decode(p.read_bytes()))
                    ok, msg = n > 0, f"{n}줄\n" + srt[:600]
                else:
                    ok, msg = True, json.dumps(streams(p), ensure_ascii=False)
            else:
                print(__doc__)
                return 2
        except Exception as e:
            ok, msg = False, f"오류: {e}"
        log(f"{'OK' if ok else '실패'} {cmd} {p}: {msg}")
        bad += 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
