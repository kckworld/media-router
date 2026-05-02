# -*- coding: utf-8 -*-
from __future__ import annotations
import os, sys, time, shutil, fnmatch, subprocess, stat, pwd, grp, re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Iterable, Optional
from zoneinfo import ZoneInfo
import requests
from db import load_cfg, save_cfg_atomic, DATA_DIR

BASE: Path = Path(__file__).resolve().parent
LOGDIR: Path = DATA_DIR / "logs"
LOGDIR.mkdir(parents=True, exist_ok=True)
LOGFILE: Path = LOGDIR / "media_router.log"

WEEKDAYS: List[str] = ["월", "화", "수", "목", "금", "토", "일"]
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Seoul"))


def current_localtime() -> datetime:
    return datetime.now(APP_TZ)

def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with LOGFILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def normalize_pattern(p: str) -> str:
    if not p: return p
    p = p.strip().strip('"').strip("'")
    has_wild = ('*' in p) or ('?' in p)
    has_ext_dot = '.' in p
    if not has_wild:
        return f"*{p}*.*"
    if not has_ext_dot and not p.endswith(".*"):
        return p + ".*"
    return p

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

# load_cfg and save_cfg_atomic are imported from db module

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

def find_matches(source_dir: Path, pattern: str) -> Iterable[Path]:
    for p in source_dir.rglob("*"):
        if p.is_file() and fnmatch.fnmatch(p.name, pattern):
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
                perm_str = f"{r}{w}{x}pdDaARWcCo" if w == "w" else f"{r}-{x}---aAR-c--"
                subprocess.run([synoacl, "-add", str(path), f"{t}:{name}:allow:{perm_str}:---n"], check=False)
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
    """자정~1시: 오늘 요일만 N으로 리셋 (요일별 독립 저장)"""
    now = current_localtime()
    if 0 <= now.hour < 1:
        today = WEEKDAYS[now.weekday()]
        changed = False
        for r in cfg.get("rules", []):
            days = r.get("days") or []
            if today in days:
                _ensure_rule_updated_map(r)
                if r["updated_map"].get(today) != "N":
                    r["updated_map"][today] = "N"
                    changed = True
        return changed
    return False

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
    if reset_updated_for_today(cfg):
        save_cfg_atomic(cfg)

    sources = [Path(s) for s in (cfg.get("paths") or {}).get("sources", [])]
    cleanup_cfg = cfg["paths"].get("cleanup", {})
    base_paths = cfg.get("base_paths", {})
    rules = cfg.get("rules", [])

    own = cfg["ownership"]
    apply_own = own.get("apply", True)
    uid, gid = resolve_ids(own["user"], own["group"])
    file_mode = parse_mode(own["file_mode"], 0o664)
    dir_mode = parse_mode(own["dir_mode"], 0o775)
    setgid_dirs = own.get("setgid_dirs", True)
    do_acl = own.get("enforce_inherit", True)
    acl_entries = own.get("extra_acl") or []

    moved_count, changed = 0, False
    today = WEEKDAYS[current_localtime().weekday()]

    for src_root in sources:
        if not src_root.exists():
            log(f"Skip missing source: {src_root}")
            continue
        clean_source(src_root, cleanup_cfg)

        for r in rules:
            category = (r.get("category") or "").strip()
            pattern  = (r.get("pattern")  or "").strip()
            sub      = (r.get("subfolder") or "").strip()
            if not (category and pattern and sub):
                continue

            base_dir = base_paths.get(category)
            if not base_dir:
                continue
            target_dir = Path(base_dir) / sub

            for f in find_matches(src_root, pattern):
                try:
                    try:
                        f.relative_to(target_dir)
                        continue
                    except ValueError:
                        pass

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
                                changed = True
                                log(f"Episode {episode_num} recorded for {sub}")

                    # 요일별 Y 마킹: days 순서만 따름 (한 개면 그 요일, 여러 개면 앞에서부터 미처리 우선)
                    _ensure_rule_updated_map(r)
                    mark_day = choose_mark_day_by_order(r)
                    if mark_day and r["updated_map"].get(mark_day) != "Y":
                        r["updated_map"][mark_day] = "Y"
                        changed = True

                except Exception as e:
                    log(f"Move fail: {f} -> {target_dir} ({e})")

    if changed:
        save_cfg_atomic(cfg)
    log(f"Done. moved={moved_count}")

if __name__ == "__main__":
    try:
        import requests  # noqa
    except Exception:
        print("pip install -r requirements.txt 필요", file=sys.stderr)
    main()
