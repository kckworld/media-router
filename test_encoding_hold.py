# -*- coding: utf-8 -*-
"""720p가 아닌 예능 → 재인코딩 new 폴더로 보류 (Drive/인코딩 → EncodeAll 흐름 대신, 2026-10-27)"""
import media_router as mr


def test_hold_moves_to_reencode_new_and_kicks_feed(monkeypatch, tmp_path):
    hold = tmp_path / "재인코딩" / "new"
    monkeypatch.setattr(mr, "ENCODING_HOLD_DIR", hold)
    script = tmp_path / "reencode.py"
    script.write_text("")
    monkeypatch.setattr(mr, "REENCODE_PY", script)
    monkeypatch.setattr(mr, "_held_any", False)
    sent, runs = [], []
    monkeypatch.setattr(mr, "tg_send", lambda cfg, text: sent.append(text))
    monkeypatch.setattr(mr.subprocess, "run", lambda args, **kw: runs.append(args))
    mr.kick_reencode_feed()
    assert runs == []                                   # 보류한 게 없으면 안 부름
    f = tmp_path / "런닝맨.E826.261101.1080p.mkv"
    f.write_text("v")
    (hold).mkdir(parents=True)
    (hold / f.name).write_text("old")                  # 같은 이름이 있으면 _1
    dst = mr.move_to_encoding_hold({}, f, "1080p (720p 아님)")
    assert dst == hold / "런닝맨.E826.261101.1080p_1.mkv" and dst.exists() and not f.exists()
    assert sent[0].startswith("🆕 인코딩 대기열에 넣었어요: 런닝맨.E826")
    mr.kick_reencode_feed()
    assert runs[0][-2:] == [str(script), "run"]


FFMPEG_OUT = """  Duration: 01:00:00.00, start: 0.000000, bitrate: 8974 kb/s
    Stream #0:0: Video: {codec}, yuv420p(tv, bt709), 1920x1080, 23.98 fps
{audio}"""


def _drama_env(monkeypatch, tmp_path, codec="h264", audio=1, size_mbps=9.0):
    hold = tmp_path / "재인코딩" / "new"
    monkeypatch.setattr(mr, "ENCODING_HOLD_DIR", hold)
    monkeypatch.setattr(mr, "ENCODED_NAMES", tmp_path / "encoded_names.txt")
    monkeypatch.setattr(mr, "_held_any", False)
    sent = []
    monkeypatch.setattr(mr, "tg_send", lambda cfg, text: sent.append(text))
    err = FFMPEG_OUT.format(codec=codec, audio="".join(f"    Stream #0:{i + 1}(kor): Audio: aac, 48000 Hz, stereo\n" for i in range(audio)))
    monkeypatch.setattr(mr.subprocess, "run", lambda args, **kw: type("R", (), {"stderr": err})())
    f = tmp_path / "굿보이.E03.mkv"
    with open(f, "wb") as fh:                       # 길이 1시간 → 크기로 비트레이트를 맞춘다
        fh.truncate(int(size_mbps * 1e6 / 8 * 3600))
    return hold, sent, f


def test_drama_over_5mbps_goes_to_new_drama(monkeypatch, tmp_path):
    hold, sent, f = _drama_env(monkeypatch, tmp_path)
    info = mr.probe_video_stream(f)
    assert info["duration"] == 3600 and info["audio"] == 1
    assert mr.check_drama_encoding({}, f, f.name)
    assert (hold / "drama" / f.name).exists() and not f.exists() and mr._held_any
    assert sent == ["🆕 드라마 인코딩 대기열에 넣었어요: 굿보이.E03.mkv (9.0Mbps)"]


def test_drama_kept_when_low_bitrate_av1_two_audio_or_already_encoded(monkeypatch, tmp_path):
    for kw in ({"size_mbps": 4.5}, {"codec": "av1"}, {"audio": 2}):
        hold, sent, f = _drama_env(monkeypatch, tmp_path, **kw)
        assert not mr.check_drama_encoding({}, f, f.name) and f.exists() and not sent, kw
    hold, sent, f = _drama_env(monkeypatch, tmp_path)
    (tmp_path / "encoded_names.txt").write_text("다른.mp4\n굿보이.E03.mp4\n", encoding="utf-8")
    assert not mr.check_drama_encoding({}, f, "굿보이.E03.mp4") and f.exists()    # 이름을 다듬기 전 이름으로도 확인
    sub = tmp_path / "굿보이.E03.ko.srt"
    sub.write_text("1")
    assert not mr.check_drama_encoding({}, sub, sub.name)                          # 자막은 그대로


def test_variety_skips_already_encoded(monkeypatch, tmp_path):
    hold, sent, f = _drama_env(monkeypatch, tmp_path)            # 1080p → 원래는 보류 대상
    (tmp_path / "encoded_names.txt").write_text(f.name + "\n", encoding="utf-8")
    assert not mr.check_variety_encoding({}, f, "", f.name) and f.exists()
    (tmp_path / "encoded_names.txt").write_text("", encoding="utf-8")
    assert mr.check_variety_encoding({}, f, "", f.name) and (hold / f.name).exists()
