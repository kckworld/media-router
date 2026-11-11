# -*- coding: utf-8 -*-
"""알림 허브 (2026-11-10 사용자 요청): 알림성 메시지를 MR 한 곳에서 켜고 끄고, 채널·봇·카톡 방을 고르고, 보낸 기록을 본다.

대상(사용자 지정 11-10): MR 요청 영상 완료 안내, Home Assistant 알림 3개, n8n 알림 6개.
텔레그램으로 명령을 받는 기능(봇 메뉴·버튼·명령 응답, 매시 정각 알림)은 대상이 아니다.

- 설정 data/notify_hub.json (600): {"bots": {id: {name, token, username}}, "rooms": [카톡 방], "items": {key: {...}}}
  봇 "mr"은 토큰을 따로 두지 않고 MR 텔레그램 설정(load_cfg)의 토큰을 그때그때 쓴다.
  토큰은 화면·API·로그 어디에도 내보내지 않는다(끝 4자리만).
- 기록 data/notify_log.sqlite3: 보낸 시각·알림·채널·받는 곳·내용·사진 주소·성공 여부·오류. 180일 지나면 지운다.
- 보내기는 dispatch(): 꺼져 있으면 '꺼짐'으로 기록만. 채널: telegram / kakao / both / kakao_tg(카톡 실패 시 텔레그램).
  실제 전송은 web_admin이 넘겨주는 tg_send(token, chat_id, text, photo, parse_mode)·kakao_send(room, poster, text) 함수로 한다.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
CHANNELS = ("telegram", "kakao", "both", "kakao_tg")
CHANNEL_LABELS = {"telegram": "텔레그램", "kakao": "카톡", "both": "둘 다", "kakao_tg": "카톡(실패 시 텔레그램)"}
LOG_KEEP_DAYS = 180
TEXT_MAX = 4000
_lock = threading.Lock()
_kakao_lock = threading.Lock()       # 카톡 화면 조작은 한 번에 하나

# 처음 설정(지금 보내는 곳 그대로, 11-10 조사). key는 HA·n8n이 부르는 이름이라 바꾸지 않는다.
DEFAULT_ITEMS = {
    "mr.request_done": {"name": "요청 영상 완료 안내(포스터+글)", "source": "MR", "channel": "both", "bot": "mr",
                        "chat_id": "", "kakao_room": "건이네가족"},
    "ha.power_report": {"name": "매일 아침 전기 리포트", "source": "HA", "channel": "telegram", "bot": "synology_kck",
                        "chat_id": "", "kakao_room": ""},
    "ha.vacuum_map": {"name": "청소 완료 지도", "source": "HA", "channel": "telegram", "bot": "synology_kck",
                      "chat_id": "", "kakao_room": ""},
    "ha.printer_toner": {"name": "프린터 토너 부족", "source": "HA", "channel": "telegram", "bot": "synology_kck",
                         "chat_id": "", "kakao_room": ""},
    "n8n.dongtan_weather": {"name": "동탄1동 날씨(06·18시)", "source": "n8n", "channel": "kakao_tg", "bot": "synology_kck",
                            "chat_id": "", "kakao_room": "건이네가족"},
    "n8n.weather_bot": {"name": "날씨봇 매일 06시(구독자별)", "source": "n8n", "channel": "telegram", "bot": "weather",
                        "chat_id": "", "kakao_room": "", "caller_targets": True},
    "n8n.weather_admin": {"name": "날씨봇 지역 등록 관리자 알림", "source": "n8n", "channel": "telegram", "bot": "telegram_account",
                          "chat_id": "", "kakao_room": ""},
    "n8n.naver_blog": {"name": "네이버 블로그 새 글", "source": "n8n", "channel": "telegram", "bot": "telegram_account",
                       "chat_id": "", "kakao_room": ""},
    "n8n.ohtani": {"name": "오타니 선발 알림", "source": "n8n", "channel": "telegram", "bot": "telegram_account",
                   "chat_id": "", "kakao_room": ""},
    "n8n.error": {"name": "n8n 에러 알림", "source": "n8n", "channel": "telegram", "bot": "telegram_account",
                  "chat_id": "", "kakao_room": ""},
}
_KEY_RE = re.compile(r"^[a-z0-9_.-]{3,60}$")
_BOT_RE = re.compile(r"^[a-z0-9_]{2,30}$")
_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
_CHAT_RE = re.compile(r"^-?\d{3,20}$|^@[A-Za-z0-9_]{4,40}$")


def now_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


# ───────────────────────────── 설정 ─────────────────────────────
def load(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("bots", {})
    d.setdefault("rooms", ["건이네가족", "김창기"])
    items = d.setdefault("items", {})
    for k, v in DEFAULT_ITEMS.items():
        if k not in items:
            items[k] = dict(v, enabled=True)
    for it in items.values():
        it.setdefault("enabled", True)
        it.setdefault("channel", "telegram")
        it.setdefault("bot", "mr")
        it.setdefault("chat_id", "")
        it.setdefault("kakao_room", "")
        it.setdefault("caller_targets", False)
    return d


def save(path: Path, d: dict) -> None:
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with _lock:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def public_view(d: dict) -> dict:
    """화면용: 토큰 대신 끝 4자리"""
    bots = {bid: {"name": b.get("name") or bid, "username": b.get("username") or "",
                  "token_tail": ("…" + str(b.get("token") or "")[-4:]) if b.get("token") else ""}
            for bid, b in d.get("bots", {}).items()}
    return {"bots": bots, "rooms": list(d.get("rooms") or []), "items": d.get("items", {}),
            "channels": [{"value": c, "label": CHANNEL_LABELS[c]} for c in CHANNELS]}


def update_item(d: dict, key: str, data: dict) -> str | None:
    """화면에서 고친 값 반영. 잘못된 값이면 오류 문구"""
    it = d["items"].get(key)
    if it is None:
        return "모르는 알림이에요."
    if "enabled" in data:
        it["enabled"] = bool(data["enabled"])
    if "channel" in data:
        if data["channel"] not in CHANNELS:
            return "채널 값이 이상해요."
        it["channel"] = data["channel"]
    if "bot" in data:
        b = str(data["bot"] or "")
        if b != "mr" and b not in d["bots"]:
            return "등록 안 된 봇이에요."
        it["bot"] = b
    if "chat_id" in data:
        c = str(data["chat_id"] or "").strip()
        if c and not _CHAT_RE.match(c):
            return "대화방 ID는 숫자(그룹은 -로 시작)나 @채널이름이에요."
        it["chat_id"] = c
    if "kakao_room" in data:
        r = str(data["kakao_room"] or "").strip()
        if r and r not in d["rooms"]:
            return "등록 안 된 카톡 방이에요. 먼저 '카톡 방'에 추가해 주세요."
        it["kakao_room"] = r
    if "name" in data and str(data["name"]).strip():
        it["name"] = str(data["name"]).strip()[:60]
    if it["channel"] in ("kakao", "both", "kakao_tg") and not it["kakao_room"]:
        return "카톡으로 보내려면 카톡 방을 골라 주세요."
    return None


def set_bot(d: dict, bid: str, name: str, token: str, username: str = "") -> str | None:
    if bid == "mr":
        return "MR 봇은 연동·경로 설정의 텔레그램 토큰을 써요."
    if not _BOT_RE.match(bid or ""):
        return "봇 ID는 영문 소문자·숫자·_ 2~30자예요."
    old = d["bots"].get(bid, {})
    token = (token or "").strip() or old.get("token", "")
    if not _TOKEN_RE.match(token):
        return "토큰 형식이 아니에요(숫자:문자열)."
    d["bots"][bid] = {"name": (name or "").strip()[:40] or bid, "token": token, "username": username or old.get("username", "")}
    return None


def delete_bot(d: dict, bid: str) -> str | None:
    users = [k for k, it in d["items"].items() if it.get("bot") == bid]
    if users:
        return "이 봇을 쓰는 알림이 있어요: " + ", ".join(users)
    d["bots"].pop(bid, None)
    return None


def set_rooms(d: dict, rooms: list) -> str | None:
    clean = []
    for r in rooms or []:
        r = str(r).strip()
        if r and r not in clean:
            if len(r) > 50:
                return "방 이름이 너무 길어요."
            clean.append(r)
    used = {it["kakao_room"] for it in d["items"].values() if it.get("kakao_room")}
    missing = used - set(clean)
    if missing:
        return "쓰는 알림이 있는 방은 지울 수 없어요: " + ", ".join(sorted(missing))
    d["rooms"] = clean
    return None


# ───────────────────────────── 기록 ─────────────────────────────
def _db(path: Path) -> sqlite3.Connection:
    new = not Path(path).exists()
    c = sqlite3.connect(str(path), timeout=10)
    # NAS 디스크가 바쁘면 커밋마다 fsync가 몇 초씩 걸려 알림이 늦어진다(11-10 테스트에서 확인) → WAL + NORMAL
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("""CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, key TEXT NOT NULL,
                 name TEXT, channel TEXT, target TEXT, text TEXT, photo TEXT, status TEXT NOT NULL, error TEXT, source TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS log_ts ON log(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS log_key ON log(key)")
    if new:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return c


def log(path: Path, key: str, name: str, channel: str, target: str, text: str, photo: str, status: str,
        error: str = "", source: str = "") -> None:
    with _lock:
        c = _db(path)
        try:
            c.execute("INSERT INTO log (ts,key,name,channel,target,text,photo,status,error,source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (now_str(), key, name, channel, target, (text or "")[:TEXT_MAX], (photo or "")[:500], status,
                       (error or "")[:300], source))
            cut = (datetime.now(KST) - timedelta(days=LOG_KEEP_DAYS)).strftime("%Y-%m-%d")
            c.execute("DELETE FROM log WHERE ts < ?", (cut,))
            c.commit()
        finally:
            c.close()


