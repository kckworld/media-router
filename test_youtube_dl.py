# -*- coding: utf-8 -*-
"""youtube_dl.py(유튜브 다운로드) 테스트. 실제 다운로드는 하지 않는다."""
from pathlib import Path

import pytest

import youtube_dl as yt


@pytest.mark.parametrize("text,expected", [
    ("https://youtu.be/kjjO1gNvcdI?si=abcDEF123", "kjjO1gNvcdI"),
    ("https://www.youtube.com/watch?v=kjjO1gNvcdI&t=30s", "kjjO1gNvcdI"),
    ("https://m.youtube.com/watch?feature=share&v=kjjO1gNvcdI", "kjjO1gNvcdI"),
    ("https://youtube.com/shorts/AbCdEfGhI_-?feature=share", "AbCdEfGhI_-"),
    ("https://music.youtube.com/watch?v=kjjO1gNvcdI&list=RDAMVM", "kjjO1gNvcdI"),
    ("불꽃야구2 Ep.22 보세요\nhttps://youtu.be/kjjO1gNvcdI", "kjjO1gNvcdI"),
    ("https://www.youtube.com/live/kjjO1gNvcdI", "kjjO1gNvcdI"),
    ("🎬 인코딩 시작", None),
    ("https://example.com/watch?v=kjjO1gNvcdI", None),
])
def test_extract_video_id(text, expected):
    assert yt.extract_video_id(text) == expected


def test_quality_suffix_matches_existing_downloader_name():
    # 기존 파일: "불꽃야구2(Burn to win) Ep.22 ¦ Studio C1 (1080p_30fps_H264-128kbit_AAC).mp4"
    assert yt.quality_suffix("1080", "30", "avc1.640028", "mp4a.40.2", "129.475") == "(1080p_30fps_H264-128kbit_AAC)"


def test_quality_suffix_other_codecs():
    assert yt.quality_suffix("720", "29.97", "vp09.00.40.08", "opus", "121") == "(720p_30fps_VP9-128kbit_OPUS)"
    assert yt.quality_suffix("1080", "NA", "av01.0.08M.08", "mp4a.40.2", "NA") == "(1080p_0fps_AV1-0kbit_AAC)"


def test_unique_path_keeps_existing(tmp_path):
    (tmp_path / "a.mp4").write_text("x")
    (tmp_path / "a (1).mp4").write_text("x")
    assert yt.unique_path(tmp_path / "a.mp4") == tmp_path / "a (2).mp4"
    (tmp_path / "a.ko.srt").write_text("x")
    assert yt.unique_path(tmp_path / "a.ko.srt") == tmp_path / "a (1).ko.srt"
    assert yt.unique_path(tmp_path / "b.mp4") == tmp_path / "b.mp4"


def test_build_command_video_and_audio():
    v = yt.build_command("video", "https://www.youtube.com/watch?v=kjjO1gNvcdI")
    assert "--no-playlist" in v and "--merge-output-format" in v and "ko" in v
    assert v[v.index("--replace-in-metadata") + 1:v.index("--replace-in-metadata") + 4] == ["title", r"\|", "¦"]
    a = yt.build_command("audio", "https://www.youtube.com/watch?v=kjjO1gNvcdI")
    assert "mp3" in a and "--write-subs" not in a


def _job(mode="video"):
    return {"id": "j1", "mode": mode, "url": "u", "title": ""}


def test_place_video_renames_with_suffix_and_subtitle(tmp_path, monkeypatch):
    monkeypatch.setattr(yt.os, "chown", lambda *a: None)
    work, drive = tmp_path / "work", tmp_path / "Drive"
    work.mkdir()
    (work / "불꽃야구2(Burn to win) Ep.22 ¦ Studio C1.mp4").write_text("v")
    (work / "불꽃야구2(Burn to win) Ep.22 ¦ Studio C1.ko.srt").write_text("s")
    # 같은 이름이 이미 Drive에 있으면 덮어쓰지 않는다
    drive.mkdir()
    existing = drive / "불꽃야구2(Burn to win) Ep.22 ¦ Studio C1 (1080p_30fps_H264-128kbit_AAC).mp4"
    existing.write_text("old")
    d = yt.Downloader.__new__(yt.Downloader)
    produced = [work / "불꽃야구2(Burn to win) Ep.22 ¦ Studio C1.mp4", work / "불꽃야구2(Burn to win) Ep.22 ¦ Studio C1.ko.srt"]
    info = {"height": "1080", "fps": "30", "vcodec": "avc1.640028", "acodec": "mp4a.40.2", "abr": "129.5"}
    final = d._place(_job(), produced, info, drive, work)
    names = [p.name for p in final]
    assert names == ["불꽃야구2(Burn to win) Ep.22 ¦ Studio C1 (1080p_30fps_H264-128kbit_AAC) (1).mp4",
                     "불꽃야구2(Burn to win) Ep.22 ¦ Studio C1 (1080p_30fps_H264-128kbit_AAC) (1).ko.srt"]
    assert existing.read_text() == "old"
    assert not list(work.iterdir())


def test_enqueue_rejects_bad_input_and_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(yt.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    d = yt.Downloader(tmp_path / "jobs.json")
    with pytest.raises(ValueError):
        d.enqueue("https://example.com", "video", "web")
    d.enqueue("https://youtu.be/kjjO1gNvcdI", "video", "web")
    with pytest.raises(ValueError):
        d.enqueue("https://www.youtube.com/watch?v=kjjO1gNvcdI", "video", "telegram")
    d.enqueue("https://youtu.be/kjjO1gNvcdI", "audio", "web")  # 음악은 별개 작업
    # 재시작하면 받던 작업은 실패로 바뀐다
    d2 = yt.Downloader(tmp_path / "jobs.json")
    assert {j["status"] for j in d2.jobs} == {"error"}
