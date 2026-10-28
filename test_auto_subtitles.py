# -*- coding: utf-8 -*-
"""auto_subtitles.py(애니 자동 한국어 자막) 테스트. DeepL과 ffmpeg는 가짜로 바꿔서 돌린다."""
import os
import time
from pathlib import Path

import pytest

import auto_subtitles as a

# 테스트에선 실제 agy를 부르지 않는다
a.AGY = Path("/nonexistent/agy")

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
    monkeypatch.setattr(a, "extract_english_sub", lambda v: ("ass", EN_ASS))
    monkeypatch.setattr(a, "MAX_PER_RUN", 99)
    monkeypatch.setattr(a, "AGY", Path("/nonexistent/agy"))
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
    monkeypatch.setattr(a, "extract_english_sub", lambda v: calls.append(v) or None)
    state = {}
    s = run(env, state)
    assert s["skipped_no_track"] == 1 and state[str(env) + "|1181"]["status"] == "no_track"
    run(env, state)
    assert len(calls) == 1


def test_episode_of():
    assert a.episode_of("Detective Conan.S01.E1217 [x].mkv") == "1217"
    assert a.episode_of("One Piece.S23.E1180 [x].ko.srt") == "1180"
    assert a.episode_of("no episode here.mkv") is None
    assert a.episode_of("[Gecko] Chiikawa - S01E382 [IQ.WEB-DL 1080P AVC, AAC, M-SUB][E2288721].mkv") == "382"
    assert a.episode_of("[Gecko] Chiikawa - S01E7 [VIU.WEB-DL].mkv") == "7"
    assert a.episode_of("Chiikawa - S01E383.1080p.TVER.WEB-DL.JPN.AAC2.0.H.264.MSubs-ToonsHub.mkv") == "383"


IQ_STREAMS = """Input #0, matroska,webm, from 'x.mkv':
    Stream #0:0: Video: h264, none, 1920x1080
    Stream #0:1(jpn): Audio: aac, 48000 Hz, stereo (default)
    Stream #0:2(eng): Subtitle: subrip (default)
    Stream #0:3(chi): Subtitle: subrip
"""


def test_pick_english_track():
    tracks = a.SUB_STREAM_RE.findall(IQ_STREAMS)
    assert tracks == [("eng", "subrip"), ("chi", "subrip")]
    assert a.pick_english([("chi", "subrip"), ("eng", "subrip")]) == 1
    assert a.pick_english([("chi", "subrip")]) is None          # VIU판 (중국어만)
    assert a.pick_english([("", "ass")]) == 0                    # 언어 표시 없음 → 첫 트랙
    assert a.pick_english([]) is None


def test_pick_source_falls_back_to_chinese():
    assert a.pick_source([("chi", "subrip"), ("eng", "subrip")]) == (1, "EN")
    assert a.pick_source([("chi", "subrip")]) == (0, "ZH")          # 먼작귀 VIU판
    assert a.pick_source([("tha", "subrip")]) is None


ZH_SRT = """1
00:00:01,000 --> 00:00:02,000
吉伊卡哇

2
00:00:03,000 --> 00:00:04,000
(Chiikawa)
"""


def test_translate_srt_from_chinese(monkeypatch):
    seen = {}
    monkeypatch.setattr(a, "deepl_translate", lambda texts, key, ctx, translate=None, source="EN": seen.update(t=texts, s=source) or ["치이카와"] * len(texts))
    out, chars = a.translate_srt(ZH_SRT, "k", "ctx", source="ZH")
    assert seen == {"t": ["吉伊卡哇"], "s": "ZH"}                  # 한자가 없는 줄은 보내지 않음
    assert out.startswith("1\n00:00:01,000 --> 00:00:02,000\n치이카와\n\n2\n") and chars == 4


def test_chinese_only_video_gets_ko_srt(tmp_path, monkeypatch):
    folder = tmp_path / "먼작귀 (2022)" / "Season 1"
    folder.mkdir(parents=True)
    monkeypatch.setattr(a, "extract_english_sub", lambda v: ("srt", ZH_SRT, "ZH"))
    monkeypatch.setattr(a, "deepl_translate", lambda texts, key, ctx, translate=None, source="EN": ["치이카와"] * len(texts))
    v = video(folder, "[Gecko] Chiikawa - S01E384 [VIU.WEB-DL 1080P AVC, AAC, SUB][X].mkv")
    state = {}
    a.process_folder(folder, state, key="k")
    assert (folder / (v.stem + ".ko.srt")).exists() and state[str(folder) + "|384"]["source"] == "ZH"


EN_SRT = """1
00:00:01,000 --> 00:00:02,500
<i>Hello there!</i>

2
00:00:03,000 --> 00:00:04,000
♪

3
00:00:05,000 --> 00:00:06,000
Chiikawa,
let's go.
"""


def test_translate_srt():
    out, chars = a.translate_srt(EN_SRT, "", "ctx", fake_translate)
    assert out == ("1\n00:00:01,000 --> 00:00:02,500\n[KO]Hello there!\n\n"
                   "2\n00:00:03,000 --> 00:00:04,000\n♪\n\n"
                   "3\n00:00:05,000 --> 00:00:06,000\n[KO]Chiikawa,\nlet's go.\n")
    assert chars > 0