def query_log(path: Path, key: str = "", status: str = "", q: str = "", date_from: str = "", date_to: str = "",
              limit: int = 200) -> list[dict]:
    if not Path(path).exists():
        return []
    sql, args = "SELECT id,ts,key,name,channel,target,text,photo,status,error,source FROM log WHERE 1=1", []
    if key:
        sql += " AND key = ?"
        args.append(key)
    if status:
        sql += " AND status = ?"
        args.append(status)
    if q:
        sql += " AND (text LIKE ? OR target LIKE ? OR name LIKE ?)"
        args += ["%" + q + "%"] * 3
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from or ""):
        sql += " AND ts >= ?"
        args.append(date_from)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to or ""):
        sql += " AND ts < ?"
        args.append((datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"))
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit or 200), 1000)))
    with _lock:
        c = _db(path)
        try:
            cols = ["id", "ts", "key", "name", "channel", "target", "text", "photo", "status", "error", "source"]
            return [dict(zip(cols, r)) for r in c.execute(sql, args).fetchall()]
        finally:
            c.close()


# ───────────────────────────── 중복 막기 (11-11) ─────────────────────────────
# n8n 날씨봇 노드가 '실패 시 3번 재시도'라, 허브가 느리게(디스크) 답하면 같은 글을 3번 보냈다.
# 같은 알림·같은 받는 곳·같은 글이 DEDUPE_SEC 안에 이미 나갔으면 보내지 않고 'dup'으로 기록만.
DEDUPE_SEC = 180
_recent: dict = {}
_recent_lock = threading.Lock()


