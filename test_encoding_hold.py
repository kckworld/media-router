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
