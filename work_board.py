# -*- coding: utf-8 -*-
"""설정 > Claude > [작업] (2026-11-07): 팀장의 작업 기록부 ai-shared/work.json을 읽기만 한다.

- work.json은 work.py(모든 세션)가 쓰고 MR은 읽기 전용 단일 파일 마운트로 본다(work.py가 제자리 덮어쓰기).
- 작업 파일(ai-shared/tasks/*.md)은 폴더를 읽기 전용으로 붙이되, work.json 항목이 가리키는 파일만,
  그중 '## 결과'·'## 확인 필요' 부분만 돌려준다(경로는 tasks/<이름>.md 꼴만 — 다른 경로·하위 폴더·심볼릭 링크 거부).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ITEM_FIELDS = ("id", "title", "cat", "status", "owner", "engine", "session", "task", "note", "requested", "updated", "done", "history")
TASK_RE = re.compile(r"^tasks/([^/\\\x00]+\.md)$")
SECTIONS = ("결과", "확인 필요")
MAX_SECTION = 8000


def load(path: Path) -> dict:
    """{"ok", "updated", "items"} — 파일이 없거나 깨지면 ok=False(화면은 안내만)"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "updated": "", "items": []}
    items = []
    for it in (d.get("items") or []) if isinstance(d, dict) else []:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        x = {k: it.get(k) for k in ITEM_FIELDS if it.get(k) not in (None, "")}
        x["history"] = [{"at": h.get("at"), "status": h.get("status"), "note": h.get("note")}
                        for h in (it.get("history") or []) if isinstance(h, dict)][-30:]
        items.append(x)
    return {"ok": True, "updated": d.get("updated") or "", "items": items}


def _sections(text: str) -> list[dict]:
    out, cur, buf = [], None, []
    for ln in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", ln)
        if m:
            if cur:
                out.append({"title": cur, "body": "\n".join(buf).strip()[:MAX_SECTION]})
            title = m.group(1)
            cur = title if any(title.startswith(s) for s in SECTIONS) else None
            buf = []
        elif cur:
            buf.append(ln)
    if cur:
        out.append({"title": cur, "body": "\n".join(buf).strip()[:MAX_SECTION]})
    return out


def task_sections(tasks_dir: Path, items: list[dict], item_id: str) -> dict | None:
    """항목 item_id의 작업 파일에서 결과·확인 필요 부분. 항목이 없거나 경로가 이상하면 None"""
    it = next((i for i in items if i.get("id") == item_id), None)
    if not it:
        return None
    m = TASK_RE.match(str(it.get("task") or ""))
    if not m:
        return {"file": it.get("task") or "", "sections": [], "missing": True}
    p = tasks_dir / m.group(1)
    try:
        if p.is_symlink() or p.resolve().parent != tasks_dir.resolve():
            return {"file": it["task"], "sections": [], "missing": True}
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"file": it["task"], "sections": [], "missing": True}
    return {"file": it["task"], "sections": _sections(text), "missing": False}


# ─── 취소·보류·다시 하기 (2026-11-10 사용자: "토큰을 아끼려고") ───────────────
# MR 작업 화면 버튼이 work.json 상태를 바꾼다. work.py와 똑같이 flock 잡고 제자리 덮어쓰기(단일 파일 마운트라 rename 안 됨).
# 세션들은 다음 단계·예약 확인 전에 상태를 보고 '취소'면 멈추고 예약을 지우며, '보류'면 held/<ID>.md를 남기고 멈춘다(TEAM.md 0-1).
import fcntl
import os
from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))
ACTIONS = {"cancel": "취소", "hold": "보류", "resume": "대기"}
OPEN_STATUSES = ("대기", "진행 중", "확인 필요")
_ID_RE = re.compile(r"^W-\d{8}-\d{2,3}$")


def _own(p) -> None:
    """MR 컨테이너는 root라 새 파일을 kck9010:users로(세션이 이어서 고칠 수 있게)"""
    try:
        os.chown(p, 1026, 100)
    except OSError:
        pass


def _now() -> str:
    return datetime.now(_KST).isoformat(timespec="minutes")


def set_status(path: Path, item_id: str, action: str, note: str = "") -> tuple[dict | None, str]:
    """(바뀐 항목, 오류). cancel·hold는 열린 일(대기·진행 중·확인 필요)만, resume은 보류만"""
    if action not in ACTIONS or not _ID_RE.match(item_id or ""):
        return None, "잘못된 요청이에요."
    with open(path, "r+", encoding="utf-8") as fp:
        fcntl.flock(fp, fcntl.LOCK_EX)
        t = fp.read()
        data = json.loads(t) if t.strip() else {"items": []}
        it = next((i for i in data.get("items", []) if i.get("id") == item_id), None)
        if not it:
            return None, "그 작업을 찾지 못했어요."
        st = it.get("status")
        if action in ("cancel", "hold") and st not in OPEN_STATUSES:
            return None, f"'{st}' 상태라 {ACTIONS[action]}할 수 없어요."
        if action == "resume" and st != "보류":
            return None, "보류한 일만 다시 할 수 있어요."
        new = ACTIONS[action]
        msg = (note or "").strip()[:200] or {"cancel": "사용자가 MR에서 취소", "hold": "사용자가 MR에서 보류",
                                              "resume": "사용자가 MR에서 다시 하기"}[action]
        it["status"], it["updated"] = new, _now()
        if new == "취소":
            it["done"] = _now()
        it.setdefault("history", []).append({"at": _now(), "status": new, "note": msg})
        data["updated"] = _now()
        fp.seek(0)
        fp.truncate()
        json.dump(data, fp, ensure_ascii=False, indent=1)
        fp.flush()
    try:
        os.chmod(path, 0o664)
    except OSError:
        pass
    return it, ""


def write_held(held_dir: Path, item: dict, sections: list[dict]) -> str:
    """보류한 일을 나중에 이어서 할 수 있게 held/<ID>.md로(작업 파일의 결과·확인 필요 부분 포함). 반환: ai-shared 기준 경로"""
    held_dir.mkdir(parents=True, exist_ok=True)
    _own(held_dir)
    p = held_dir / f"{item['id']}.md"
    lines = [f"# 보류: {item.get('title', '')}", "",
             f"- ID: {item['id']} · 분류 {item.get('cat', '')} · 맡은 곳 {item.get('owner', '')} · 엔진 {item.get('engine', '')}",
             f"- 시킨 날 {item.get('requested', '')} · 보류 {_now()}",
             f"- 작업 파일: {item.get('task') or '(없음)'}",
             f"- 메모: {item.get('note') or ''}", "",
             "## 다시 시작하려면",
             f"MR 설정 > Claude > 작업에서 [다시 하기]를 누르거나, 세션에 \"{item['id']} 이어서 해줘\"라고 하면 "
             "이 파일과 작업 파일을 읽고 이어 간다.", "",
             "## 바뀐 기록"]
    lines += [f"- {h.get('at', '')} {h.get('status', '')} {h.get('note', '')}".rstrip() for h in item.get("history", [])[-20:]]
    for sc in sections or []:
        lines += ["", f"## (작업 파일) {sc['title']}", sc.get("body") or "(비어 있음)"]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _own(p)
    try:
        os.chmod(p, 0o664)
    except OSError:
        pass
    return f"held/{p.name}"
