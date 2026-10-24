"""Claude Code 스킬 관리 (2026-10-24) - 웹 "설정 > 스킬" 화면.

NAS의 kck9010 홈(/app/kck_home)을 직접 읽고 쓴다.
- 설치된 스킬: 개인(~/.claude/skills/<이름>), claude.ai 동기화(~/.claude/skills/synced/...),
  플러그인(installed_plugins.json의 installPath/skills). 개인 스킬만 지울 수 있다.
- k-skill(github.com/NomaDamas/k-skill) 카탈로그: 저장소 압축본 하나를 받아 캐시해 두고
  거기서 목록을 읽고 개별 스킬을 설치한다.

k-skill의 SKILL.md는 본문 없이 `npx -y @nomadamas/k-skill@0 instruct <이름>`으로 npm에서
그때그때 지시문을 받아 따르게 돼 있다. 그러면 패키지가 바뀔 때마다 Claude가 따르는 지시가
몰래 바뀐다. 그래서 설치할 때 저장소의 원문(instruction.md)을 SKILL.md 본문으로 고정해 넣는다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import threading
import time
from pathlib import Path

import requests

HOME = Path(os.environ.get("SKILLS_HOME_ROOT", "/app/kck_home"))
# 플러그인 installPath는 호스트 경로로 적혀 있어서 컨테이너 경로로 바꿔 읽는다
HOST_HOME = os.environ.get("SKILLS_HOST_HOME", "/var/services/homes/kck9010")
OWNER_UID, OWNER_GID = 1026, 100  # kck9010:users - 컨테이너(root)가 만든 파일도 claude가 읽고 지울 수 있게

KSKILL_REPO = "NomaDamas/k-skill"
KSKILL_TARBALL = f"https://codeload.github.com/{KSKILL_REPO}/tar.gz/main"
KSKILL_MARKER = ".k-skill.json"
CATALOG_MAX_AGE = 6 * 3600

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_lock = threading.Lock()


def claude_dir() -> Path:
    return HOME / ".claude"


def skills_dir() -> Path:
    return claude_dir() / "skills"


def trash_dir() -> Path:
    return claude_dir() / "skills-trash"


def parse_frontmatter(text: str) -> dict:
    """SKILL.md 맨 위 --- 블록에서 name/description만 뽑는다 (yaml 의존성 없이)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            if val in ("|", ">", "|-", ">-"):
                val = ""
            out[key] = val.strip("'\"")
        elif key and line.startswith((" ", "\t")) and key in ("description",):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def _read_skill(path: Path) -> dict | None:
    md = path / "SKILL.md"
    try:
        fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace")[:20000])
    except OSError:
        return None
    desc = fm.get("description", "")
    return {"name": fm.get("name") or path.name, "dir": path.name, "description": desc, "desc_len": len(desc)}


def _host_to_container(p: str) -> Path:
    return HOME / p[len(HOST_HOME):].lstrip("/") if p.startswith(HOST_HOME) else Path(p)


def list_installed() -> dict:
    personal, synced, plugins = [], [], []
    sd = skills_dir()
    if sd.is_dir():
        for p in sorted(sd.iterdir()):
            if not p.is_dir():
                continue
            if p.name == "synced":
                # claude.ai 계정 스킬: synced/<계정id>/<스킬>/SKILL.md
                for acct in p.iterdir():
                    for s in sorted(acct.iterdir()) if acct.is_dir() else []:
                        info = s.is_dir() and _read_skill(s)
                        if info:
                            synced.append(info)
                continue
            info = _read_skill(p)
            if info:
                marker = p / KSKILL_MARKER
                if marker.exists():
                    try:
                        info["kskill"] = json.loads(marker.read_text())
                    except ValueError:
                        info["kskill"] = {}
                personal.append(info)

    try:
        installed = json.loads((claude_dir() / "plugins" / "installed_plugins.json").read_text())
    except (OSError, ValueError):
        installed = {}
    try:
        enabled = json.loads((claude_dir() / "settings.json").read_text()).get("enabledPlugins") or {}
    except (OSError, ValueError):
        enabled = {}
    for key, entries in (installed.get("plugins") or {}).items():
        entry = entries[0] if isinstance(entries, list) and entries else {}
        root = _host_to_container(entry.get("installPath", ""))
        skills = []
        if (root / "skills").is_dir():
            for s in sorted((root / "skills").iterdir()):
                info = s.is_dir() and _read_skill(s)
                if info:
                    skills.append(info)
        plugins.append({"key": key, "version": entry.get("version", ""), "enabled": bool(enabled.get(key)),
                        "skills": skills})
    return {"personal": personal, "synced": synced, "plugins": plugins}


