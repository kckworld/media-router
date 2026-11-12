"""로그인 기록 기기 칸(11-12): 파이썬 시험 접속은 'NAS 시험(Claude)', 예전 '? · ?'(…1)도 그렇게"""
import json

import accounts


def test_ua_summary_python():
    assert accounts.ua_summary("Python-urllib/3.11") == accounts.NAS_TEST_UA
    assert accounts.ua_summary("python-requests/2.31") == accounts.NAS_TEST_UA
    assert accounts.ua_summary("Mozilla/5.0 (Windows NT 10.0) Chrome/130 Edg/130") == "Windows · Edge"
    assert accounts.ua_summary("") == "? · ?"


def test_old_entries_relabeled(tmp_path):
    p = tmp_path / "accounts.json"
    (tmp_path / "login_log.json").write_text(json.dumps([
        {"at": "1", "ok": True, "user": "owner", "ip": "…1", "ua": "? · ?"},
        {"at": "2", "ok": True, "user": "owner", "ip": "…57", "ua": "? · ?"}]), encoding="utf-8")
    got = accounts.login_log(p)
    assert got[0]["ua"] == "? · ?" and got[1]["ua"] == accounts.NAS_TEST_UA
