# -*- coding: utf-8 -*-
"""SQLite-backed persistence layer — replaces config.yaml / state.yaml / status.json"""
from __future__ import annotations
import json, os, sqlite3
from pathlib import Path
from typing import Any, Dict, List

BASE: Path = Path(__file__).resolve().parent
DATA_DIR: Path = Path(os.getenv("DATA_DIR", str(BASE / "data")))
DB_PATH: Path = DATA_DIR / "media_router.db"

WEEKDAYS: List[str] = ["월", "화", "수", "목", "금", "토", "일"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    position          INTEGER NOT NULL DEFAULT 0,
    category          TEXT    NOT NULL DEFAULT '',
    pattern           TEXT    NOT NULL DEFAULT '',
    pattern_or        TEXT,
    pattern2          TEXT,
    pattern2_or       TEXT,
    exclude_pattern   TEXT,
    subfolder         TEXT    NOT NULL DEFAULT '',
    days              TEXT    NOT NULL DEFAULT '[]',
    updated           TEXT    NOT NULL DEFAULT 'N',
    updated_map       TEXT    NOT NULL DEFAULT '{}',
    total_episodes    INTEGER,
    received_episodes TEXT,
    last_episode      INTEGER,
    release           TEXT
);
"""

_initialized = False
_connection: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    """Return a shared, persistent connection.

    Reused across calls instead of opening/closing per request: on this NAS's
    btrfs volume, closing the last WAL connection triggers an auto-checkpoint
    (fsync-bound, observed 1-10s+), so opening a fresh connection for every
    load/save made every save operation pay that cost. synchronous=NORMAL
    avoids fsync on ordinary commits in WAL mode (only checkpoints need it).
    """
    global _connection
    if _connection is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _connection = c
    return _connection


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    c = _conn()
    c.executescript(_SCHEMA)
    c.commit()
    # 기존 DB에 pattern2, exclude_pattern 컬럼 추가 (마이그레이션)
    _migrate_add_pattern2_exclude(c)
    row_count = c.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
    cfg_count = c.execute("SELECT COUNT(*) FROM config").fetchone()[0]
    if row_count == 0 and cfg_count == 0:
        _try_migrate(c)
    _initialized = True


def _migrate_add_pattern2_exclude(c: sqlite3.Connection) -> None:
    """Add pattern_or, pattern2, pattern2_or, exclude_pattern columns if they don't exist."""
    try:
        # Check if columns already exist
        cols = [col[1] for col in c.execute("PRAGMA table_info(rules)")]
        if "pattern_or" not in cols:
            c.execute("ALTER TABLE rules ADD COLUMN pattern_or TEXT")
        if "pattern2" not in cols:
            c.execute("ALTER TABLE rules ADD COLUMN pattern2 TEXT")
        if "pattern2_or" not in cols:
            c.execute("ALTER TABLE rules ADD COLUMN pattern2_or TEXT")
        if "exclude_pattern" not in cols:
            c.execute("ALTER TABLE rules ADD COLUMN exclude_pattern TEXT")
        c.commit()
    except Exception as e:
        print(f"[db] Column migration warning: {e}")
        c.rollback()


