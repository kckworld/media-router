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
                if r["updated_map"].get(today) != "N":
                    r["updated_map"][today] = "N"
                    update_rule_fields(r["id"], updated_map=r["updated_map"])
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

                    # 예능: 동일 파일명이 이미 있으면 작은 파일만 남긴다.
                    skip_move_keep_existing = False
                    if category == "예능":
                        existing_dst = target_dir / f.name
                        if existing_dst.exists() and existing_dst.is_file():
                            try:
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
                                log(f"Duplicate size-compare fail: {f} vs {existing_dst} ({e})")

                    if skip_move_keep_existing:
                        tg_send(cfg, f"중복 정리: {f.name} (더 작은 기존 파일 유지)")
                        _ensure_rule_updated_map(r)
                        mark_day = choose_mark_day_by_order(r)
                        if mark_day and r["updated_map"].get(mark_day) != "Y":
                            r["updated_map"][mark_day] = "Y"
                            update_rule_fields(r["id"], updated_map=r["updated_map"])
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

                    # 드라마인 경우 에피소드 번호 추출 및 저장
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

                    # 요일별 Y 마킹: days 순서만 따름 (한 개면 그 요일, 여러 개면 앞에서부터 미처리 우선)
                    _ensure_rule_updated_map(r)
                    mark_day = choose_mark_day_by_order(r)
                    if mark_day and r["updated_map"].get(mark_day) != "Y":
                        r["updated_map"][mark_day] = "Y"
                        update_rule_fields(r["id"], updated_map=r["updated_map"])

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
