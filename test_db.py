# -*- coding: utf-8 -*-
"""db.py 저장 계층 테스트.

특히 update_rule_fields()가 "다른 규칙을 지우거나 되살리는" 레이스 컨디션을
더 이상 일으키지 않는지를 고정해둔다 — media_router.py가 크론으로 오래 실행
되는 동안 웹 UI에서 규칙을 추가/삭제해도 서로 안전해야 한다는 요구사항의
회귀 테스트.
"""


def test_update_rule_fields_only_touches_target_row(fresh_db):
    cfg = fresh_db.load_cfg()
    cfg["rules"] = [
        {"category": "드라마", "pattern": "*A*", "subfolder": "A", "days": ["토"], "updated_map": {"토": "Y"}},
        {"category": "드라마", "pattern": "*B*", "subfolder": "B", "days": ["토"], "updated_map": {"토": "Y"}},
    ]
    fresh_db.save_cfg(cfg)
    ids = [r["id"] for r in fresh_db.load_cfg()["rules"]]

    ok = fresh_db.update_rule_fields(ids[0], updated_map={"토": "N"})

    assert ok is True
    by_id = {r["id"]: r for r in fresh_db.load_cfg()["rules"]}
    assert by_id[ids[0]]["updated_map"] == {"토": "N"}
    assert by_id[ids[1]]["updated_map"] == {"토": "Y"}  # 다른 규칙은 안 건드림


def test_update_rule_fields_on_deleted_row_is_noop_not_resurrection(fresh_db):
    """레이스 컨디션 재현: cron이 오래된 스냅샷을 들고 있는 사이 웹 UI에서
    규칙이 삭제됐다면, targeted update는 그 규칙을 되살리면 안 된다."""
    cfg = fresh_db.load_cfg()
    cfg["rules"] = [
        {"category": "드라마", "pattern": "*A*", "subfolder": "A", "days": ["토"], "updated_map": {"토": "Y"}},
    ]
    fresh_db.save_cfg(cfg)
    rule_id = fresh_db.load_cfg()["rules"][0]["id"]

    # 웹 UI에서 삭제
    cfg2 = fresh_db.load_cfg()
    cfg2["rules"] = []
    fresh_db.save_cfg(cfg2)

    # cron이 삭제 사실을 모른 채 targeted update 시도
    ok = fresh_db.update_rule_fields(rule_id, updated_map={"토": "N"})

    assert ok is False  # 0행에 적용됨 - 되살아나지 않음
    assert fresh_db.load_cfg()["rules"] == []


def test_update_rule_fields_new_row_added_concurrently_survives(fresh_db):
    """레이스 컨디션 재현: cron이 규칙 A만 아는 스냅샷을 들고 있는 사이 웹
    UI에서 규칙 B가 추가됐다면, cron의 targeted update가 B를 지우면 안 된다."""
    cfg = fresh_db.load_cfg()
    cfg["rules"] = [
        {"category": "드라마", "pattern": "*A*", "subfolder": "A", "days": ["토"], "updated_map": {"토": "Y"}},
    ]
    fresh_db.save_cfg(cfg)
    a_id = fresh_db.load_cfg()["rules"][0]["id"]

    # 웹 UI에서 새 규칙 추가
    cfg2 = fresh_db.load_cfg()
    cfg2["rules"].append(
        {"category": "드라마", "pattern": "*B*", "subfolder": "B", "days": ["토"], "updated_map": {"토": "N"}}
    )
    fresh_db.save_cfg(cfg2)

    # cron: A만 아는 상태로 targeted update
    fresh_db.update_rule_fields(a_id, updated_map={"토": "N"})

    subfolders = {r["subfolder"] for r in fresh_db.load_cfg()["rules"]}
    assert subfolders == {"A", "B"}  # B가 사라지지 않음


def test_update_rule_fields_ignores_unknown_columns(fresh_db):
    cfg = fresh_db.load_cfg()
    cfg["rules"] = [{"category": "드라마", "pattern": "*A*", "subfolder": "A", "days": [], "updated_map": {}}]
    fresh_db.save_cfg(cfg)
    rule_id = fresh_db.load_cfg()["rules"][0]["id"]

    ok = fresh_db.update_rule_fields(rule_id, category="변조시도")  # 화이트리스트에 없는 컬럼

    assert ok is False
    assert fresh_db.load_cfg()["rules"][0]["category"] == "드라마"  # 안 바뀜


def test_set_config_value_does_not_touch_rules(fresh_db):
    cfg = fresh_db.load_cfg()
    cfg["rules"] = [{"category": "드라마", "pattern": "*A*", "subfolder": "A", "days": [], "updated_map": {}}]
    fresh_db.save_cfg(cfg)

    fresh_db.set_config_value("last_reset_date", "2026-08-22")

    reloaded = fresh_db.load_cfg()
    assert reloaded["last_reset_date"] == "2026-08-22"
    assert len(reloaded["rules"]) == 1
