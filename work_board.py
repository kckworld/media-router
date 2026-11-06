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
