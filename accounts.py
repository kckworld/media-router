# -*- coding: utf-8 -*-
"""MR 계정 (2026-11-04 사용자 요청: 설정 > 계정·메뉴 관리, 계정별 권한).

- 소유자(owner): 지금 관리자. 비밀번호는 예전처럼 MEDIA_ADMIN_PASSWORD(환경 변수). 지울 수도 막을 수도 없고 권한 고정.
- 일반 계정: data/accounts.json(600)에 비밀번호 **해시만**(werkzeug scrypt). 아이디는 영문 소문자·숫자·_ 2~20자.
  예전 보기 전용 비밀번호(MEDIA_VIEWER_PASSWORD)는 처음 한 번 'viewer' 계정으로 옮긴다(보던 메뉴 그대로 읽기 전용).
- 바뀔 때마다 data/accounts_audit.jsonl에 누가·언제·무엇을(비밀번호 값은 절대 안 남김).
- perms(메뉴별 권한 none/view/edit)는 2단계(서버 강제)·3단계(메뉴 관리 화면)에서 쓴다. 비어 있으면 예전 보기 전용과 같게 본다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

OWNER = "owner"
ID_RE = re.compile(r"^[a-z0-9_]{2,20}$")
MIN_PW = 8
_lock = threading.Lock()
_cache = {"path": None, "mtime": None, "data": None}


def _empty() -> dict:
    return {"version": 1, "accounts": {}}


def load(path: Path) -> dict:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return _empty()
    with _lock:
        if _cache["path"] != str(path) or _cache["mtime"] != mtime:
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(d, dict) or not isinstance(d.get("accounts"), dict):
                    d = _empty()
            except (OSError, ValueError):
                d = _empty()
            _cache.update(path=str(path), mtime=mtime, data=d)
        return json.loads(json.dumps(_cache["data"]))      # 복사본(호출한 쪽이 고쳐도 캐시는 그대로)


def _save(path: Path, data: dict) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    with _lock:
        _cache.update(mtime=None)


def audit(path: Path, by: str, action: str, target: str, detail: str = "") -> None:
    """바뀐 기록 한 줄(비밀번호는 절대 안 넣음)"""
    log = path.with_name("accounts_audit.jsonl")
    line = json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by, "action": action, "target": target,
                       "detail": detail}, ensure_ascii=False)
    try:
        new = not log.exists()
        with log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if new:
            os.chmod(log, 0o600)
    except OSError:
        pass


def migrate_viewer(path: Path, viewer_password: str) -> bool:
    """예전 보기 전용 비밀번호를 'viewer' 계정으로 한 번만 옮긴다(이미 있거나 옮긴 적 있으면 안 함)"""
    if not viewer_password:
        return False
    data = load(path)
    if data.get("viewer_migrated") or "viewer" in data["accounts"]:
        return False
    data["accounts"]["viewer"] = {"name": "보기 전용", "hash": generate_password_hash(viewer_password), "disabled": False,
                                  "perms": {}, "legacy_viewer": True, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    data["viewer_migrated"] = True
    _save(path, data)
    audit(path, "system", "migrate_viewer", "viewer", "예전 보기 전용 비밀번호를 계정으로 옮김")
    return True


def get(path: Path, uid: str) -> dict | None:
    a = load(path)["accounts"].get(uid)
    return a if isinstance(a, dict) else None


def active(path: Path, uid: str) -> bool:
    a = get(path, uid)
    return bool(a) and not a.get("disabled")


def verify(path: Path, uid: str, password: str) -> bool:
    a = get(path, uid)
    if not a or a.get("disabled") or not password:
        return False
    try:
        return check_password_hash(a.get("hash", ""), password)
    except (ValueError, TypeError):
        return False


def listing(path: Path) -> list[dict]:
    """화면용(해시 빼고)"""
    out = []
    for uid, a in sorted(load(path)["accounts"].items()):
        out.append({"id": uid, "name": a.get("name", ""), "disabled": bool(a.get("disabled")),
                    "created": a.get("created", ""), "changed": a.get("changed", ""), "perms": a.get("perms") or {},
                    "legacy_viewer": bool(a.get("legacy_viewer"))})
    return out


def _check_pw(pw: str) -> str | None:
    if not pw or len(pw) < MIN_PW:
        return f"비밀번호는 {MIN_PW}자 이상으로 해 주세요."
    if len(pw) > 128:
        return "비밀번호가 너무 길어요."
    return None


def add(path: Path, uid: str, name: str, pw: str, by: str = OWNER) -> str | None:
    uid = (uid or "").strip().lower()
    if not ID_RE.match(uid) or uid == OWNER:
        return "아이디는 영문 소문자·숫자·_ 2~20자로(owner는 못 써요)."
    err = _check_pw(pw)
    if err:
        return err
    data = load(path)
    if uid in data["accounts"]:
        return "이미 있는 아이디예요."
    data["accounts"][uid] = {"name": (name or "").strip()[:30] or uid, "hash": generate_password_hash(pw),
                             "disabled": False, "perms": {}, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save(path, data)
    audit(path, by, "add", uid)
    return None


def _change(path: Path, uid: str, by: str, action: str, fn, detail: str = "") -> str | None:
    if uid == OWNER:
        return "소유자 계정은 여기서 바꿀 수 없어요."
    data = load(path)
    a = data["accounts"].get(uid)
    if not a:
        return "없는 계정이에요."
    fn(a)
    a["changed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(path, data)
    audit(path, by, action, uid, detail)
    return None


def set_password(path: Path, uid: str, pw: str, by: str = OWNER) -> str | None:
    err = _check_pw(pw)
    if err:
        return err
    return _change(path, uid, by, "password", lambda a: a.update(hash=generate_password_hash(pw)))


def set_disabled(path: Path, uid: str, disabled: bool, by: str = OWNER) -> str | None:
    return _change(path, uid, by, "disable" if disabled else "enable", lambda a: a.update(disabled=bool(disabled)))


def set_name(path: Path, uid: str, name: str, by: str = OWNER) -> str | None:
    return _change(path, uid, by, "rename", lambda a: a.update(name=(name or "").strip()[:30] or uid))


def set_perms(path: Path, uid: str, perms: dict, by: str = OWNER) -> str | None:
    clean = {str(k): v for k, v in (perms or {}).items() if v in ("none", "view", "edit")}
    return _change(path, uid, by, "perms", lambda a: a.update(perms=clean), detail=json.dumps(clean, ensure_ascii=False))


def delete(path: Path, uid: str, by: str = OWNER) -> str | None:
    if uid == OWNER:
        return "소유자 계정은 지울 수 없어요."
    data = load(path)
    if uid not in data["accounts"]:
        return "없는 계정이에요."
    del data["accounts"][uid]
    # 세대 번호를 올려 남겨 둔다: 같은 아이디로 다시 만들어도 지우기 전 세션은 무효(인사팀 점검 11-04)
    data.setdefault("gen", {})[uid] = int(data.get("gen", {}).get(uid, 0)) + 1
    _save(path, data)
    audit(path, by, "delete", uid)
    return None


# ── 세션 세대 번호 (11-04 인사팀 점검): 세션 토큰에 로그인 때의 세대 번호(g)를 넣고, 지금 번호와 다르면 무효 ──
def gen(path: Path, uid: str) -> int:
    return int(load(path).get("gen", {}).get(uid, 0))


def bump_all(path: Path, by: str = OWNER) -> None:
    """[모든 기기 로그아웃]: 소유자 포함 모든 계정의 세대 번호를 올린다 → 지금 있는 세션 전부 무효"""
    data = load(path)
    g = data.setdefault("gen", {})
    for uid in set(list(data["accounts"]) + [OWNER] + list(g)):
        g[uid] = int(g.get(uid, 0)) + 1
    _save(path, data)
    audit(path, by, "logout_all", "*")


# ── 로그인 기록 (11-04 인사팀 점검): 성공·실패·계정·시각·IP 끝자리·브라우저 요약, 최근 200건, 600 ──
LOGIN_LOG_MAX = 200


def ua_summary(ua: str) -> str:
    ua = ua or ""
    dev = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua
           else "Windows" if "Windows" in ua else "Mac" if "Macintosh" in ua else "Linux" if "Linux" in ua else "?")
    br = ("Edge" if "Edg/" in ua else "Samsung" if "SamsungBrowser" in ua else "Chrome" if "Chrome/" in ua
          else "Firefox" if "Firefox/" in ua else "Safari" if "Safari/" in ua else "curl" if ua.startswith("curl") else "?")
    return f"{dev} · {br}"


def log_login(path: Path, ok: bool, user: str, ip: str, ua: str, note: str = "") -> None:
    log = path.with_name("login_log.json")
    try:
        items = json.loads(log.read_text(encoding="utf-8"))
        items = items if isinstance(items, list) else []
    except (OSError, ValueError):
        items = []
    tail = re.split(r"[.:]", ip or "")[-1] if ip else ""
    items.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": bool(ok), "user": (user or "")[:20],
                  "ip": f"…{tail}" if tail else "?", "ua": ua_summary(ua), "note": note[:40]})
    tmp = log.with_name("." + log.name + ".tmp")
    tmp.write_text(json.dumps(items[-LOGIN_LOG_MAX:], ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, log)


def login_log(path: Path) -> list[dict]:
    try:
        items = json.loads(path.with_name("login_log.json").read_text(encoding="utf-8"))
        return list(reversed(items)) if isinstance(items, list) else []
    except (OSError, ValueError):
        return []
