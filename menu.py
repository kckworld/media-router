# -*- coding: utf-8 -*-
"""MR 메뉴 한 곳 정의 + 계정별 권한 (2026-11-04 계정·메뉴 관리 2단계).

화면 메뉴(base.html)와 서버 권한 검사(web_admin._perm_guard)가 같은 목록을 쓴다.
페이지마다 그 화면과 API 주소(prefixes)를 적어 두고, 요청 주소가 어느 페이지 것인지 찾아 계정 권한(none/view/edit)을 본다.
- owner_only: 소유자 전용으로 고정(다른 계정에 못 줌) — 설정 전체·검색직·Claude 관리.
- caution: 다른 계정에 줄 때 "개인정보가 보여요" 확인(가계부·일기).
- legacy_hidden: 예전 보기 전용 계정에게 숨겼던 화면 → 일반 계정 기본값이 '없음'(그 밖은 기본 '보기').
새 화면을 만들면 여기 한 줄(주소 포함)만 추가하면 메뉴·권한이 같이 붙는다. 주소가 어느 페이지에도 없으면
일반 계정은 GET만 되고 POST는 막힌다(안전한 쪽).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

LEVELS = ("none", "view", "edit")

GROUPS = [
    {"key": "today", "label": "오늘", "pages": [
        {"key": "check", "label": "체크 목록", "href": "/?mode=check",
         "prefixes": ["/check", "/api/check", "/api/gulbang", "/reset_day", "/check_episodes"]},
        {"key": "stats", "label": "시청 통계", "href": "/stats", "prefixes": ["/stats", "/api/stats"]}]},
    {"key": "rules", "label": "규칙", "pages": [
        {"key": "rules", "label": "분류 규칙", "href": "/?mode=", "prefixes": ["/add", "/edit", "/delete", "/search_tmdb"]},
        {"key": "rename", "label": "파일명 치환", "href": "/rename_rules", "prefixes": ["/rename_rules"]},
        {"key": "bt4g", "label": "예능 키워드", "href": "/bt4g_keywords", "prefixes": ["/bt4g_keywords", "/api/bt4g_enabled"]},
        {"key": "drama", "label": "드라마 키워드", "href": "/drama_keywords", "prefixes": ["/drama_keywords"]}]},
    {"key": "ops", "label": "작업", "pages": [
        {"key": "downloads", "label": "다운로드", "href": "/downloads", "prefixes": ["/downloads", "/api/downloads"]},
        {"key": "youtube", "label": "유튜브", "href": "/youtube", "prefixes": ["/youtube", "/api/youtube"]},
        {"key": "movies", "label": "영화", "href": "/movies", "prefixes": ["/movies", "/api/movies"]},
        {"key": "tfdrama", "label": "드라마", "href": "/tf/drama", "prefixes": ["/tf/drama", "/api/tf/drama"]},
        {"key": "tfvariety", "label": "예능", "href": "/tf/variety", "prefixes": ["/tf/variety", "/api/tf/variety"]},
        {"key": "requests", "label": "요청", "href": "/requests", "prefixes": ["/requests", "/api/requests", "/api/account_requests"],
         "legacy_hidden": True},
        {"key": "surveys", "label": "설문", "href": "/surveys", "prefixes": ["/surveys", "/surveys.csv", "/api/surveys"]},
        {"key": "batch", "label": "배치", "href": "/logs", "prefixes": ["/logs", "/run_batch", "/api/processing_pause"]},
        {"key": "encode", "label": "인코딩", "href": "/encode_log",
         "prefixes": ["/encode_log", "/api/reencode", "/wake_pc", "/shutdown_pc", "/schedule_pc_shutdown", "/cancel_shutdown"]}]},
    {"key": "life", "label": "생활", "pages": [
        {"key": "recon", "label": "가계부", "href": "/recon", "prefixes": ["/recon", "/api/recon"],
         "legacy_hidden": True, "caution": True},
        # 고정비·택배(11-13): 가계부·쇼핑 메일에서 뽑은 금융·주문 정보 — 나만
        {"key": "fixed", "label": "고정비", "href": "/fixed", "prefixes": ["/fixed", "/api/fixed"], "owner_only": True},
        {"key": "parcels", "label": "택배", "href": "/parcels", "prefixes": ["/parcels", "/api/parcels"], "owner_only": True},
        {"key": "journal", "label": "일기", "href": "/journal", "prefixes": ["/journal", "/api/journal", "/diary", "/api/diary"],
         "legacy_hidden": True, "caution": True},
        {"key": "people", "label": "검색직", "href": "/people", "prefixes": ["/people", "/api/people"], "owner_only": True},
        # 메모장(11-11): 나만 쓰는 웹 동기화 메모. 공유 링크는 공개 사이트 /pub/n/… (menu 밖, _pub_gate)
        {"key": "notes", "label": "메모장", "href": "/notes", "prefixes": ["/notes", "/api/notes"], "owner_only": True},
        # 핫딜(11-13): 텔레그램 핫딜 채널 모아 보기. 홈 화면 앱 /hotdeal?app=1
        {"key": "hotdeal", "label": "핫딜", "href": "/hotdeal", "prefixes": ["/hotdeal", "/api/hotdeal"], "owner_only": True}]},
    {"key": "settings", "label": "설정", "pages": [
        {"key": "settings", "label": "연동·경로", "href": "/settings", "prefixes": ["/settings"], "owner_only": True},
        {"key": "users", "label": "사용자 매핑", "href": "/users", "prefixes": ["/users", "/api/users"], "owner_only": True},
        # 알림 허브(11-10): MR 요청 완료 안내·HA·n8n 알림 켜기/끄기·채널·봇·카톡 방·보낸 기록
        {"key": "notify", "label": "알림", "href": "/notify", "prefixes": ["/notify", "/api/notify"], "owner_only": True},
        # 공개 사이트(request.kckworld.com, 11-07): 사용법 문서 편집·그림 올리기, 공개 화면 미리 보기
        {"key": "guides", "label": "공개 사이트", "href": "/guides", "prefixes": ["/guides", "/api/guides"], "owner_only": True},
        {"key": "skills", "label": "Claude", "href": "/skills", "prefixes": ["/skills", "/api/skills"], "owner_only": True},
        {"key": "accounts", "label": "계정", "href": "/accounts", "prefixes": ["/accounts", "/api/accounts"], "owner_only": True},
        {"key": "menu", "label": "메뉴 관리", "href": "/menu", "prefixes": ["/menu", "/api/menu"], "owner_only": True}]},
]
PAGES = {p["key"]: dict(p, group=g["key"]) for g in GROUPS for p in g["pages"]}
# 누구나(로그인한 일반 계정) GET만 되는 공용 주소 — 헤더 PC 상태 등. POST는 페이지 권한으로만
COMMON_GET = ("/pc_status", "/schedule_pc_shutdown/status", "/static", "/favicon.ico", "/apple-touch-icon")
ALWAYS_OK = ("/login", "/logout", "/health")


def _match(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/") or (prefix.endswith(".csv") and path == prefix)


def page_for(path: str, args: dict | None = None) -> str | None:
    """요청 주소 → 페이지 키. '/'는 mode로 체크 목록/분류 규칙을 나눈다"""
    if path == "/":
        mode = (args or {}).get("mode", "check")
        return "check" if mode in ("check", None) else "rules"
    best, best_len = None, -1
    for key, p in PAGES.items():
        for pre in p["prefixes"]:
            if _match(path, pre) and len(pre) > best_len:
                best, best_len = key, len(pre)
    return best


def default_level(key: str) -> str:
    p = PAGES.get(key, {})
    if p.get("owner_only"):
        return "none"
    return "none" if p.get("legacy_hidden") else "view"


def level(user: str, perms: dict | None, key: str, owner: str = "owner") -> str:
    """계정 user의 페이지 key 권한. 소유자는 늘 edit, owner_only는 소유자 말고 늘 none"""
    if user == owner:
        return "edit"
    p = PAGES.get(key)
    if not p or p.get("owner_only"):
        return "none"
    v = (perms or {}).get(key)
    return v if v in LEVELS else default_level(key)


# ── 메뉴 보이기·순서 (3단계 화면에서 바꿈). data/menu.json: {"order": [page keys], "hidden": [page keys]} ──

def load_layout(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_layout(path: Path, layout: dict) -> None:
    clean = {"order": [k for k in layout.get("order", []) if k in PAGES],
             # 11-07: 소유자 전용 화면(검색직 등)도 숨기기는 됨(권한은 그대로 소유자 전용). 계정·메뉴 관리 자신만 못 숨김
             "hidden": [k for k in layout.get("hidden", []) if k in PAGES and k not in ("accounts", "menu")]}
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def nav_for(user: str, perms: dict | None, layout: dict | None = None, owner: str = "owner") -> list[dict]:
    """그 계정이 볼 메뉴(그룹 → 페이지). 권한 없음·숨김은 빠지고, 순서는 layout.order(그룹 안에서)"""
    layout = layout or {}
    hidden = set(layout.get("hidden") or [])
    order = {k: i for i, k in enumerate(layout.get("order") or [])}
    out = []
    for g in GROUPS:
        pages = [p for p in g["pages"]
                 if p["key"] not in hidden and level(user, perms, p["key"], owner) != "none"]
        pages.sort(key=lambda p: order.get(p["key"], 1000 + [q["key"] for q in g["pages"]].index(p["key"])))
        if pages:
            out.append({"key": g["key"], "label": g["label"],
                        "pages": [{"key": p["key"], "label": p["label"], "href": p["href"]} for p in pages]})
    return out


def first_href(nav: list[dict]) -> str | None:
    for g in nav:
        for p in g["pages"]:
            return p["href"]
    return None
