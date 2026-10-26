# -*- coding: utf-8 -*-
"""분류 규칙 추가·삭제 ↔ 예능·드라마 키워드"""
import json

import web_admin


def test_variety_rule_keyword_add_and_remove(monkeypatch, tmp_path):
    path = tmp_path / "bt4g_keywords.txt"
    path.write_text("나 혼자\n미운 우리\n우리들의 발라드\n런닝맨 720p\n", encoding="utf-8")
    monkeypatch.setattr(web_admin, "BT4G_KEYWORDS_PATH", path)
    # 이미 잡는 키워드가 있으면 안 넣음, 예능은 릴을 안 붙임
    assert web_admin.add_rule_keyword("예능", "나 혼자 산다", "720p") is False
    assert web_admin.add_rule_keyword("예능", "신인감독 김연경 (2025)", "720p-NEXT") is True
    assert path.read_text(encoding="utf-8").splitlines()[-1] == "신인감독 김연경"
    rules = [{"category": "예능", "subfolder": "우리들의 발라드 (2025)"},
             {"category": "예능", "subfolder": "미운 우리 새끼"}]
    # 규칙 삭제 → 그 제목을 잡던 키워드만 지움 ('런닝맨 720p'처럼 720p 붙은 것도)
    assert web_admin.remove_rule_keyword("예능", "런닝맨", rules) == ["런닝맨 720p"]
    assert web_admin.remove_rule_keyword("예능", "미운 우리 새끼", rules[:1]) == ["미운 우리"]
    # 다른 규칙도 잡는 키워드는 남김
    path.write_text("우리\n", encoding="utf-8")
    assert web_admin.remove_rule_keyword("예능", "미운 우리 새끼", rules[:1]) == []


def _client(monkeypatch, cfg, saved):
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "load_cfg", lambda: json.loads(json.dumps(cfg)))
    monkeypatch.setattr(web_admin, "save_cfg", lambda c: saved.update(c))
    monkeypatch.setattr(web_admin, "tmdb_auto_total_episodes", lambda sub: None)
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client()


def test_add_and_delete_rule_sync_keywords(monkeypatch, tmp_path):
    drama, variety = tmp_path / "drama.txt", tmp_path / "variety.txt"
    drama.write_text("100일의 거짓말\n연애박사 | Sniper\n", encoding="utf-8")
    variety.write_text("런닝맨\n", encoding="utf-8")
    monkeypatch.setattr(web_admin, "DRAMA_KEYWORDS_PATH", drama)
    monkeypatch.setattr(web_admin, "BT4G_KEYWORDS_PATH", variety)
    cfg = {"rules": [{"id": 1, "category": "드라마", "subfolder": "연애박사 (2026)", "pattern": "연애"},
                     {"id": 2, "category": "드라마", "subfolder": "100일의 거짓말 (2026)", "pattern": "100"},
                     {"id": 3, "category": "예능", "subfolder": "런닝맨", "pattern": "런닝맨"}]}
    saved = {}
    c = _client(monkeypatch, cfg, saved)
    c.post("/add", data={"category": "예능", "pattern": "티처스", "subfolder": "성적을 부탁해 티처스 (2023)"})
    assert variety.read_text(encoding="utf-8") == "런닝맨\n성적을 부탁해 티처스\n"
    c.post("/delete", data={"idx": "1"})
    assert drama.read_text(encoding="utf-8") == "100일의 거짓말\n"
    c.post("/delete", data={"idx": "3"})
    assert variety.read_text(encoding="utf-8") == "성적을 부탁해 티처스\n"
