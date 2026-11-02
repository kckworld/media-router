# -*- coding: utf-8 -*-
"""ASS → SRT (2026-11-02): 변환 규칙, 외부 파일, mkv 다시 묶기, media_router 이동 뒤 자동 변환"""
import shutil
import subprocess
from pathlib import Path

import pytest

import ass2srt as a

HEAD = "[Script Info]\nTitle: t\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"


def ass(*dialogues):
    return HEAD + "".join(f"Dialogue: {d}\n" for d in dialogues)


def test_tags_drawing_comments_and_newlines():
    text = ass("0,0:00:01.00,0:00:02.50,D,,0,0,0,,{\\an8\\pos(1,2)}안녕\\N{\\c&HFF&}하세요",
               "0,0:00:03.00,0:00:04.00,D,,0,0,0,,{\\p1}m 0 0 l 10 10{\\p0}",
               "0,0:00:05.00,0:00:06.00,D,,0,0,0,,<font face=\"굴림\" color=pink>노래</font><script>x</script>") + \
        "Comment: 0,0:00:07.00,0:00:08.00,D,,0,0,0,,주석\n"
    srt, n = a.ass_to_srt(text)
    assert n == 2
    assert srt.startswith("1\n00:00:01,000 --> 00:00:02,500\n안녕\n하세요\n")
    assert '<font color=pink>노래</font>x' in srt and "face" not in srt and "주석" not in srt and "\\p1" not in srt


def test_merges_karaoke_growth_shadow_layers_and_continuations():
    text = ass("0,0:00:10.00,0:00:10.50,D,,0,0,0,,W", "0,0:00:10.50,0:00:11.00,D,,0,0,0,,We",
               "0,0:00:11.00,0:00:12.00,D,,0,0,0,,We Are",
               "0,0:00:20.00,0:00:22.00,D,,0,0,0,,<font color=black>그림자</font>",
               "1,0:00:20.00,0:00:22.00,D,,0,0,0,,<font color=white>그림자</font>",
               "0,0:00:30.00,0:00:31.00,D,,0,0,0,,이어짐", "0,0:00:31.00,0:00:32.00,D,,0,0,0,,이어짐")
    cues = a.merge_cues(a.parse_ass(text))
    assert cues == [(10000, 12000, "We Are"), (20000, 22000, "<font color=white>그림자</font>"), (30000, 32000, "이어짐")]


def test_decode_cp949_and_utf16():
    assert a.decode("한글".encode("cp949")) == "한글"
    assert a.decode("한글".encode("utf-16")) == "한글"
    assert a.decode("﻿한글".encode("utf-8")) == "한글"


def test_convert_file_keeps_language_tag_never_overwrites(tmp_path):
    f = tmp_path / "v" / "Show - 01.ko.ass"
    f.parent.mkdir()
    f.write_bytes(ass("0,0:00:01.00,0:00:02.00,D,,0,0,0,,대사").encode("cp949"))
    ok, msg = a.convert_file(f, hold=tmp_path / "hold")
    assert ok and not f.exists() and (tmp_path / "v" / "Show - 01.ko.srt").read_text(encoding="utf-8").endswith("대사\n")
    assert (tmp_path / "hold" / "Show - 01.ko.ass").exists()                 # 원본은 보관
    g = tmp_path / "v" / "Other.ass"
    g.write_text(ass("0,0:00:01.00,0:00:02.00,D,,0,0,0,,x"), encoding="utf-8")
    (tmp_path / "v" / "Other.srt").write_text("기존", encoding="utf-8")
    ok, msg = a.convert_file(g, hold=tmp_path / "hold")
    assert not ok and "이미 있음" in msg and g.exists() and (tmp_path / "v" / "Other.srt").read_text(encoding="utf-8") == "기존"
    empty = tmp_path / "v" / "Empty.ass"
    empty.write_text(HEAD, encoding="utf-8")
    assert not a.convert_file(empty, hold=tmp_path / "hold")[0] and empty.exists()


FF = shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"


@pytest.mark.skipif(not Path(FF).exists(), reason="ffmpeg 없음")
def test_convert_mkv_replaces_only_ass_tracks(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "FFMPEG", FF)
    (tmp_path / "s.ass").write_text(ass("0,0:00:00.50,0:00:01.50,D,,0,0,0,,{\\an8}첫 줄",
                                        "0,0:00:02.00,0:00:02.80,D,,0,0,0,,둘째"), encoding="utf-8")
    (tmp_path / "e.srt").write_text("1\n00:00:00,500 --> 00:00:01,000\nhi\n", encoding="utf-8")
    v = tmp_path / "lib" / "ep.mkv"
    v.parent.mkdir()
    subprocess.run([FF, "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=3",
                    "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-i", str(tmp_path / "s.ass"), "-i", str(tmp_path / "e.srt"),
                    "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-t", "3", "-c:v", "mpeg4", "-c:a", "aac",
                    "-c:s:0", "ass", "-c:s:1", "srt", "-metadata:s:s:0", "language=kor", "-metadata:s:s:0", "title=한국어",
                    "-metadata:s:s:1", "language=eng", "-disposition:s:0", "default", str(v)], check=True)
    assert a.has_embedded_ass(v)
    ok, msg = a.convert_mkv(v, hold=tmp_path / "hold")
    assert ok, msg
    st = a.streams(v)
    subs = [s for s in st if s["type"] == "subtitle"]
    assert [(s["codec"], s["lang"]) for s in subs] == [("subrip", "kor"), ("subrip", "eng")]
    assert subs[0]["title"] == "한국어" and subs[0]["default"] and not subs[1]["default"]
    assert sum(1 for s in st if s["type"] in ("video", "audio")) == 2
    assert (tmp_path / "hold" / "ep.mkv").exists() and not list(v.parent.glob(".*"))   # 원본 보관, 임시 파일 없음
    out = subprocess.run([FF, "-v", "error", "-i", str(v), "-map", "0:s:0", "-f", "srt", "-"], capture_output=True, text=True).stdout
    assert "첫 줄" in out and "an8" not in out
    assert a.convert_mkv(v)[1] == "내장 ASS 없음"
    # discard: 원본 보관 없이 바로 교체(재인코딩 결과물용)
    w = tmp_path / "hold" / "ep.mkv"
    assert a.convert_mkv(w, discard=True)[0] and not a.has_embedded_ass(w)


def test_media_router_converts_after_move(tmp_path, monkeypatch):
    import media_router as mr
    calls, sent = [], []
    monkeypatch.setattr(mr.ass2srt, "convert_file", lambda p: calls.append(("file", p.name)) or (True, "1줄"))
    monkeypatch.setattr(mr.ass2srt, "convert_mkv", lambda p: calls.append(("mkv", p.name)) or (False, "실패 이유"))
    monkeypatch.setattr(mr.ass2srt, "has_embedded_ass", lambda p: p.name == "b.mkv")
    monkeypatch.setattr(mr, "tg_send", lambda cfg, text, *a, **k: sent.append(text))
    (tmp_path / "x.ko.srt").write_text("기존", encoding="utf-8")
    for name in ("a.ko.ass", "x.ko.ass", "b.mkv", "c.mkv", "d.mp4"):
        mr.convert_ass_after_move({}, tmp_path / name)
    assert calls == [("file", "a.ko.ass"), ("mkv", "b.mkv")]               # 같은 이름 SRT 있으면 건너뜀
    assert len(sent) == 1 and "b.mkv" in sent[0] and "실패 이유" in sent[0]
