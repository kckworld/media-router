# -*- coding: utf-8 -*-
"""재인코딩 대기 목록 순서: queue.json order=drama_first면 드라마 D1→D2→D3가 일괄 예능보다 먼저 (2026-11-02)"""
import json

import reencode_ctl
from test_reencode import re_env  # noqa: F401  (fixture)


def _items():
    def it(i, profile="variety", tier=3):
        return {"id": i, "src": f"/volume1/video/x/{i}.mp4", "size": 2 ** 30, "dur": 60, "status": "pending",
                "profile": profile, "tier": tier}
    return [it("0101"), it("0102"), it("1539", "drama", "D2"), it("1540", "drama", "D1"), it("1541", "drama", "D3")]


def test_waiting_list_variety_first_by_default(re_env, monkeypatch, tmp_path):
    p = tmp_path / "q.json"
    p.write_text(json.dumps({"items": _items()}), encoding="utf-8")
    monkeypatch.setattr(reencode_ctl, "QUEUE", p)
    st = reencode_ctl.state()
    assert [r["id"] for r in st["waiting_list"]] == ["0101", "0102", "1539", "1540", "1541"]
    assert st["order"] == "variety_first"


def test_waiting_list_drama_first(re_env, monkeypatch, tmp_path):
    p = tmp_path / "q.json"
    p.write_text(json.dumps({"order": "drama_first", "items": _items()}), encoding="utf-8")
    monkeypatch.setattr(reencode_ctl, "QUEUE", p)
    st = reencode_ctl.state()
    assert [r["id"] for r in st["waiting_list"]] == ["1540", "1539", "1541", "0101", "0102"]
    assert st["order"] == "drama_first"