def _dup_key(key: str, target: str, text: str, photo: str) -> tuple:
    return (key, target, hash((text or "")[:TEXT_MAX]), photo or "")


def seen_recently(key: str, target: str, text: str, photo: str = "", log_path: Path | None = None) -> bool:
    k, now = _dup_key(key, target, text, photo), time.time()
    with _recent_lock:
        for kk in [kk for kk, t in _recent.items() if now - t > DEDUPE_SEC]:
            _recent.pop(kk, None)
        if k in _recent:
            return True
    if log_path and Path(log_path).exists():       # 다른 워커·재시작 뒤에도: 기록에서 확인
        cut = (datetime.now(KST) - timedelta(seconds=DEDUPE_SEC)).strftime("%Y-%m-%d %H:%M:%S")
        with _lock:
            c = _db(log_path)
            try:
                r = c.execute("SELECT 1 FROM log WHERE key=? AND target=? AND text=? AND status='ok' AND ts>=? LIMIT 1",
                              (key, target, (text or "")[:TEXT_MAX], cut)).fetchone()
            finally:
                c.close()
        if r:
            return True
    return False


def mark_sent(key: str, target: str, text: str, photo: str = "") -> None:
    with _recent_lock:
        _recent[_dup_key(key, target, text, photo)] = time.time()


