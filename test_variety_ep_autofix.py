# -*- coding: utf-8 -*-
"""예능 회차 번호 자동 고침 (2026-11-01 사용자 승인): 직전 회차 7일 뒤 + 번호가 정확히 1 큼"""
import media_router as mr


def _touch(path, size):
    path.write_bytes(b"x" * size)
    return path


def test_variety_episode_autofix_rules(tmp_path):
    _touch(tmp_path / "런닝맨.E824.261025.mp4", 1)
    fix = mr.variety_episode_autofix
    assert fix(tmp_path, "런닝맨.E826.261101.720p-NEXT.mp4") == "런닝맨.E825.261101.720p-NEXT.mp4"
    assert fix(tmp_path, "런닝맨.e826.261101.mp4") == "런닝맨.e825.261101.mp4"
    assert fix(tmp_path, "런닝맨.E825.261101.mp4") is None            # 이미 맞음
    assert fix(tmp_path, "런닝맨.E827.261101.mp4") is None            # 차이 2 이상
    assert fix(tmp_path, "런닝맨.E826.261108.mp4") is None            # 14일 뒤(결방 낀 주)
    assert fix(tmp_path, "런닝맨.E826.261102.mp4") is None            # 7일이 아님
    assert fix(tmp_path, "나 혼자 산다.E701.261101.mp4") is None      # 직전 회차 없음
    assert fix(tmp_path, "런닝맨.E284.바캉스 레이스.130630.mp4") is None
    _touch(tmp_path / "놀면 뭐하니.E099.261025.mp4", 1)
    assert fix(tmp_path, "놀면 뭐하니.E101.261101.mp4") == "놀면 뭐하니.E100.261101.mp4"   # 자릿수 유지


def _run_main_variety(tmp_path, monkeypatch, incoming, existing):
    dl, base = tmp_path / "dl", tmp_path / "예능"
    lib = base / "런닝맨"
    dl.mkdir()
    lib.mkdir(parents=True)
    for name, size in existing:
        _touch(lib / name, size)
    for name, size in incoming:
        _touch(dl / name, size)
    cfg = {"paths": {"sources": [str(dl)], "cleanup": {}}, "base_paths": {"예능": str(base)},
           "rules": [{"id": 21, "category": "예능", "pattern": "런닝맨", "subfolder": "런닝맨", "days": ["일"]}],
           "ownership": {"apply": False, "user": "x", "group": "x", "file_mode": "664", "dir_mode": "775"}}
    sent = []
    monkeypatch.setattr(mr, "load_cfg", lambda: cfg)
    monkeypatch.setattr(mr, "reset_updated_for_today", lambda c: None)
    monkeypatch.setattr(mr, "clean_source", lambda *a, **k: None)
    monkeypatch.setattr(mr, "check_variety_encoding", lambda *a, **k: False)
    monkeypatch.setattr(mr, "mark_rule_updated_map", lambda *a, **k: None)
    monkeypatch.setattr(mr, "tg_send", lambda c, text, *a, **k: sent.append(text))
    mr.main()
    return sorted(p.name for p in lib.iterdir()), sent


def test_main_autofixes_plus_one_episode(tmp_path, monkeypatch, fresh_db):
    names, sent = _run_main_variety(tmp_path, monkeypatch, [("런닝맨.E826.261101.720p-NEXT.mp4", 5)],
                                    [("런닝맨.E824.261025.mp4", 3)])
    assert "런닝맨.E825.261101.720p-NEXT.mp4" in names and "런닝맨.E826.261101.720p-NEXT.mp4" not in names
    assert any(t.startswith("회차 번호 자동으로 고침: 런닝맨.E826.261101.720p-NEXT.mp4 → 런닝맨.E825") for t in sent)
    assert not any("확인 필요" in t for t in sent)


def test_main_only_alerts_when_gap_not_fixable(tmp_path, monkeypatch, fresh_db):
    names, sent = _run_main_variety(tmp_path, monkeypatch, [("런닝맨.E827.261101.mp4", 5)],
                                    [("런닝맨.E824.261025.mp4", 3)])
    assert "런닝맨.E827.261101.mp4" in names
    assert any("회차 번호 확인 필요: 런닝맨.E827.261101.mp4" in t for t in sent)
    assert not any("자동으로 고침" in t for t in sent)


def test_main_no_autofix_when_fixed_name_exists(tmp_path, monkeypatch, fresh_db):
    # 고친 이름(E825)이 이미 있으면(크기가 달라 중복 처리도 안 됨) 고치지 않고 알림만
    names, sent = _run_main_variety(tmp_path, monkeypatch, [("런닝맨.E826.261101.mp4", 5)],
                                    [("런닝맨.E824.261025.mp4", 3), ("런닝맨.E825.261101.mp4", 4)])
    assert {"런닝맨.E825.261101.mp4", "런닝맨.E826.261101.mp4"} <= set(names)
    assert any("확인 필요" in t and "이미 있어서 안 고침" in t for t in sent)
