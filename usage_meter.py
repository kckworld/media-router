"""상단 바 사용량 표시(2026-11-12 사용자): Claude 플랜 한도(5시간·주간)와 안티그래비티(agy) 남은 양.

- Claude: ~/.claude/.credentials.json의 로그인 토큰으로 api.anthropic.com/api/oauth/usage (Claude Code /usage와 같은 값)
- agy: ~/.gemini/antigravity-cli/antigravity-oauth-token으로 v1internal:fetchAvailableModels의 모델별 quotaInfo
  (remainingFraction·resetTime). Gemini 묶음과 agy 안의 Claude·GPT 묶음은 한도가 따로라 둘 다 남긴다.
  agy 토큰은 1시간짜리라 만료됐으면 refresh_agy()(호스트에서 `agy models` 한 번)로 갱신하고 다시 읽는다.
5분마다 refresh()가 data/usage_meter.json에 저장하고, 화면은 그 파일만 읽는다(load). 실패하면 이전 값을 두고 stale 표시.
토큰 값은 로그·파일에 남기지 않는다.
"""
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path(os.getenv("USAGE_HOME", "/app/kck_home"))
CLAUDE_CRED = HOME / ".claude" / ".credentials.json"
AGY_TOKEN = HOME / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
CLAUDE_URL = "https://api.anthropic.com/api/oauth/usage"
AGY_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
KST = timezone(timedelta(hours=9))
WEEKDAYS = "월화수목금토일"


def _get_json(url, token, data=None, headers=None):
    h = {"Authorization": "Bearer " + token, "User-Agent": "media-router"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _pct(v):
    try:
        return max(0, min(100, round(float(v))))
    except (TypeError, ValueError):
        return None


def fetch_claude(get=_get_json):
    cred = json.loads(CLAUDE_CRED.read_text(encoding="utf-8"))["claudeAiOauth"]
    d = get(CLAUDE_URL, cred["accessToken"], headers={"anthropic-beta": "oauth-2025-04-20"})
    five, week = d.get("five_hour") or {}, d.get("seven_day") or {}
    return {"five": _pct(five.get("utilization")), "five_reset": five.get("resets_at"),
            "week": _pct(week.get("utilization")), "week_reset": week.get("resets_at")}


def _agy_token():
    d = json.loads(AGY_TOKEN.read_text(encoding="utf-8"))["token"]
    exp = d.get("expiry") or ""
    try:   # 2026-11-12T10:48:54.289017524+09:00 — 나노초는 fromisoformat이 못 읽어서 소수점 아래를 뗀다
        exp = re.sub(r"\.\d+", "", exp).replace("Z", "+00:00")
        expired = datetime.fromisoformat(exp) <= datetime.now(timezone.utc) + timedelta(seconds=60)
    except ValueError:
        expired = False
    return d["access_token"], expired


def fetch_agy(get=_get_json, refresh_agy=None):
    token, expired = _agy_token()
    if expired and refresh_agy:
        refresh_agy()
        token, expired = _agy_token()
    # User-Agent가 antigravity가 아니면 403(11-12 확인)
    d = get(AGY_URL, token, data=b"{}", headers={"Content-Type": "application/json", "User-Agent": "antigravity"})
    groups = {"gemini": [], "other": []}
    for name, m in (d.get("models") or {}).items():
        q = m.get("quotaInfo") or {}
        if "resetTime" not in q:          # 탭 자동 완성 등 한도 없는 내부 모델
            continue
        key = "gemini" if name.startswith("gemini") else "other"
        groups[key].append((float(q.get("remainingFraction", 1)), q["resetTime"]))
    out = {}
    for key, items in groups.items():
        if items:
            frac, reset = min(items)
            out[key] = _pct((1 - frac) * 100)
            out[key + "_reset"] = reset
    return out


def refresh(path: Path, refresh_agy=None, get=_get_json):
    """두 곳을 읽어 path에 저장. 한쪽이 실패하면 그쪽은 이전 값 + stale"""
    old = load(path)
    out = {"ts": time.time()}
    for key, fn in (("claude", lambda: fetch_claude(get)), ("agy", lambda: fetch_agy(get, refresh_agy))):
        try:
            out[key] = fn()
            out[key]["at"] = time.time()
        except Exception as e:            # 토큰 만료·네트워크 — 값은 남겨 두고 표시만 흐리게
            prev = dict(old.get(key) or {})
            prev["stale"] = True
            prev["error"] = type(e).__name__
            out[key] = prev
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return out


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _reset_label(iso):
    if not iso:
        return ""
    try:
        # 14:59:59.9처럼 와서 분 단위로 반올림(Claude 화면도 00:00으로 보여 줌)
        t = (datetime.fromisoformat(iso.replace("Z", "+00:00")) + timedelta(seconds=30)).astimezone(KST)
    except ValueError:
        return ""
    return f"{t:%m-%d}({WEEKDAYS[t.weekday()]}) {t:%H:%M}"


def _level(p):
    return "bad" if p is not None and p >= 90 else "warn" if p is not None and p >= 70 else ""


def view(data: dict, max_age: int = 1800):
    """상단 칩에 쓸 값. 읽은 값이 없으면 None(칩 안 띄움)"""
    c, a = data.get("claude") or {}, data.get("agy") or {}
    if c.get("week") is None and a.get("gemini") is None:
        return None
    now = time.time()
    old = lambda d: d.get("stale") or now - (d.get("at") or 0) > max_age
    lines = []
    if c.get("week") is not None:
        five = c.get("five") or 0
        lines.append(f"Claude 5시간 {five}%" + (f" · {_reset_label(c.get('five_reset'))} 초기화" if five else " · 첫 메시지를 보내면 시작"))
        lines.append(f"Claude 주간 {c['week']}% · {_reset_label(c.get('week_reset'))} 초기화")
    if a.get("gemini") is not None:
        lines.append(f"agy Gemini {a['gemini']}% · {_reset_label(a.get('gemini_reset'))} 초기화")
    if a.get("other") is not None:
        lines.append(f"agy Claude·GPT {a['other']}% · {_reset_label(a.get('other_reset'))} 초기화")
    if old(c) or old(a):
        lines.append("(일부는 마지막으로 읽은 값)")
    return {"five": c.get("five"), "week": c.get("week"), "agy": a.get("gemini"),
            "claude_lvl": _level(max(c.get("five") or 0, c.get("week") or 0)), "agy_lvl": _level(a.get("gemini")),
            "claude_old": bool(c) and old(c), "agy_old": bool(a) and old(a), "title": "\n".join(lines)}