def _try_migrate(c: sqlite3.Connection) -> None:
    """Import config.yaml into DB on first run, then rename it."""
    config_path = BASE / "config.yaml"
    if not config_path.exists():
        return
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        for key in ("paths", "base_paths", "telegram", "ownership"):
            if key in cfg:
                c.execute(
                    "INSERT OR REPLACE INTO config (key, value) VALUES (?,?)",
                    (key, json.dumps(cfg[key], ensure_ascii=False)),
                )
        rules = cfg.get("rules") or []
        for pos, r in enumerate(rules):
            received = json.dumps(r["received_episodes"]) if r.get("received_episodes") is not None else None
            c.execute(
                """INSERT INTO rules
                       (position, category, pattern, subfolder, days,
                        updated, updated_map, total_episodes, received_episodes,
                        last_episode, release)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pos,
                    r.get("category", ""),
                    r.get("pattern", ""),
                    r.get("subfolder", ""),
                    json.dumps(r.get("days", [])),
                    r.get("updated", "N"),
                    json.dumps(r.get("updated_map", {})),
                    r.get("total_episodes"),
                    received,
                    r.get("last_episode"),
                    r.get("release"),
                ),
            )
        c.commit()
        config_path.rename(config_path.with_name("config.yaml.migrated"))
        print(f"[db] Migrated config.yaml → SQLite ({len(rules)} rules)")
    except Exception as e:
        print(f"[db] Migration warning: {e}")
        c.rollback()


def _ensure_rule_updated_map(rule: Dict) -> None:
    days = rule.get("days") or []
    umap = rule.get("updated_map")
    if not isinstance(umap, dict):
        umap = {}
    base_u = rule.get("updated", "N")
    if base_u not in ("Y", "N"):
        base_u = "N"
    for d in days:
        if d in WEEKDAYS and d not in umap:
            umap[d] = base_u
    rule["updated_map"] = umap


def load_cfg() -> Dict:
    init_db()
    c = _conn()
    cfg: Dict[str, Any] = {}
    for row in c.execute("SELECT key, value FROM config"):
        cfg[row["key"]] = json.loads(row["value"])

    cfg.setdefault("paths", {
        "sources": [],
        "cleanup": {"remove_dirs": ["@eaDir"], "remove_files": ["thumbs.db", "Thumbs.db"]},
    })
    cfg["paths"].setdefault("sources", [])
    cfg["paths"].setdefault("cleanup", {
        "remove_dirs": ["@eaDir"], "remove_files": ["thumbs.db", "Thumbs.db"],
    })
    cfg.setdefault("base_paths", {
        "예능": "/path/to/video/예능",
        "드라마": "/path/to/video/드라마",
        "다큐": "/path/to/video/다큐",
        "애니메이션": "/path/to/video/애니메이션",
    })
    cfg.setdefault("telegram", {"enabled": False, "bot_token": "", "chat_id": ""})
    cfg.setdefault("ownership", {
        "apply": True, "user": "plex", "group": "users",
        "file_mode": 0o664, "dir_mode": 0o775,
        "setgid_dirs": True, "enforce_inherit": True,
    })

    rules: List[Dict] = []
    for row in c.execute("SELECT * FROM rules ORDER BY position, id"):
        r: Dict[str, Any] = {
            "id": row["id"],
            "category": row["category"],
            "pattern": row["pattern"],
            "subfolder": row["subfolder"],
            "days": json.loads(row["days"] or "[]"),
            "updated": row["updated"],
            "updated_map": json.loads(row["updated_map"] or "{}"),
        }
        if row["pattern_or"]:
            r["pattern_or"] = row["pattern_or"]
        if row["pattern2"]:
            r["pattern2"] = row["pattern2"]
        if row["pattern2_or"]:
            r["pattern2_or"] = row["pattern2_or"]
        if row["exclude_pattern"]:
            r["exclude_pattern"] = row["exclude_pattern"]
        if row["total_episodes"] is not None:
            r["total_episodes"] = row["total_episodes"]
        if row["received_episodes"] is not None:
            r["received_episodes"] = json.loads(row["received_episodes"])
        if row["last_episode"] is not None:
            r["last_episode"] = row["last_episode"]
        if row["release"]:
            r["release"] = row["release"]
        _ensure_rule_updated_map(r)
        rules.append(r)

    cfg["rules"] = rules
    return cfg


def save_cfg(cfg: Dict) -> None:
    init_db()
    c = _conn()
    with c:
        for key in ("paths", "base_paths", "telegram", "ownership", "tmdb_api_key", "last_reset_date"):
            if key in cfg:
                c.execute(
                    "INSERT OR REPLACE INTO config (key, value) VALUES (?,?)",
                    (key, json.dumps(cfg[key], ensure_ascii=False)),
                )

        rules = cfg.get("rules") or []
        incoming_ids = {r["id"] for r in rules if r.get("id")}
        existing_ids = {row[0] for row in c.execute("SELECT id FROM rules")}

        for rid in existing_ids - incoming_ids:
            c.execute("DELETE FROM rules WHERE id=?", (rid,))

        for pos, r in enumerate(rules):
            received_json = (
                json.dumps(r["received_episodes"])
                if r.get("received_episodes") is not None
                else None
            )
            rule_id = r.get("id")
            if rule_id and rule_id in existing_ids:
                c.execute(
                    """UPDATE rules SET
                           position=?, category=?, pattern=?, pattern_or=?, pattern2=?, pattern2_or=?,
                           exclude_pattern=?, subfolder=?, days=?, updated=?, updated_map=?,
                           total_episodes=?, received_episodes=?,
                           last_episode=?, release=?
                       WHERE id=?""",
                    (
                        pos,
                        r.get("category", ""),
                        r.get("pattern", ""),
                        r.get("pattern_or"),
                        r.get("pattern2"),
                        r.get("pattern2_or"),
                        r.get("exclude_pattern"),
                        r.get("subfolder", ""),
                        json.dumps(r.get("days", [])),
                        r.get("updated", "N"),
                        json.dumps(r.get("updated_map", {})),
                        r.get("total_episodes"),
                        received_json,
                        r.get("last_episode"),
                        r.get("release"),
                        rule_id,
                    ),
                )
            else:
                c.execute(
                    """INSERT INTO rules
                           (position, category, pattern, pattern_or, pattern2, pattern2_or, exclude_pattern,
                            subfolder, days, updated, updated_map, total_episodes,
                            received_episodes, last_episode, release)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pos,
                        r.get("category", ""),
                        r.get("pattern", ""),
                        r.get("pattern_or"),
                        r.get("pattern2"),
                        r.get("pattern2_or"),
                        r.get("exclude_pattern"),
                        r.get("subfolder", ""),
                        json.dumps(r.get("days", [])),
                        r.get("updated", "N"),
                        json.dumps(r.get("updated_map", {})),
                        r.get("total_episodes"),
                        received_json,
                        r.get("last_episode"),
                        r.get("release"),
                    ),
                )


