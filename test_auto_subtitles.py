# -*- coding: utf-8 -*-
"""auto_subtitles.py(애니 자동 한국어 자막) 테스트. DeepL과 ffmpeg는 가짜로 바꿔서 돌린다."""
import os
import time
from pathlib import Path

import pytest

import auto_subtitles as a

EN_ASS = """[Script Info]
Title: English (US)
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: DCMain,Roboto Medium,26,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.3,0,2,20,20,23,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:02:44.03,0:02:48.52,DCMain,KOGORO,0,0,0,,I see. Someone's trying\\Nto kill your fiancée.
Dialogue: 0,0:02:53.13,0:02:54.90,DCMain,SHOTA,0,0,0,,{\\an8}Her name is {\\i1}Tojo Mayu{\\i0}.
Dialogue: 0,0:02:55.00,0:02:56.00,Sign,,0,0,0,,{\\p1}m 0 0 l 10 10{\\p0}
Dialogue: 0,0:02:57.00,0:02:58.00,DCMain,,0,0,0,,...
Dialogue: 0,0:02:59.00,0:03:00.00,DCMain,CONAN,0,0,0,,I see. Someone's trying\\Nto kill your fiancée.
"""


def fake_translate(texts):
    fake_translate.calls.append(list(texts))
    return ["[KO]" + t for t in texts]


fake_translate.calls = []


@pytest.fixture
def env(tmp_path, monkeypatch):
    folder = tmp_path / "명탐정 코난 (1996)"
    folder.mkdir()
    monkeypatch.setattr(a, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(a, "extract_english_ass", lambda v: EN_ASS)
    fake_translate.calls = []
    return folder


def video(folder, name, age_sec=3600):
    p = folder / name
    p.write_bytes(b"video")
    t = time.time() - age_sec
    os.utime(p, (t, t))
    return p


def run(folder, state, **kw):
    return a.process_folder(folder, state, key="", translate=fake_translate, **kw)


def test_translate_ass_keeps_timing_and_tags():
    out, chars = a.translate_ass(EN_ASS, "", "ctx", fake_translate)
    lines = [l for l in out.splitlines() if l.startswith("Dialogue:")]
    assert lines[0] == "Dialogue: 0,0:02:44.03,0:02:48.52,DCMain,KOGORO,0,0,0,,[KO]I see. Someone's trying\\Nto kill your fiancée."
    # 맨 앞 태그는 유지, 중간 태그는 빠짐
    assert lines[1] == "Dialogue: 0,0:02:53.13,0:02:54.90,DCMain,SHOTA,0,0,0,,{\\an8}[KO]Her name is Tojo Mayu."
    assert lines[2].endswith("{\\p1}m 0 0 l 10 10{\\p0}")      # 도형은 그대로
    assert lines[3].endswith(",,...")                            # 글자가 없으면 번역 안 함
    assert a.MARKER in out and "Title: 한국어 자동 번역 (DeepL)" in out
    assert chars > 0


def test_deepl_dedupes_identical_lines(monkeypatch):
    sent = []

    class Resp:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *e): pass

    def fake_urlopen(req, timeout=0):
        import json
        texts = json.loads(req.data)["text"]
        sent.extend(texts)
        assert req.full_url.startswith("https://api-free.deepl.com")
        return Resp(json.dumps({"translations": [{"text": "번역:" + t} for t in texts]}).encode())

    monkeypatch.setattr(a.urllib.request, "urlopen", fake_urlopen)
    out = a.deepl_translate(["A", "B", "A"], "abc:fx", "ctx")
    assert out == ["번역:A", "번역:B", "번역:A"] and sent == ["A", "B"]


def test_creates_korean_sub_for_new_episode(env):
    v = video(env, "Detective Conan.S01.E1217 [에피소드 1217].mkv")
    state = {}
    s = run(env, state)
    sub = env / "Detective Conan.S01.E1217 [에피소드 1217].ko.ass"
    assert s["created"] == 1 and sub.exists() and "[KO]" in sub.read_text(encoding="utf-8")
    rec = state[str(env) + "|1217"]
    assert rec["status"] == "auto" and rec["sha256"] == a.sha256(sub)
    assert (sub.stat().st_mode & 0o777) == 0o664      # Plex·Jellyfin이 읽을 수 있어야 함
    # 두 번째 실행은 아무것도 안 함
    assert run(env, state)["created"] == 0


