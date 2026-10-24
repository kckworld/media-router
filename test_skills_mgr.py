"""스킬 관리(skills_mgr.py, 설정 > Claude 스킬) 테스트. 네트워크 없이 가짜 k-skill 압축본으로."""
import io
import json
import tarfile

import pytest

import skills_mgr


def _add(tf, path, text, mode=0o644):
    data = text.encode()
    info = tarfile.TarInfo(path)
    info.size, info.mode = len(data), mode
    tf.addfile(info, io.BytesIO(data))


@pytest.fixture
def env(tmp_path, monkeypatch):
    home, data = tmp_path / "home", tmp_path / "data"
    (home / ".claude/skills/wiki").mkdir(parents=True)
    (home / ".claude/skills/wiki/SKILL.md").write_text("---\nname: wiki\ndescription: 내 위키\n---\n")
    (data / "kskill").mkdir(parents=True)
    with tarfile.open(data / "kskill/main.tar.gz", "w:gz") as tf:
        _add(tf, "k-skill-main/k-skills-list.md", "# 목록\n\n## 생활 · 기타\n- **delivery-tracking** — 택배 조회\n")
        _add(tf, "k-skill-main/delivery-tracking/SKILL.md",
             "---\nname: delivery-tracking\ndescription: Track parcels\n---\n\nRun `npx -y @nomadamas/k-skill@0 instruct delivery-tracking`\n")
        _add(tf, "k-skill-main/delivery-tracking/instruction.md", "# 택배\n원문 지시\n")
        _add(tf, "k-skill-main/delivery-tracking/scripts/track.py", "print(1)\n", 0o755)
        _add(tf, "k-skill-main/delivery-tracking/../../evil.txt", "x")
        _add(tf, "k-skill-main/wiki/SKILL.md", "---\nname: wiki\ndescription: 남의 위키\n---\n")
        _add(tf, "k-skill-main/docs/README.md", "not a skill")
    monkeypatch.setattr(skills_mgr, "HOME", home)
    monkeypatch.setattr(skills_mgr, "HOST_HOME", "/var/services/homes/kck9010")
    return home, data


def test_catalog_uses_korean_list(env):
    _, data = env
    c = skills_mgr.catalog(data)
    names = {s["name"]: s for s in c["skills"]}
    assert set(names) == {"delivery-tracking", "wiki"}
    assert names["delivery-tracking"]["category"] == "생활 · 기타"
    assert names["delivery-tracking"]["summary"] == "택배 조회"
    assert names["delivery-tracking"]["has_scripts"] is True


def test_install_pins_instruction_instead_of_npx(env):
    home, data = env
    skills_mgr.install(data, "delivery-tracking")
    d = home / ".claude/skills/delivery-tracking"
    md = (d / "SKILL.md").read_text()
    assert md.startswith("---\nname: delivery-tracking")
    assert "원문 지시" in md and "instruct delivery-tracking`\n" not in md
    assert (d / "scripts/track.py").stat().st_mode & 0o111
    assert json.loads((d / skills_mgr.KSKILL_MARKER).read_text())["repo"] == skills_mgr.KSKILL_REPO
    assert not (home / ".claude/evil.txt").exists() and not (home / "evil.txt").exists()
    # k-skill로 설치한 건 다시 설치(업데이트)할 수 있다
    skills_mgr.install(data, "delivery-tracking")
    inst = skills_mgr.list_installed()
    assert {s["name"]: bool(s.get("kskill")) for s in inst["personal"]} == {"delivery-tracking": True, "wiki": False}


def test_install_never_overwrites_own_skill(env):
    home, data = env
    with pytest.raises(ValueError):
        skills_mgr.install(data, "wiki")
    assert "내 위키" in (home / ".claude/skills/wiki/SKILL.md").read_text()


@pytest.mark.parametrize("name", ["../x", "a/b", "", "synced", "UPPER"])
def test_bad_names(env, name):
    _, data = env
    with pytest.raises(ValueError):
        skills_mgr.install(data, name)
    with pytest.raises(ValueError):
        skills_mgr.remove(name)


def test_remove_moves_to_trash(env):
    home, _ = env
    rel = skills_mgr.remove("wiki")
    assert not (home / ".claude/skills/wiki").exists()
    assert (home / rel / "SKILL.md").exists() and rel.startswith(".claude/skills-trash/wiki-")


def test_list_installed_reads_plugins_and_synced(env):
    home, _ = env
    c = home / ".claude"
    (c / "skills/synced/acct/pdf").mkdir(parents=True)
    (c / "skills/synced/acct/pdf/SKILL.md").write_text("---\nname: pdf\ndescription: PDF\n---\n")
    plug = c / "plugins/cache/omc/1.0/skills/plan"
    plug.mkdir(parents=True)
    (plug / "SKILL.md").write_text("---\nname: plan\ndescription: >\n  여러 줄\n  설명\n---\n")
    (c / "plugins/installed_plugins.json").write_text(json.dumps({"plugins": {"omc@omc": [
        {"version": "1.0", "installPath": "/var/services/homes/kck9010/.claude/plugins/cache/omc/1.0"}]}}))
    (c / "settings.json").write_text(json.dumps({"enabledPlugins": {"omc@omc": True}}))
    inst = skills_mgr.list_installed()
    assert [s["name"] for s in inst["synced"]] == ["pdf"]
    assert inst["plugins"][0]["enabled"] is True
    assert inst["plugins"][0]["skills"][0]["description"] == "여러 줄 설명"


def test_viewer_cannot_touch_skills(env, monkeypatch):
    import web_admin
    monkeypatch.setattr(web_admin, "session_role", lambda req: "viewer")
    c = web_admin.app.test_client()
    assert c.post("/api/skills/install", json={"name": "delivery-tracking"}).status_code == 403
    assert c.get("/api/skills").status_code == 403
    assert not (env[0] / ".claude/skills/delivery-tracking").exists()
