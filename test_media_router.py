# -*- coding: utf-8 -*-
"""media_router.py 핵심 로직 회귀 테스트.

목적: find_matches의 AND/OR/exclude 조합, extract_episode_number의 정규식
우선순위, reset_updated_for_today의 캐치업 로직처럼 겉보기엔 단순해 보이지만
고치다가 실수하기 쉬운 부분을, 사람이 매번 손으로 재현하지 않고 `pytest`
한 번으로 검증할 수 있게 한다.

실행: pytest -q  (requirements-dev.txt 설치 후)
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import media_router as mr

KST = ZoneInfo("Asia/Seoul")


# ---------------------------------------------------------------------------
# extract_episode_number
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("전현무계획.S04E08.260821.mp4", 8),      # S01E05 형식 (실제 운영 로그에서 확인된 파일명)
    ("Bloody.Game.X.S01E09.mkv", 9),
    ("나 혼자 산다.E660.260821.mp4", 660),     # E677 형식
    ("우주떡집.E04.260821.mp4", 4),
    ("옥탑방의 문제아들.EP325.mp4", 325),      # EP 접두
    ("드라마 14화.mp4", 14),                   # 화 형식
    ("1화.mp4", 1),
    ("아무 숫자도 없는 파일.mp4", None),
    ("연도만 있는 파일.2024.mp4", None),       # 단순 4자리 숫자는 매치되면 안 됨
])
def test_extract_episode_number(filename, expected):
    assert mr.extract_episode_number(filename) == expected


def test_extract_episode_number_prefers_season_episode_pattern():
    # S01E05 패턴이 있으면 그걸 우선 사용해야 한다 (뒤에 다른 숫자가 있어도)
    assert mr.extract_episode_number("드라마.S02E05.2024.mp4") == 5


# ---------------------------------------------------------------------------
# find_matches: (pattern AND pattern2) OR (pattern_or AND pattern2_or), NOT exclude
# ---------------------------------------------------------------------------

def test_find_matches_basic_substring(tmp_path):
    (tmp_path / "런닝맨.E677.mp4").touch()
    (tmp_path / "관계없는파일.mp4").touch()

    results = {p.name for p in mr.find_matches(tmp_path, "런닝맨")}

    assert results == {"런닝맨.E677.mp4"}


def test_find_matches_and_group_requires_both_patterns(tmp_path):
    (tmp_path / "드라마.720p.mp4").touch()
    (tmp_path / "드라마.1080p.mp4").touch()

    results = {p.name for p in mr.find_matches(tmp_path, "드라마", pattern2="720p")}

    assert results == {"드라마.720p.mp4"}


def test_find_matches_or_group_is_independent_of_and_group(tmp_path):
    (tmp_path / "런닝맨.mp4").touch()
    (tmp_path / "전지적참견시점.mp4").touch()
    (tmp_path / "관계없음.mp4").touch()

    results = {p.name for p in mr.find_matches(tmp_path, "런닝맨", pattern_or="전지적참견")}

    assert results == {"런닝맨.mp4", "전지적참견시점.mp4"}


def test_find_matches_exclude_pattern_applies_to_both_groups(tmp_path):
    (tmp_path / "드라마.E01.mp4").touch()
    (tmp_path / "드라마.E01.자막.mp4").touch()

    results = {p.name for p in mr.find_matches(tmp_path, "드라마", exclude_pattern="자막")}

    assert results == {"드라마.E01.mp4"}


def test_find_matches_skips_incomplete_download_extensions(tmp_path):
    (tmp_path / "런닝맨.E01.mp4.part").touch()
    (tmp_path / "런닝맨.E01.mp4.!qb").touch()
    (tmp_path / "런닝맨.E02.mp4").touch()

    results = {p.name for p in mr.find_matches(tmp_path, "런닝맨")}

    assert results == {"런닝맨.E02.mp4"}


def test_find_matches_recurses_into_subdirectories(tmp_path):
    sub = tmp_path / "임시" / "download"
    sub.mkdir(parents=True)
    (sub / "런닝맨.E01.mp4").touch()

    results = {p.name for p in mr.find_matches(tmp_path, "런닝맨")}

    assert results == {"런닝맨.E01.mp4"}


def test_find_matches_no_pattern_matches_nothing(tmp_path):
    (tmp_path / "아무거나.mp4").touch()

    results = list(mr.find_matches(tmp_path, "", None, None, None, None))

    assert results == []


# ---------------------------------------------------------------------------
# reset_updated_for_today: 캐치업 리셋 로직 (실제 DB 왕복까지 검증)
# ---------------------------------------------------------------------------

def _seed_rule(db_module, **overrides):
    """테스트용 규칙 하나를 DB에 저장하고, id가 채워진 최신 상태로 반환."""
    cfg = db_module.load_cfg()
    rule = {
        "category": "드라마",
        "pattern": "*",
        "subfolder": "테스트드라마",
        "days": ["토"],
        "updated_map": {"토": "Y"},
    }
    rule.update(overrides)
    cfg.setdefault("rules", []).append(rule)
    db_module.save_cfg(cfg)
    saved = db_module.load_cfg()
    return saved["rules"][-1]


def test_reset_migration_first_run_leaves_existing_state_untouched(fresh_db, monkeypatch):
    """last_reset_date가 아예 없던 과거 DB(마이그레이션 시점)에서는, 이미 체크된
    항목을 실수로 되돌리지 않고 날짜만 기록해야 한다."""
    rule = _seed_rule(fresh_db, days=["토"], updated_map={"토": "Y"})
    cfg = fresh_db.load_cfg()
    monkeypatch.setattr(mr, "current_localtime", lambda: datetime(2026, 8, 22, 11, 3, tzinfo=KST))

    changed = mr.reset_updated_for_today(cfg)

    assert changed is True
    assert cfg["last_reset_date"] == "2026-08-22"
    reloaded = fresh_db.load_cfg()
    target = next(r for r in reloaded["rules"] if r["id"] == rule["id"])
    assert target["updated_map"]["토"] == "Y"


def test_reset_same_day_rerun_is_noop(fresh_db, monkeypatch):
    """같은 날 여러 번 실행돼도(20분마다) 한 번만 리셋되고, 사용자가 이미 체크한
    항목이 낮에 다시 N으로 되돌아가면 안 된다."""
    _seed_rule(fresh_db, days=["토"], updated_map={"토": "N"})
    cfg = fresh_db.load_cfg()
    monkeypatch.setattr(mr, "current_localtime", lambda: datetime(2026, 8, 22, 11, 3, tzinfo=KST))
    mr.reset_updated_for_today(cfg)  # 첫 실행: 날짜만 기록

    cfg2 = fresh_db.load_cfg()
    monkeypatch.setattr(mr, "current_localtime", lambda: datetime(2026, 8, 22, 15, 0, tzinfo=KST))
    changed = mr.reset_updated_for_today(cfg2)

    assert changed is False


def test_reset_catches_up_when_midnight_window_was_missed(fresh_db, monkeypatch):
    """스케줄러가 자정~1시 창을 놓쳐서 낮 12시반에야 처음 실행돼도, 오늘 요일은
    캐치업으로 리셋돼야 한다 (이번에 실제로 겪은 버그의 회귀 테스트)."""
    rule = _seed_rule(fresh_db, days=["토"], updated_map={"토": "Y"})
    cfg = fresh_db.load_cfg()
    monkeypatch.setattr(mr, "current_localtime", lambda: datetime(2026, 8, 22, 11, 3, tzinfo=KST))
    mr.reset_updated_for_today(cfg)  # 마이그레이션 첫 실행

    # 다음 주 토요일: 지난주 완료 흔적을 재현해두고, 낮 12시반에 첫 실행됐다고 가정
    reloaded = fresh_db.load_cfg()
    target = next(r for r in reloaded["rules"] if r["id"] == rule["id"])
    target["updated_map"]["토"] = "Y"
    fresh_db.update_rule_fields(target["id"], updated_map=target["updated_map"])
    cfg2 = fresh_db.load_cfg()
    monkeypatch.setattr(mr, "current_localtime", lambda: datetime(2026, 8, 29, 12, 30, tzinfo=KST))

    changed = mr.reset_updated_for_today(cfg2)

    assert changed is True
    final = fresh_db.load_cfg()
    target2 = next(r for r in final["rules"] if r["id"] == rule["id"])
    assert target2["updated_map"]["토"] == "N"
    assert final.get("last_reset_date") == "2026-08-29"


def test_reset_does_not_touch_unrelated_weekday(fresh_db, monkeypatch):
    """오늘이 토요일이면, 일요일에만 걸린 규칙은 건드리면 안 된다."""
    rule = _seed_rule(fresh_db, days=["일"], updated_map={"일": "Y"})
    fresh_db.set_config_value("last_reset_date", "2026-08-28")
    cfg = fresh_db.load_cfg()
    monkeypatch.setattr(mr, "current_localtime", lambda: datetime(2026, 8, 29, 0, 5, tzinfo=KST))

    mr.reset_updated_for_today(cfg)

    reloaded = fresh_db.load_cfg()
    target = next(r for r in reloaded["rules"] if r["id"] == rule["id"])
    assert target["updated_map"]["일"] == "Y"