_RULE_PATCHABLE_COLUMNS = {"updated_map", "received_episodes", "last_episode", "total_episodes", "updated"}


def update_rule_fields(rule_id: int, **fields: Any) -> bool:
    """기존 규칙 하나의 지정된 컬럼만 UPDATE (전체 목록 교체/삭제 없음).

    save_cfg()는 "메모리에 들고 있는 전체 규칙 목록"을 기준으로 DB에 없는 id를
    삭제하고 있는 id를 통째로 덮어쓰는 스냅샷 교체 방식이다. media_router.py처럼
    크론으로 주기 실행되며 오래 걸리는(파일 이동 중 텔레그램 전송 등으로 수십 초
    이상 걸릴 수 있는) 호출자가 이 방식을 쓰면, 그 사이 웹 UI에서 규칙을
    추가/삭제했을 때 크론이 들고 있던 오래된 스냅샷 기준으로 방금 추가된 규칙을
    지워버리거나 방금 삭제된 규칙을 되살리는 레이스 컨디션이 생긴다.

    규칙을 추가/삭제하지 않고 기존 규칙의 상태 필드(처리 여부, 받은 에피소드 등)만
    갱신하는 호출자는 이 함수로 해당 id의 해당 컬럼만 UPDATE해야 한다. 그 사이
    규칙이 삭제됐다면 UPDATE는 0행에 적용되고 조용히 끝난다(되살리지 않음).
    다른 규칙에는 전혀 영향을 주지 않는다.
    """
    fields = {k: v for k, v in fields.items() if k in _RULE_PATCHABLE_COLUMNS}
    if not fields or not rule_id:
        return False
    init_db()
    c = _conn()
    set_clauses = []
    values: List[Any] = []
    for col, val in fields.items():
        if col in ("updated_map",):
            val = json.dumps(val)
        elif col == "received_episodes":
            val = json.dumps(val) if val is not None else None
        set_clauses.append(f"{col}=?")
        values.append(val)
    values.append(rule_id)
    with c:
        cur = c.execute(f"UPDATE rules SET {', '.join(set_clauses)} WHERE id=?", values)
    return cur.rowcount > 0


def set_config_value(key: str, value: Any) -> None:
    """규칙 목록은 건드리지 않고 top-level 설정 키 하나만 갱신.

    save_cfg()를 거치면 규칙 목록 diff/삭제 로직까지 함께 도는데, last_reset_date
    처럼 규칙과 무관한 단순 값을 갱신할 때는 그럴 필요가 없다.
    """
    init_db()
    c = _conn()
    with c:
        c.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?,?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )


# Alias so media_router.py can import save_cfg_atomic
save_cfg_atomic = save_cfg
