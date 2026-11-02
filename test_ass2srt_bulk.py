# -*- coding: utf-8 -*-
"""내장 ASS 일괄 변환 (2026-11-02): 켜짐·밤 시간·재인코딩 대기 건너뛰기·한 번만"""
import json
from datetime import datetime

import ass2srt_bulk as b


def setup(tmp_path, monkeypatch, files):
    monkeypatch.setattr(b, "WORK", tmp_path)
    monkeypatch.setattr(b, "SCAN", tmp_path / "scan.tsv")
    monkeypatch.setattr(b, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(b, "ENABLED", tmp_path / "enabled")
    monkeypatch.setattr(b, "LOCK", tmp_path / ".lock")
    monkeypatch.setattr(b, "QUEUE", tmp_path / "queue.json")
    rows = []
    for name, ass in files:
        p = tmp_path / name
        p.write_bytes(b"x")
        rows.append(f"{p}\t1000000\t{ass}\t{ass + 1}\t0")
    (tmp_path / "scan.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    done = []
    monkeypatch.setattr(b.ass2srt, "convert_mkv", lambda p: done.append(p.name) or (True, "ok"))
    monkeypatch.setattr(b.ass2srt, "log", lambda m: None)
    return done


NIGHT = datetime(2026, 11, 3, 2, 0)


def test_off_or_daytime_does_nothing(tmp_path, monkeypatch):
    done = setup(tmp_path, monkeypatch, [("a.mkv", 1)])
    assert b.step(now=NIGHT) == 0 and not done                     # 꺼져 있음
    b.main(["x", "on"])
    assert b.step(now=datetime(2026, 11, 3, 14, 0)) == 0 and not done   # 낮
    assert b.status()["enabled"] and b.status()["total"] == 1


def test_converts_once_skips_reencode_pending_and_missing(tmp_path, monkeypatch):
    done = setup(tmp_path, monkeypatch, [("a.mkv", 1), ("b.mkv", 0), ("c.mkv", 2), ("d.mkv", 1)])
    (tmp_path / "queue.json").write_text(json.dumps({"items": [{"src": str(tmp_path / "c.mkv"), "status": "pending"}]}))
    (tmp_path / "d.mkv").unlink()
    b.main(["x", "on"])
    b.step(now=NIGHT)
    assert done == ["a.mkv"]                                        # b는 ASS 없음, c는 재인코딩 대기, d는 없음
    st = b.status()
    assert st["done"] == 1 and st["skipped"] == 1 and st["left"] == 1   # c만 남음(재인코딩 뒤 다시 봄)
    b.step(now=NIGHT)
    assert done == ["a.mkv"]                                        # 한 번 바꾼 건 다시 안 함
    b.main(["x", "off"])
    assert not b.status()["enabled"]