# ── k-skill 카탈로그 ─────────────────────────────────────

def _cache_dir(data_dir: Path) -> Path:
    d = data_dir / "kskill"
    d.mkdir(parents=True, exist_ok=True)
    return d


def refresh_catalog(data_dir: Path) -> None:
    r = requests.get(KSKILL_TARBALL, timeout=60)
    r.raise_for_status()
    tmp = _cache_dir(data_dir) / "main.tar.gz.tmp"
    tmp.write_bytes(r.content)
    tmp.replace(_cache_dir(data_dir) / "main.tar.gz")
    (_cache_dir(data_dir) / "catalog.json").unlink(missing_ok=True)


def _tarball(data_dir: Path, refresh: bool = False) -> Path:
    tgz = _cache_dir(data_dir) / "main.tar.gz"
    if refresh or not tgz.exists():
        refresh_catalog(data_dir)
    return tgz


def _skill_members(tf: tarfile.TarFile) -> dict[str, dict[str, tarfile.TarInfo]]:
    """{스킬이름: {상대경로: TarInfo}} - 최상위 폴더 중 SKILL.md가 있는 것만."""
    by_skill: dict[str, dict[str, tarfile.TarInfo]] = {}
    for m in tf.getmembers():
        parts = m.name.split("/")
        if len(parts) < 3 or not NAME_RE.match(parts[1]):
            continue
        by_skill.setdefault(parts[1], {})["/".join(parts[2:])] = m
    return {k: v for k, v in by_skill.items() if "SKILL.md" in v}


def parse_ko_list(text: str) -> dict[str, tuple[str, str]]:
    """k-skills-list.md("## 분류" 아래 "- **이름** — 요약")를 {이름: (분류, 요약)}으로."""
    out, cat = {}, "기타"
    for line in text.splitlines():
        if line.startswith("## "):
            cat = line[3:].strip()
            continue
        m = re.match(r"^\s*[-*]\s+\*\*([a-z0-9-]+)\*\*\s*[—–-]+\s*(.+)$", line)
        if m:
            out[m.group(1)] = (cat, m.group(2).strip())
    return out


def catalog(data_dir: Path, refresh: bool = False) -> dict:
    with _lock:
        cache = _cache_dir(data_dir) / "catalog.json"
        tgz = _cache_dir(data_dir) / "main.tar.gz"
        stale = not tgz.exists() or time.time() - tgz.stat().st_mtime > CATALOG_MAX_AGE
        if not refresh and not stale and cache.exists():
            return json.loads(cache.read_text())
        tgz = _tarball(data_dir, refresh=refresh or stale)
        items = []
        with tarfile.open(tgz) as tf:
            ko = {}
            listing = next((m for m in tf.getmembers() if m.name.count("/") == 1 and m.name.endswith("/k-skills-list.md")), None)
            if listing:
                ko = parse_ko_list(tf.extractfile(listing).read().decode("utf-8", "replace"))
            for name, files in sorted(_skill_members(tf).items()):
                fm = parse_frontmatter(tf.extractfile(files["SKILL.md"]).read().decode("utf-8", "replace"))
                desc = fm.get("description", "")
                cat, summary = ko.get(name, ("기타", ""))
                items.append({"name": name, "description": desc, "desc_len": len(desc), "category": cat,
                              "summary": summary, "has_scripts": any(p.startswith("scripts/") for p in files)})
        out = {"fetched": int(tgz.stat().st_mtime), "skills": items}
        cache.write_text(json.dumps(out, ensure_ascii=False))
        return out