# ───────────────────────────── 보내기 ─────────────────────────────
def strip_html(text: str) -> str:
    """카톡엔 텔레그램 HTML 태그를 빼고 보낸다"""
    t = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def dispatch(d: dict, key: str, text: str, *, photo: str = "", parse_mode: str = "", chat_ids: list | None = None,
             bot_token_of=None, default_chat_of=None, tg_send=None, kakao_send=None,
             log_path: Path | None = None) -> dict:
    """알림 하나 보내기. 반환 {ok, skipped?, results:[{channel,target,ok,error}]}
    - bot_token_of(bot_id) -> 토큰(없으면 "")
    - tg_send(token, chat_id, text, photo, parse_mode) -> (ok, error)
    - kakao_send(room, poster_https_url, text) -> (ok, error)"""
    it = d["items"].get(key)
    if it is None:
        return {"ok": False, "error": "등록 안 된 알림이에요: " + key, "results": []}
    name, src = it.get("name") or key, it.get("source") or ""
    text = (text or "").strip()
    if not text and not photo:
        return {"ok": False, "error": "보낼 내용이 없어요.", "results": []}

    def rec(channel, target, status, error=""):
        if log_path:
            log(log_path, key, name, channel, target, text, photo, status, error, src)

    if not it.get("enabled"):
        rec(it.get("channel"), "", "off")
        return {"ok": True, "skipped": True, "results": []}

    results = []

    def do_tg():
        token = bot_token_of(it.get("bot") or "mr") if bot_token_of else ""
        targets = [str(c) for c in (chat_ids or []) if _CHAT_RE.match(str(c))] if it.get("caller_targets") and chat_ids else []
        if not targets:
            chat = it.get("chat_id") or (default_chat_of(it.get("bot") or "mr") if default_chat_of else "")
            targets = [chat] if chat else []
        if not token or not targets:
            err = "봇 토큰이 없어요" if not token else "텔레그램 대화방이 정해지지 않았어요"
            rec("telegram", ",".join(targets), "fail", err)
            results.append({"channel": "telegram", "target": "", "ok": False, "error": err})
            return False
        any_ok = False
        for chat in targets:
            label = f"{it.get('bot')}→{chat}"
            if seen_recently(key, label, text, photo, log_path):
                rec("telegram", label, "dup", f"{DEDUPE_SEC // 60}분 안에 같은 글을 이미 보냄")
                results.append({"channel": "telegram", "target": chat, "ok": True, "error": "", "dup": True})
                any_ok = True
                continue
            mark_sent(key, label, text, photo)           # 보내기 전에 표시: 동시에 들어온 같은 요청도 막음
            ok, err = tg_send(token, chat, text, photo, parse_mode)
            if not ok:
                with _recent_lock:
                    _recent.pop(_dup_key(key, label, text, photo), None)   # 실패면 다시 시도할 수 있게
            rec("telegram", label, "ok" if ok else "fail", err or "")
            results.append({"channel": "telegram", "target": chat, "ok": ok, "error": err or ""})
            any_ok = any_ok or ok
        return any_ok

    def do_kakao():
        room = it.get("kakao_room") or ""
        if not room:
            rec("kakao", "", "fail", "카톡 방이 정해지지 않았어요")
            results.append({"channel": "kakao", "target": "", "ok": False, "error": "카톡 방 없음"})
            return False
        poster = photo if str(photo).startswith("https://") else ""
        body = strip_html(text) if parse_mode.upper() == "HTML" else text
        if seen_recently(key, room, text, photo, log_path):
            rec("kakao", room, "dup", f"{DEDUPE_SEC // 60}분 안에 같은 글을 이미 보냄")
            results.append({"channel": "kakao", "target": room, "ok": True, "error": "", "dup": True})
            return True
        mark_sent(key, room, text, photo)
        with _kakao_lock:
            ok, err = kakao_send(room, poster, body)
        if not ok:
            with _recent_lock:
                _recent.pop(_dup_key(key, room, text, photo), None)
        if ok and photo and not poster:
            err = "사진은 https 주소가 아니라 글만 보냈어요"
        rec("kakao", room, "ok" if ok else "fail", err or "")
        results.append({"channel": "kakao", "target": room, "ok": ok, "error": err or ""})
        return ok

    ch = it.get("channel")
    if ch == "telegram":
        do_tg()
    elif ch == "kakao":
        do_kakao()
    elif ch == "both":
        do_tg()
        do_kakao()
    elif ch == "kakao_tg":
        if not do_kakao():
            do_tg()
    ok = any(r["ok"] for r in results)
    return {"ok": ok, "results": results, "error": "" if ok else "; ".join(r["error"] for r in results if r["error"])}