@pytest.mark.parametrize("fan", ["X.E1217.ko.srt", "Detective Conan.S01.E1217 [t].ko.ass", "a.E1217.kor.smi"])
def test_skips_when_fan_sub_already_there(env, fan):
    video(env, "Detective Conan.S01.E1217 [t].mkv")
    (env / fan).write_text("fan", encoding="utf-8")
    state = {}
    assert run(env, state)["created"] == 0 and state == {}


def test_skips_old_and_just_moved_videos(env):
    video(env, "Detective Conan.S01.E1100.mkv", age_sec=30 * 86400)
    video(env, "Detective Conan.S01.E1218.mkv", age_sec=10)
    assert run(env, {})["created"] == 0


def test_fan_srt_arrives_removes_auto(env):
    video(env, "Detective Conan.S01.E1217 [t].mkv")
    state = {}
    run(env, state)
    (env / "Detective Conan.S01.E1217 [t].ko.srt").write_text("fan", encoding="utf-8")
    run(env, state)
    assert not (env / "Detective Conan.S01.E1217 [t].ko.ass").exists()
    assert (env / "Detective Conan.S01.E1217 [t].ko.srt").exists()
    assert state[str(env) + "|1217"]["status"] == "replaced"
    # 교체된 뒤 다시 만들지 않음 (팬 자막을 지워도)
    (env / "Detective Conan.S01.E1217 [t].ko.srt").unlink()
    assert run(env, state)["created"] == 0


def test_fan_ass_pushed_to_underscore_name_gets_cleaned(env):
    video(env, "Detective Conan.S01.E1217 [t].mkv")
    state = {}
    run(env, state)
    # media_router가 같은 이름이 있어서 "_1"을 붙여 옮긴 팬 자막
    (env / "Detective Conan.S01.E1217 [t].ko_1.ass").write_text("fan sub", encoding="utf-8")
    run(env, state)
    final = env / "Detective Conan.S01.E1217 [t].ko.ass"
    assert final.read_text(encoding="utf-8") == "fan sub"
    assert not (env / "Detective Conan.S01.E1217 [t].ko_1.ass").exists()
    assert state[str(env) + "|1217"]["status"] == "replaced"


def test_aniname_overwrite_counts_as_replaced(env):
    video(env, "Detective Conan.S01.E1217 [t].mkv")
    state = {}
    run(env, state)
    (env / "Detective Conan.S01.E1217 [t].ko.ass").write_text("fan by aniname rename", encoding="utf-8")
    run(env, state)
    assert (env / "Detective Conan.S01.E1217 [t].ko.ass").read_text(encoding="utf-8") == "fan by aniname rename"
    assert state[str(env) + "|1217"]["status"] == "replaced"


def test_follows_aniname_title_rename(env):
    v = video(env, "Detective Conan.S01.E1217 [에피소드 1217].mkv")
    state = {}
    run(env, state)
    old = env / "Detective Conan.S01.E1217 [에피소드 1217].ko.ass"
    new = env / "Detective Conan.S01.E1217 [진짜 제목].ko.ass"
    old.rename(new)
    v.rename(env / "Detective Conan.S01.E1217 [진짜 제목].mkv")
    run(env, state)
    rec = state[str(env) + "|1217"]
    assert rec["status"] == "auto" and rec["path"] == str(new) and new.exists()


def test_no_subtitle_track_recorded_once(env, monkeypatch):
    video(env, "One Piece.S23.E1181 [t].mkv")
    calls = []
    monkeypatch.setattr(a, "extract_english_ass", lambda v: calls.append(v) or None)
    state = {}
    s = run(env, state)
    assert s["skipped_no_track"] == 1 and state[str(env) + "|1181"]["status"] == "no_track"
    run(env, state)
    assert len(calls) == 1


def test_episode_of():
    assert a.episode_of("Detective Conan.S01.E1217 [x].mkv") == "1217"
    assert a.episode_of("One Piece.S23.E1180 [x].ko.srt") == "1180"
    assert a.episode_of("no episode here.mkv") is None