def _pinned_skill_md(name: str, skill_md: str, instruction: str | None, dest: Path) -> str:
    """npx로 지시문을 받아오는 껍데기 대신 저장소 원문을 고정해 넣은 SKILL.md."""
    if not instruction:
        return skill_md
    m = re.match(r"^---\s*\n.*?\n---\s*\n", skill_md, re.S)
    front = m.group(0) if m else f"---\nname: {name}\n---\n"
    host_path = f"{HOST_HOME}/{dest.relative_to(HOME)}"
    note = (f"\n> Media Router에서 k-skill 저장소 원문(instruction.md)을 고정해 설치한 스킬입니다.\n"
            f"> `npx -y @nomadamas/k-skill@0 instruct/update`로 지시문을 다시 받지 마세요.\n"
            f"> `npx -y @nomadamas/k-skill@0 exec {name} <파일> -- <인자>`가 나오면 가능하면 이 폴더"
            f"(`{host_path}`)의 같은 파일을 직접 실행하세요.\n\n")
    return front + note + instruction


def install(data_dir: Path, name: str) -> dict:
    if not NAME_RE.match(name or ""):
        raise ValueError("스킬 이름이 이상해요.")
    dest = skills_dir() / name
    with _lock:
        if dest.exists() and not (dest / KSKILL_MARKER).exists():
            raise ValueError(f"같은 이름의 스킬({name})이 이미 있어요. k-skill로 설치한 게 아니라 덮어쓰지 않았어요.")
        with tarfile.open(_tarball(data_dir)) as tf:
            files = _skill_members(tf).get(name)
            if not files:
                raise ValueError(f"k-skill에 {name} 스킬이 없어요.")
            tmp = skills_dir() / f".{name}.installing"
            shutil.rmtree(tmp, ignore_errors=True)
            for rel, m in files.items():
                if not (m.isfile() or m.isdir()) or ".." in rel.split("/"):
                    continue  # 링크·장치 파일·경로 탈출은 건너뛴다
                target = tmp / rel
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tf.extractfile(m).read())
                target.chmod(0o755 if m.mode & 0o111 else 0o644)
            instruction = None
            if "instruction.md" in files:
                instruction = tf.extractfile(files["instruction.md"]).read().decode("utf-8", "replace")
            skill_md = tf.extractfile(files["SKILL.md"]).read().decode("utf-8", "replace")
            (tmp / "SKILL.md").write_text(_pinned_skill_md(name, skill_md, instruction, dest), encoding="utf-8")
            fetched = int(Path(tf.name).stat().st_mtime)
        (tmp / KSKILL_MARKER).write_text(json.dumps({"repo": KSKILL_REPO, "fetched": fetched, "installed": int(time.time())}))
        for p in [tmp, *tmp.rglob("*")]:
            try:
                os.chown(p, OWNER_UID, OWNER_GID)
            except OSError:
                pass
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    return _read_skill(dest) or {"name": name}


def remove(name: str) -> str:
    """개인 스킬을 휴지통(~/.claude/skills-trash)으로 옮긴다. 영구 삭제는 하지 않는다."""
    if not NAME_RE.match(name or "") or name == "synced":
        raise ValueError("지울 수 없는 스킬이에요.")
    src = skills_dir() / name
    if not (src / "SKILL.md").exists():
        raise ValueError(f"{name} 스킬이 없어요.")
    with _lock:
        trash_dir().mkdir(parents=True, exist_ok=True)
        try:
            os.chown(trash_dir(), OWNER_UID, OWNER_GID)
        except OSError:
            pass
        dest = trash_dir() / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
        src.rename(dest)
    return str(dest.relative_to(HOME))