def test_season_folder_makes_ko_srt_with_show_context(tmp_path, monkeypatch):
    folder = tmp_path / "먼작귀 (2022)" / "Season 1"
    folder.mkdir(parents=True)
    monkeypatch.setattr(a, "extract_english_sub", lambda v: ("srt", EN_SRT))
    ctx = []
    monkeypatch.setattr(a, "deepl_translate", lambda texts, key, context, translate=None, source="EN": ctx.append(context) or ["번역"] * len(texts))
    v = video(folder, "Chiikawa - S01E383.1080p.TVER.WEB-DL.JPN.AAC2.0.H.264.MSubs-ToonsHub.mkv")
    state = {}
    a.process_folder(folder, state, key="k")
    assert (folder / (v.stem + ".ko.srt")).exists()
    assert "먼작귀 (2022)" in ctx[0]
    assert state[str(folder) + "|383"]["status"] == "auto"
    # 이미 한국어 자막이 있는 회차(미디어 정리 세션이 만든 382화 등)는 건드리지 않음
    v2 = video(folder, "[Gecko] Chiikawa - S01E382 [IQ.WEB-DL][E2288721].mkv")
    (folder / (v2.stem + ".ko.srt")).write_text("수동", encoding="utf-8")
    a.process_folder(folder, state, key="k")
    assert str(folder) + "|382" not in state


class FakeRun:
    """agy 대신: 입력 파일(번호<TAB>화자<TAB>대사)을 읽어 '번호<TAB>[KO]대사'를 돌려준다. drop이면 마지막 줄을 뺀다."""

    def __init__(self, drop_when_over=None):
        self.calls, self.drop_when_over = [], drop_when_over

    def __call__(self, cmd, cwd=None, **kw):
        rows = (Path(cwd) / "sub.tsv").read_text(encoding="utf-8").splitlines()
        self.calls.append(rows)
        if self.drop_when_over and len(rows) > self.drop_when_over:
            rows = rows[:-1]
        out = "생각 중...\n" + "".join(f"{r.split(chr(9))[0]}\t[KO]{r.split(chr(9))[2]}\n" for r in rows)
        return type("R", (), {"stdout": out.encode(), "returncode": 0})()


def test_agy_chunk_roundtrip_keeps_newlines_and_speakers():
    run = FakeRun()
    out = a.agy_translate_chunk(["Hi\nthere", "Bye"], ["RAN", ""], "명탐정 코난 (1996)", "EN", run)
    assert out == ["[KO]Hi\nthere", "[KO]Bye"]
    assert run.calls[0] == ["1\tRAN\tHi\\Nthere", "2\t-\tBye"]


def test_agy_missing_line_retries_in_halves(monkeypatch):
    monkeypatch.setattr(a, "AGY_CHUNK", 4)
    run = FakeRun(drop_when_over=2)           # 4줄 덩어리는 한 줄을 빼먹고, 2줄씩은 제대로
    out = a.agy_translate([f"line {i}" for i in range(1, 5)], [""] * 4, "코난", "EN", run)
    assert out == [f"[KO]line {i}" for i in range(1, 5)] and [len(c) for c in run.calls] == [4, 2, 2]
    run = FakeRun(drop_when_over=1)
    with pytest.raises(a.AgyError):
        a.agy_translate(["a", "b", "c", "d"], [""] * 4, "코난", "EN", run)


def test_auto_translator_prefers_agy_then_deepl(monkeypatch):
    monkeypatch.setattr(a, "agy_available", lambda: True)
    monkeypatch.setattr(a, "agy_translate", lambda texts, speakers, show, source: ["agy:" + t for t in texts])
    tr = a.AutoTranslator("k:fx", "ctx", "코난", "EN")
    ko, _ = a.translate_ass(EN_ASS, "k:fx", "ctx", tr)
    assert tr.engine == "agy" and tr.speakers == ["KOGORO", "SHOTA", "CONAN"] and "agy:Her name is Tojo Mayu." in ko

    def boom(*args):
        raise a.AgyError("줄 수 안 맞음")
    monkeypatch.setattr(a, "agy_translate", boom)
    monkeypatch.setattr(a, "_deepl_http", lambda texts, key, ctx, source="EN": ["dl:" + t for t in texts])
    tr = a.AutoTranslator("k:fx", "ctx", "코난", "EN")
    assert tr(["x"]) == ["dl:x"] and tr.engine == "deepl"
    with pytest.raises(a.AgyError):
        a.AutoTranslator("", "ctx", "코난", "EN")(["x"])          # DeepL 키도 없으면 실패


def test_agy_episode_labels_engine_limits_per_run_and_retries_failures(env, monkeypatch):
    monkeypatch.setattr(a, "MAX_PER_RUN", 1)
    monkeypatch.setattr(a, "agy_available", lambda: True)
    monkeypatch.setattr(a, "agy_translate", lambda texts, speakers, show, source: ["agy:" + t for t in texts])
    video(env, "Detective Conan.S01.E1218 [t].mkv")
    video(env, "Detective Conan.S01.E1219 [t].mkv")
    state = {}
    s = a.process_folder(env, state, key="")
    assert s["created"] == 1                                         # 한 번에 한 편
    rec = state[str(env) + "|1218"]
    assert rec["engine"] == "agy" and str(env) + "|1219" not in state
    text = Path(rec["path"]).read_text(encoding="utf-8")
    assert "Title: 한국어 자동 번역 (Gemini)" in text and "영어 자막 → Gemini)" in text

    def boom(*args):
        raise a.AgyError("시간 초과")
    monkeypatch.setattr(a, "agy_translate", boom)
    for n in (1, 2, 3):
        a.process_folder(env, state, key="")
        assert state[str(env) + "|1219"] == {**state[str(env) + "|1219"], "status": "fail", "attempts": n}
    calls = []
    monkeypatch.setattr(a, "agy_translate", lambda *x: calls.append(1) or [])
    a.process_folder(env, state, key="")
    assert not calls                                                  # 3번 실패하면 더 안 함
