# -*- coding: utf-8 -*-
from __future__ import annotations
import os, sys, time, shutil, fnmatch, subprocess, stat, pwd, grp, re, logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Iterable, Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
import requests
from db import load_cfg, update_rule_fields, set_config_value, DATA_DIR

BASE: Path = Path(__file__).resolve().parent
LOGDIR: Path = DATA_DIR / "logs"
LOGDIR.mkdir(parents=True, exist_ok=True)
LOGFILE: Path = LOGDIR / "media_router.log"

WEEKDAYS: List[str] = ["월", "화", "수", "목", "금", "토", "일"]
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Seoul"))
VIDEO_EXTENSIONS: set = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.ts', '.m2ts', '.mts', '.m4v', '.3gp', '.ogv', '.asf', '.divx', '.vob', '.f4v', '.mpg', '.mpeg', '.m2v'}

# media_router.log가 open("a")로 무한정 append만 되며 계속 커지던 문제를 막기 위해
# 5MB x 5개(최대 25MB)로 순환 저장한다. 로그 줄 형식(`[media_router TS] msg`)은 그대로 유지.
_logger = logging.getLogger("media_router")
_logger.setLevel(logging.INFO)
_logger.propagate = False
if not _logger.handlers:
    _fmt = logging.Formatter("[media_router %(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    _file_handler = RotatingFileHandler(LOGFILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    _file_handler.setFormatter(_fmt)
    _logger.addHandler(_file_handler)
    _stream_handler = logging.StreamHandler(sys.stdout)
    _stream_handler.setFormatter(_fmt)
    _logger.addHandler(_stream_handler)


def current_localtime() -> datetime:
    return datetime.now(APP_TZ)

def log(msg: str) -> None:
    _logger.info(msg)

def extract_episode_number(filename: str):
    """파일명에서 에피소드 번호 추출 (E01, E1, EP01, S01E05, 01화 등)"""
    # S01E05, S1E5 같은 패턴 (E 뒤 숫자 추출)
    match = re.search(r'[Ss]\d+[Ee](\d+)', filename)
    if match:
        return int(match.group(1))
    
    # E01, E1, EP01, EP1 같은 패턴
    match = re.search(r'\b[Ee][Pp]?\s*(\d+)\b', filename)
    if match:
        return int(match.group(1))
    
    # 01화, 1화 같은 패턴
    match = re.search(r'(\d+)\s*화', filename)
    if match:
        return int(match.group(1))
    
    return None

def sanitize_filename_for_plex(filename: str) -> str:
    """Plex TV 스캐너가 인식 못하는 토큰(예: .END) 제거"""
    return re.sub(r'(?i)\.END(?=\.[^.]+$)', '', filename)

def strip_encoder_tag(filename: str) -> str:
    """ShanaEncoder로 재인코딩하면 파일명 앞에 붙는 "[SHANA]" 태그 제거.
    최종 라이브러리 폴더에는 원래 이름 그대로 들어가야 하므로, 이동 전에
    카테고리 무관하게 항상 정리한다."""
    return re.sub(r'^\[SHANA\]\s*', '', filename, flags=re.IGNORECASE)

def _ensure_rule_updated_map(rule: Dict) -> None:
    """하위호환: 단일 updated → 요일별 updated_map으로 승격"""
    days = rule.get("days") or []
    # 새 구조 없으면 생성
    umap = rule.get("updated_map")
    if not isinstance(umap, dict):
        umap = {}
    # 기존 updated가 있으면 기본값으로 확산, 없으면 N
    base_u = rule.get("updated", "N")
    if base_u not in ("Y", "N"):
        base_u = "N"
    for d in days:
        if d in WEEKDAYS and d not in umap:
            umap[d] = base_u
    rule["updated_map"] = umap
    # 단일 updated 키는 남겨두되, 더 이상 로직에 사용하지 않음

# load_cfg, update_rule_fields, set_config_value are imported from db module

def tg_send(cfg: Dict, text: str) -> None:
    tg = cfg.get("telegram") or {}
    if not tg.get("enabled") or not tg.get("bot_token") or not tg.get("chat_id"): return
    try:
        requests.post(
            f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
            data={"chat_id": tg["chat_id"], "text": text},
            timeout=5,
        )
    except Exception as e:
        log(f"TELEGRAM ERROR: {e}")

def clean_source(root: Path, cleanup_cfg: Dict) -> None:
    for dname in cleanup_cfg.get("remove_dirs", []):
        for p in root.rglob(dname):
            if p.is_dir():
                try: shutil.rmtree(p); log(f"Removed dir: {p}")
                except Exception as e: log(f"Remove dir fail: {p} - {e}")
    for fname in cleanup_cfg.get("remove_files", []):
        for p in root.rglob(fname):
            if p.is_file():
                try: p.unlink(); log(f"Removed file: {p}")
                except Exception as e: log(f"Remove file fail: {p} - {e}")

def matches_any(filename: str, pattern_str: str) -> bool:
    """Check if filename matches any pattern separated by |."""
    if not pattern_str:
        return False
    return any(p.strip() in filename for p in pattern_str.split("|") if p.strip())

def find_matches(source_dir: Path, pattern: str, pattern_or: str = None, pattern2: str = None, pattern2_or: str = None, exclude_pattern: str = None) -> Iterable[Path]:
    """Find files matching the pattern conditions.

    Conditions:
    - group A: pattern AND pattern2 (pattern2 optional)
    - group B: pattern_or AND pattern2_or (both optional; group inactive if empty)
    - included: group A OR group B
    - excluded: exclude_pattern (must NOT match, applies regardless of group)

    Final match: (group A OR group B) AND NOT excluded
    """
    def and_group_match(filename: str, a: str, b: str) -> bool:
        if not a and not b:
            return False
        if a and not matches_any(filename, a):
            return False
        if b and not matches_any(filename, b):
            return False
        return True

    for p in source_dir.rglob("*"):
        if not p.is_file():
            continue

        INCOMPLETE_EXTENSIONS = {'.!qb', '.part', '.crdownload', '.tmp'}
        if p.suffix.lower() in INCOMPLETE_EXTENSIONS:
            continue

        filename = p.name

        group_a = and_group_match(filename, pattern, pattern2)
        group_b = and_group_match(filename, pattern_or, pattern2_or)
        if not (group_a or group_b):
            continue

        # Exclude pattern: Must NOT match, applies to the whole result
        if exclude_pattern and matches_any(filename, exclude_pattern):
            continue

        yield p

def parse_mode(val, default: int) -> int:
    if val is None: return default
    if isinstance(val, int): return val
    try: return int(str(val), 8)
    except Exception: return default

def resolve_ids(user: str, group: str) -> tuple[int, int]:
    uid = -1; gid = -1
    try: uid = pwd.getpwnam(user).pw_uid
    except KeyError: log(f"WARNING: user '{user}' not found")
    try: gid = grp.getgrnam(group).gr_gid
    except KeyError: log(f"WARNING: group '{group}' not found")
    return uid, gid

def apply_ownership_and_mode(path: Path, is_dir: bool,
                             uid: int, gid: int,
                             file_mode: int, dir_mode: int,
                             setgid_dirs: bool) -> None:
    try:
        if uid != -1 or gid != -1:
            os.chown(str(path), uid if uid != -1 else -1, gid if gid != -1 else -1)
    except Exception as e:
        log(f"CHOWN fail: {path} - {e}")
    try:
        mode = dir_mode if is_dir else file_mode
        os.chmod(str(path), mode)
        if is_dir and setgid_dirs:
            cur = os.stat(str(path)).st_mode
            os.chmod(str(path), cur | stat.S_ISGID)
    except Exception as e:
        log(f"CHMOD fail: {path} - {e}")

def syno_enforce_inherit(target: Path) -> None:
    tool = shutil.which("synoacltool")
    if tool:
        try: subprocess.run([tool, "-enforce-inherit", str(target)], check=False)
        except Exception as e: log(f"ACL enforce fail: {target} - {e}")

def apply_extra_acl(path: Path, acl_entries: List[Dict]) -> None:
    """추가 ACL 권한 설정 (setfacl 우선, 없으면 synoacltool 사용)"""
    if not acl_entries:
        return
    setfacl = shutil.which("setfacl")
    synoacl = shutil.which("synoacltool")
    for entry in acl_entries:
        t = entry.get("type", "user")   # user / group
        name = entry.get("name", "")
        perms = entry.get("permissions", "rw")  # r, w, x 조합
        if not name:
            continue
        try:
            if setfacl:
                flag = "u" if t == "user" else "g"
                subprocess.run([setfacl, "-m", f"{flag}:{name}:{perms}", str(path)], check=False)
            elif synoacl:
                r = "r" if "r" in perms else "-"
                w = "w" if "w" in perms else "-"
                x = "x" if "x" in perms else "-"
                # synoacltool 13자 권한 문자열: rwxpdDaARWcCo
                if w == "w":
                    perm_str = f"{r}{w}{x}p--aARWc--"  # read+write+traverse+append+attrs
                else:
                    perm_str = f"{r}-{x}---a-R-c--"    # read+traverse+read attrs
                subprocess.run([synoacl, "-add", str(path), f"{t}:{name}:allow:{perm_str}:fd--"], check=False)
        except Exception as e:
            log(f"ACL add fail: {path} ({name}) - {e}")

def ensure_dir_with_ownership(p: Path, uid: int, gid: int,
                              dir_mode: int, setgid_dirs: bool, do_acl: bool,
                              acl_entries: Optional[List[Dict]] = None) -> None:
    if not p.exists(): p.mkdir(parents=True, exist_ok=True)
    apply_ownership_and_mode(p, True, uid, gid, 0o664, dir_mode, setgid_dirs)
    if do_acl: syno_enforce_inherit(p)
    apply_extra_acl(p, acl_entries or [])

def safe_move_with_ownership(src: Path, dst_dir: Path,
                             uid: int, gid: int,
                             file_mode: int, dir_mode: int,
                             setgid_dirs: bool, do_acl: bool,
                             acl_entries: Optional[List[Dict]] = None) -> Path:
    ensure_dir_with_ownership(dst_dir, uid, gid, dir_mode, setgid_dirs, do_acl, acl_entries)
    dst = dst_dir / src.name
    if dst.exists():
        stem, suf, i = dst.stem, dst.suffix, 1
        while True:
            cand = dst_dir / f"{stem}_{i}{suf}"
            if not cand.exists():
                dst = cand; break
            i += 1
    shutil.move(str(src), str(dst))
    apply_ownership_and_mode(dst, False, uid, gid, file_mode, dir_mode, setgid_dirs)
    if do_acl: syno_enforce_inherit(dst)
    apply_extra_acl(dst, acl_entries or [])
    return dst

FFMPEG_BIN = "/bin/ffmpeg"  # Synology DSM에 이미 설치돼 있음(별도 설치/빌드 불필요)
_VIDEO_STREAM_RE = re.compile(
    r"Video:\s*(?P<codec>\S+?)(?:\s*\([^)]*\))*\s*,\s*(?P<pixfmt>[\w]+)(?:\([^)]*\))?\s*,\s*(?P<width>\d+)x(?P<height>\d+)"
)

def probe_video_stream(path: Path) -> Optional[Dict]:
    """ffmpeg -i 출력(stderr)에서 비디오 스트림 정보(코덱/해상도) 파싱.
    별도 ffprobe/패키지 설치 없이 DSM에 이미 있는 /bin/ffmpeg만 사용한다.
    ffmpeg -i는 출력파일을 안 주면 항상 exit code 1로 끝나므로 returncode는
    확인하지 않고 stderr 텍스트만 본다. 실패하면 None."""
    try:
        out = subprocess.run([FFMPEG_BIN, "-i", str(path)], capture_output=True, text=True, timeout=30)
        m = _VIDEO_STREAM_RE.search(out.stderr or "")
        if not m:
            log(f"ffmpeg probe: no video stream matched for {path}")
            return None
        return {"codec": m.group("codec"), "width": int(m.group("width")), "height": int(m.group("height"))}
    except Exception as e:
        log(f"ffmpeg probe error: {path} - {e}")
        return None

ENCODING_HOLD_DIR = Path("/volume1/homes/kck9010/Drive/인코딩")

# check_variety_encoding이 "720p 아님"을 보류 사유로 판단하더라도, 그 쇼의
# 규칙(rename_rules)에 설정된 "릴리즈 정보"에 이 토큰이 있으면 "이 쇼는
# 1080p를 허용한다"는 뜻으로 보고 예외로 둔다(강제 인코딩 안 함, 최종 폴더로
# 바로 이동). 파일명이 아니라 Web Admin에서 설정한 release 필드를 본다.
# 기존 라이브러리 파일과 비교하는 방식(최근 수정된 파일을 기준으로 삼는
# 방식)은 라이브러리에 아직 안 바뀐 구형 1080p 원본이 섞여있으면 정상 720p
# 신규 파일까지 오탐하는 문제가 있어 폐기함(SNL 코리아 리부트, 2026-10-08)
# — 이제는 비교 없이 파일 자신의 해상도 + 규칙의 release 설정으로 판단한다.
VARIETY_1080_EXEMPT_TOKEN = "1080"

def move_to_encoding_hold(cfg: Dict, f: Path, reason: str) -> Path | None:
    """f를 ENCODING_HOLD_DIR(Drive/인코딩)로 옮기고 텔레그램으로 알린다.
    성공하면 이동된 경로를, 실패하면 None을 돌려준다."""
    try:
        ENCODING_HOLD_DIR.mkdir(parents=True, exist_ok=True)
        dst = ENCODING_HOLD_DIR / f.name
        if dst.exists():
            stem, suf, i = dst.stem, dst.suffix, 1
            while True:
                cand = ENCODING_HOLD_DIR / f"{stem}_{i}{suf}"
                if not cand.exists():
                    dst = cand
                    break
                i += 1
        shutil.move(str(f), str(dst))
    except Exception as e:
        log(f"인코딩 보류 폴더로 이동 실패: {f} - {e}")
        return None

    log(f"ENCODING_HOLD: {f.name} ({reason}) -> {dst}")
    tg_send(cfg, f"⚠️ 인코딩 보류: {f.name}\n{reason}\n{ENCODING_HOLD_DIR}로 옮겨뒀어요. PC에서 재인코딩 후 Drive에 다시 넣어주세요.")
    return dst

def mark_original_in_hold_done(f: Path) -> None:
    """f(이번에 인코딩이 정상 확인되어 최종 폴더로 이동되는 파일)와 이름이
    같은(확장자 제외) 원본이 ENCODING_HOLD_DIR(Drive/인코딩)에 아직 남아있으면
    "[완료] " 접두사를 붙여, 재인코딩이 끝나서 더 이상 필요 없는 원본임을
    표시해둔다. 삭제는 하지 않음 — 확인 후 사용자가 직접 지운다."""
    try:
        if not ENCODING_HOLD_DIR.exists():
            return
        for cand in ENCODING_HOLD_DIR.iterdir():
            if not cand.is_file() or cand.name.startswith("[완료]"):
                continue
            if cand.stem == f.stem:
                new_name = f"[완료] {cand.name}"
                try:
                    cand.rename(cand.with_name(new_name))
                    log(f"ORIGINAL_MARKED_DONE: {cand.name} -> {new_name}")
                except Exception as e:
                    log(f"원본 완료 표시 실패: {cand} - {e}")
    except Exception as e:
        log(f"ENCODING_HOLD_DIR 스캔 실패: {e}")

def check_variety_encoding(cfg: Dict, f: Path, release: str = "") -> bool:
    """예능 파일 자신의 해상도만으로 판단한다(기존 라이브러리 파일과는
    비교하지 않음 - 2026-10-08 SNL 오탐 사고 이후 정책 변경).
    이미 720p면 통과, 아니면 이 쇼의 규칙에 설정된 release 문자열(Web
    Admin에서 입력, 파일명이 아님)에 "1080"이 포함돼 있지 않은 한
    ENCODING_HOLD_DIR(Drive/인코딩)로 보류하고 텔레그램으로 알린다.
    사용자가 PC에서 재인코딩해서 다시 Drive에 넣으면 drama_name.sh →
    media_router.py 파이프라인을 다시 타고 재검사된다(drama_name.sh는 이
    ENCODING_HOLD_DIR만 훑지 않도록 제외돼 있음).
    반환값이 True면 이미 이 함수 안에서 옮겼다는 뜻이니, 호출자는 추가로
    최종 폴더로 이동시키지 말고 건너뛰어야 한다."""
    info = probe_video_stream(f)
    if not info:
        return False  # 확인 불가 - 보수적으로 그냥 정상 이동시킴
    if info.get("height") == 720:
        # 가로폭은 정확히 1280이 아니어도(네이티브 720p 원본이 약간 다른
        # 폭을 가질 수 있음) 통과시킨다. PC 인코더 출력은 항상 정확히
        # 1280x720이라, 여기서 폭까지 엄격히 맞추면 폭만 살짝 다른 네이티브
        # 720p 원본은 PC에서 다시 인코딩해도 똑같이 "폭이 다름"이 되어
        # 영원히 통과를 못 하는 막다른 보류 루프가 될 수 있다.
        return False  # 이미 표준 해상도 - 정상 이동
    if VARIETY_1080_EXEMPT_TOKEN in (release or ""):
        return False  # 이 쇼의 release 설정이 1080 허용 - 강제 인코딩 예외

    reason = f"해상도 {info.get('width')}x{info.get('height')} (720p 아님)"
    return move_to_encoding_hold(cfg, f, reason) is not None

def reset_updated_for_today(cfg: Dict) -> bool:
    """오늘 날짜의 첫 실행에서 오늘 요일만 N으로 리셋 (요일별 독립 저장).

    캐치업: 예전에는 00:00~01:00 사이에 실행될 때만 리셋했는데, 재부팅/스케줄러
    장애 등으로 그 시간대에 배치가 한 번도 못 돌면 해당 요일 리셋이 통째로
    스킵되고 다음 주 같은 요일까지 복구되지 않는 문제가 있었다.
    이제는 '마지막으로 리셋한 날짜'(last_reset_date)를 cfg에 저장해두고,
    오늘 날짜로 아직 리셋을 안 했다면 실행 시각과 무관하게(=지연 캐치업)
    첫 실행에서 리셋한다. 같은 날 여러 번 실행돼도 한 번만 리셋되므로
    사용자가 이미 체크(Y)한 항목을 낮에 다시 N으로 되돌리는 일은 없다.

    이 함수는 스스로 targeted write를 수행한다(호출자가 별도로 전체 저장할
    필요 없음) — 레이스 컨디션 방지를 위해 db.update_rule_fields()로 건드린
    규칙만 개별 UPDATE하고, 전체 규칙 목록을 스냅샷 교체하지 않는다.
    """
    now = current_localtime()
    today_date = now.date().isoformat()
    last = cfg.get("last_reset_date")

    if last == today_date:
        return False  # 오늘 이미 리셋 완료

    today = WEEKDAYS[now.weekday()]
    if last is not None:
        # 정상 케이스(과거에도 리셋 이력이 있음): 날짜가 바뀌었으니 오늘 요일 리셋
        for r in cfg.get("rules", []):
            days = r.get("days") or []
            if today in days:
                _ensure_rule_updated_map(r)
                umap_changed = r["updated_map"].get(today) != "N"
                # 하루 여러 편 드라마의 요일별 카운터도 오늘 요일만 0으로
                prog = _get_day_progress(r)
                prog_changed = prog.get(today, 0) != 0
                if not (umap_changed or prog_changed):
                    continue
                patch = {}
                if umap_changed:
                    r["updated_map"][today] = "N"
                    patch["updated_map"] = r["updated_map"]
                if prog_changed:
                    prog[today] = 0
                    r["day_progress"] = prog
                    patch["day_progress"] = prog
                update_rule_fields(r["id"], **patch)
    # last가 None이면(last_reset_date 필드가 처음 생기는 마이그레이션 시점) 기존
    # updated_map을 건드리지 않고 날짜만 기록해, 배포 당일 이미 체크한 항목이
    # 실수로 되돌아가지 않게 한다. 다음 날부터는 정상적으로 캐치업 동작한다.
    cfg["last_reset_date"] = today_date
    set_config_value("last_reset_date", today_date)
    return True

def choose_mark_day_by_order(rule: Dict) -> str | None:
    """규칙의 days 순서대로 마킹할 요일 결정.
    - days가 1개면 그 요일
    - days가 2개 이상이면 앞에서부터 아직 Y가 아닌 첫 요일
    - 모두 Y면 days[0]

    에피소드 번호를 추적하는 드라마는 choose_mark_days_by_episode()를 우선
    사용한다. 이 함수는 에피소드 번호를 못 뽑는 규칙(예능/다큐/애니, 또는
    파일명에서 화수를 못 읽는 드라마)의 폴백이다.
    """
    days = (rule.get("days") or [])[:]
    if not days:
        return None
    umap = rule.get("updated_map") or {}
    if len(days) == 1:
        return days[0]
    for d in days:
        if umap.get(d, "N") != "Y":
            return d
    return days[0]


def _episodes_per_day(rule: Dict) -> int:
    try:
        p = int(rule.get("episodes_per_day") or 1)
    except (TypeError, ValueError):
        p = 1
    return p if p >= 1 else 1


def _get_day_progress(rule: Dict) -> Dict[str, int]:
    prog = rule.get("day_progress")
    if not isinstance(prog, dict):
        return {}
    out: Dict[str, int] = {}
    for k, v in prog.items():
        try:
            out[k] = int(v)
        except (TypeError, ValueError):
            out[k] = 0
    return out


def mark_rule_updated_map(rule: Dict, new_episodes: int = 0) -> None:
    """규칙 하나의 updated_map을 Y로 갱신하고 DB에 개별 저장한다.

    '하루 편수'(episodes_per_day, P)가 2 이상인 드라마는 요일별 카운터
    (day_progress)에 이번에 새로 받은 편수를 days 순서대로 채운다. 한 요일이
    P편을 다 채워야 그 요일이 Y가 되고, 그 전에는 다음 방영일로 넘어가지
    않는다. 예: 신병(수/목 각 2편) → 수요일분 2편을 받으면 '수'만 Y, '목'은
    목요일분 2편이 실제로 들어와야 Y. day_progress는 주간 리셋 때 그 요일만
    0으로 돌아간다(reset_updated_for_today).

    그 외(P<=1, 예능/다큐/애니, 또는 새 에피소드 없이 호출된 경우)는 기존
    choose_mark_day_by_order() 폴백을 쓴다. 어느 경우든 기존 Y를 N으로
    되돌리지는 않는다(주간 리셋의 역할).
    """
    _ensure_rule_updated_map(rule)
    umap = rule["updated_map"]
    days = rule.get("days") or []
    p = _episodes_per_day(rule)

    if (rule.get("category") or "").strip() == "드라마" and p > 1 and days and new_episodes > 0:
        prog = _get_day_progress(rule)
        remaining = new_episodes
        prog_changed = False
        umap_changed = False
        for d in days:
            if remaining <= 0:
                break
            cur = prog.get(d, 0)
            if cur >= p:
                continue  # 이번 주 이 요일 몫은 이미 참
            add = min(remaining, p - cur)
            cur += add
            remaining -= add
            prog[d] = cur
            prog_changed = True
            if cur >= p and umap.get(d) != "Y":
                umap[d] = "Y"
                umap_changed = True
        # remaining > 0 (모든 요일이 다 참): 다음 주 몫이 미리 온 것. received에
        # 이미 기록돼 있으므로 여기서 별도로 이월하지 않는다.
        if prog_changed:
            rule["day_progress"] = prog
            update_rule_fields(rule["id"], day_progress=prog, updated_map=umap)
        elif umap_changed:
            update_rule_fields(rule["id"], updated_map=umap)
        return

    # 하루 편수>1 드라마인데 새 에피소드 수를 못 셌으면(파일명에서 화수 못 읽음
    # 등) 아무것도 마킹하지 않는다 — 잘못 넘어가느니 리셋 상태(N)를 유지.
    if (rule.get("category") or "").strip() == "드라마" and p > 1:
        return

    mark_day = choose_mark_day_by_order(rule)
    if mark_day and umap.get(mark_day) != "Y":
        umap[mark_day] = "Y"
        update_rule_fields(rule["id"], updated_map=umap)

def main() -> None:
    cfg = load_cfg()
    reset_updated_for_today(cfg)  # 필요 시 스스로 targeted write 수행 (전체 스냅샷 저장 없음)

    sources = [Path(s) for s in (cfg.get("paths") or {}).get("sources", [])]
    cleanup_cfg = cfg["paths"].get("cleanup", {})
    base_paths = cfg.get("base_paths", {})
    rules = cfg.get("rules", [])

    own = cfg["ownership"]
    apply_own = own.get("apply", True)
    uid, gid = resolve_ids(own["user"], own["group"]) if apply_own else (-1, -1)
    file_mode = parse_mode(own["file_mode"], 0o664)
    dir_mode = parse_mode(own["dir_mode"], 0o775)
    setgid_dirs = own.get("setgid_dirs", True)
    do_acl = own.get("enforce_inherit", True)
    acl_entries = own.get("extra_acl") or []

    moved_count = 0
    today = WEEKDAYS[current_localtime().weekday()]

    for src_root in sources:
        if not src_root.exists():
            log(f"Skip missing source: {src_root}")
            continue
        clean_source(src_root, cleanup_cfg)

        for r in rules:
            category = (r.get("category") or "").strip()
            pattern  = (r.get("pattern")  or "").strip()
            pattern_or = (r.get("pattern_or") or "").strip() or None
            pattern2 = (r.get("pattern2") or "").strip() or None
            pattern2_or = (r.get("pattern2_or") or "").strip() or None
            exclude_pattern = (r.get("exclude_pattern") or "").strip() or None
            sub      = (r.get("subfolder") or "").strip()
            if not (category and pattern and sub):
                continue

            base_dir = base_paths.get(category)
            if not base_dir:
                continue
            target_dir = Path(base_dir) / sub

            for f in find_matches(src_root, pattern, pattern_or, pattern2, pattern2_or, exclude_pattern):
                try:
                    try:
                        f.relative_to(target_dir)
                        continue
                    except ValueError:
                        pass

                    # ShanaEncoder로 재인코딩된 파일의 "[SHANA]" 접두사 제거
                    stripped = strip_encoder_tag(f.name)
                    if stripped != f.name:
                        new_src = f.with_name(stripped)
                        try:
                            f.rename(new_src)
                            log(f"RENAMED_STRIP_TAG: {f.name} -> {stripped}")
                            f = new_src
                        except Exception as e:
                            log(f"Rename fail: {f} -> {new_src} ({e})")

                    # 예능: 동일 파일명이 이미 있으면 둘 중 하나만 남긴다.
                    # 해상도를 먼저 본다(720p인 쪽이 "인코딩 완료된 올바른 쪽"이라는
                    # 뜻이므로 우선) - 파일 크기만 보면 "작은 쪽 = 인코딩됨"이라는
                    # 우연한 상관관계에 의존하게 되어, 가끔 짧은 특집편처럼 원래도
                    # 작은 1080p 원본이 끼면 잘못 판단할 수 있다. 둘 다 720p거나
                    # 둘 다 아니거나 해상도 확인이 안 되면 기존처럼 크기로 판단.
                    skip_move_keep_existing = False
                    if category == "예능":
                        existing_dst = target_dir / f.name
                        if existing_dst.exists() and existing_dst.is_file():
                            try:
                                existing_info = probe_video_stream(existing_dst)
                                new_info = probe_video_stream(f)
                                existing_is_720 = bool(existing_info) and existing_info.get("height") == 720
                                new_is_720 = bool(new_info) and new_info.get("height") == 720

                                if new_is_720 and not existing_is_720:
                                    existing_dst.unlink()
                                    log(f"DUPLICATE_720P_KEPT: kept incoming {f}, removed {existing_dst} (720p 아님)")
                                elif existing_is_720 and not new_is_720:
                                    f.unlink()
                                    skip_move_keep_existing = True
                                    log(f"DUPLICATE_720P_KEPT: kept {existing_dst}, removed {f} (720p 아님)")
                                else:
                                    src_size = f.stat().st_size
                                    dst_size = existing_dst.stat().st_size
                                    if src_size > dst_size:
                                        # 들어온 파일이 더 크면 들어온 파일 삭제
                                        f.unlink()
                                        skip_move_keep_existing = True
                                        log(f"DUPLICATE_SMALLER_KEPT: kept {existing_dst} ({dst_size}B), removed {f} ({src_size}B)")
                                    elif src_size < dst_size:
                                        # 기존 파일이 더 크면 기존 파일 삭제 후 들어온 파일 이동
                                        existing_dst.unlink()
                                        log(f"DUPLICATE_SMALLER_KEPT: kept incoming {f} ({src_size}B), removed {existing_dst} ({dst_size}B)")
                                    else:
                                        # 크기가 같으면 기존 파일 유지, 들어온 파일 삭제
                                        f.unlink()
                                        skip_move_keep_existing = True
                                        log(f"DUPLICATE_SAME_SIZE: kept {existing_dst}, removed {f}")
                            except Exception as e:
                                log(f"Duplicate compare fail: {f} vs {existing_dst} ({e})")

                    if skip_move_keep_existing:
                        tg_send(cfg, f"중복 정리: {f.name} (더 작은 기존 파일 유지)")
                        mark_rule_updated_map(r)
                        continue

                    # 드라마 파일명에 .END 같은 꼬리 토큰이 있으면 Plex 스캔 호환 형태로 정리
                    if category == "드라마":
                        sanitized = sanitize_filename_for_plex(f.name)
                        if sanitized != f.name:
                            new_src = f.with_name(sanitized)
                            try:
                                f.rename(new_src)
                                log(f"RENAMED_FOR_PLEX: {f.name} -> {sanitized}")
                                f = new_src
                            except Exception as e:
                                log(f"Rename fail: {f} -> {new_src} ({e})")

                    # 예능: 720p가 아니면(이 쇼의 release 설정에 1080 허용이
                    # 없는 한) 최종 폴더로 옮기지 않고 보류 폴더(Drive/인코딩)로
                    # 옮긴 뒤 다음 파일로 넘어간다. 예전엔 "불꽃야구"를 해상도
                    # 확인 없이 무조건 보류시키는 별도 규칙(FORCE_ENCODE_SHOWS)이
                    # 있었는데, 그게 이미 720p로 재인코딩되어 돌아온 완성본까지
                    # 다시 붙잡아 무한루프를 만들었다(2026-10-10 실제 발생).
                    # check_variety_encoding이 이제 해상도를 직접 보니 그 규칙은
                    # 필요 없어져서 제거함.
                    if category == "예능":
                        try:
                            if check_variety_encoding(cfg, f, r.get("release", "")):
                                continue
                            else:
                                mark_original_in_hold_done(f)
                        except Exception as e:
                            log(f"Encoding check fail: {f} - {e}")

                    if apply_own:
                        dst = safe_move_with_ownership(f, target_dir, uid, gid, file_mode, dir_mode, setgid_dirs, do_acl, acl_entries)
                    else:
                        target_dir.mkdir(parents=True, exist_ok=True)
                        dst = target_dir / f.name
                        if dst.exists():
                            stem, suf, i = dst.stem, dst.suffix, 1
                            while True:
                                cand = target_dir / f"{stem}_{i}{suf}"
                                if not cand.exists():
                                    dst = cand; break
                                i += 1
                        shutil.move(str(f), str(dst))

                    moved_count += 1
                    log(f"MOVED: {f} -> {dst}")
                    tg_send(cfg, f"이동: {f.name} → {target_dir}")

                    # 자막(.srt 등) 같은 비디오가 아닌 파일은 "완료" 체크를 건드리지
                    # 않는다 - 영상은 인코딩 보류로 빠지고 자막만 먼저 최종 폴더에
                    # 도착하는 경우, 자막만 보고 체크모드에서 "완료"로 잘못 표시되던
                    # 문제가 있었다(2026-10-13, 불꽃야구에서 실제 발생).
                    if f.suffix.lower() in VIDEO_EXTENSIONS:
                        # 드라마인 경우 에피소드 번호 추출 및 저장
                        new_episodes = 0
                        if category == "드라마":
                            episode_num = extract_episode_number(f.name)
                            if episode_num is not None:
                                received = r.get("received_episodes") or []
                                if not isinstance(received, list):
                                    received = []
                                if episode_num not in received:
                                    received.append(episode_num)
                                    received.sort()
                                    r["received_episodes"] = received
                                    update_rule_fields(r["id"], received_episodes=received)
                                    log(f"Episode {episode_num} recorded for {sub}")
                                    new_episodes = 1

                        # 요일별 Y 마킹: 드라마(하루 편수>1)는 요일별 카운터로 채우고,
                        # 그 외에는 days 순서 폴백
                        mark_rule_updated_map(r, new_episodes=new_episodes)

                except Exception as e:
                    log(f"Move fail: {f} -> {target_dir} ({e})")

    # 남은 비디오 파일 검사 및 텔레그램 전송
    remaining_videos = []
    for src_root in sources:
        if not src_root.exists():
            continue
        for video_file in src_root.rglob("*"):
            if video_file.is_file() and video_file.suffix.lower() in VIDEO_EXTENSIONS:
                remaining_videos.append(video_file)
    
    if remaining_videos:
        if len(remaining_videos) <= 20:
            msg_lines = [f"다운로드 폴더에 남은 동영상 파일 ({len(remaining_videos)}개):"] + [f"• {f.name}" for f in remaining_videos]
        else:
            msg_lines = [f"다운로드 폴더에 남은 동영상 파일 ({len(remaining_videos)}개):"]
            for f in remaining_videos[:10]:
                msg_lines.append(f"• {f.name}")
            msg_lines.append(f"... 외 {len(remaining_videos) - 10}개")
        tg_send(cfg, "\n".join(msg_lines))
        log(f"Found {len(remaining_videos)} remaining video files in source folders")
    
    log(f"Done. moved={moved_count}")

if __name__ == "__main__":
    try:
        import requests  # noqa
    except Exception:
        print("pip install -r requirements.txt 필요", file=sys.stderr)
    main()
