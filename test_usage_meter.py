"""상단 사용량 칩(11-12): Claude·agy 읽기, 실패 시 이전 값 유지, 칩 표시(소유자만)"""
import json
import time

import pytest

import usage_meter


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "c-tok"}}))
    agy = tmp_path / ".gemini" / "antigravity-cli"
    agy.mkdir(parents=True)
    (agy / "antigravity-oauth-token").write_text(json.dumps({"token": {"access_token": "a-tok", "expiry": "2099-01-01T00:00:00.123456789+09:00"}}))
    monkeypatch.setattr(usage_meter, "CLAUDE_CRED", tmp_path / ".claude" / ".credentials.json")
    monkeypatch.setattr(usage_meter, "AGY_TOKEN", agy / "antigravity-oauth-token")
    return tmp_path


def fake_get(url, token, data=None, headers=None):
    if "anthropic" in url:
        assert token == "c-tok"
        return {"five_hour": {"utilization": 12.4, "resets_at": "2026-11-12T05:49:59+00:00"},
                "seven_day": {"utilization": 82.0, "resets_at": "2026-11-13T14:59:59+00:00"}}
    assert token == "a-tok"
    return {"models": {"gemini-3-flash": {"quotaInfo": {"remainingFraction": 0.36, "resetTime": "2026-11-14T13:01:02Z"}},
                       "gemini-2.5-pro": {"quotaInfo": {"remainingFraction": 0.5, "resetTime": "2026-11-14T13:01:02Z"}},
                       "claude-sonnet-4-6": {"quotaInfo": {"remainingFraction": 1, "resetTime": "2026-11-19T00:56:23Z"}},
                       "tab_flash_lite_preview": {"quotaInfo": {"remainingFraction": 1}}}}


def test_refresh_and_view(home, tmp_path):
    p = tmp_path / "u.json"
    d = usage_meter.refresh(p, get=fake_get)
    assert d["claude"]["five"] == 12 and d["claude"]["week"] == 82
    assert d["agy"]["gemini"] == 64 and d["agy"]["other"] == 0          # 가장 적게 남은 Gemini 모델 기준
    v = usage_meter.view(usage_meter.load(p))
    assert v["claude_lvl"] == "warn" and v["agy_lvl"] == ""
    assert "11-14(토) 00:00" in v["title"] and "11-14(토) 22:01" in v["title"]
    assert "tok" not in p.read_text()                                     # 토큰은 파일에 안 남김


def test_failure_keeps_old_values(home, tmp_path):
    p = tmp_path / "u.json"
    usage_meter.refresh(p, get=fake_get)

    def broken(url, *a, **k):
        if "anthropic" in url:
            raise OSError("down")
        return fake_get(url, *a, **k)
    d = usage_meter.refresh(p, get=broken)
    assert d["claude"]["week"] == 82 and d["claude"]["stale"] is True
    v = usage_meter.view(d)
    assert v["claude_old"] and not v["agy_old"] and "마지막으로 읽은 값" in v["title"]


def test_expired_agy_token_calls_refresh(home):
    tok = usage_meter.AGY_TOKEN
    tok.write_text(json.dumps({"token": {"access_token": "old", "expiry": "2020-01-01T00:00:00Z"}}))
    called = []

    def refresh():
        called.append(1)
        tok.write_text(json.dumps({"token": {"access_token": "a-tok", "expiry": "2099-01-01T00:00:00Z"}}))
    assert usage_meter.fetch_agy(fake_get, refresh)["gemini"] == 64 and called


def test_no_data_no_chip():
    assert usage_meter.view({}) is None


def test_chip_owner_only(monkeypatch, tmp_path):
    import web_admin
    p = tmp_path / "u.json"
    p.write_text(json.dumps({"claude": {"five": 0, "week": 95, "at": time.time()}, "agy": {"gemini": 10, "at": time.time()}}))
    monkeypatch.setattr(web_admin, "USAGE_METER_PATH", p)
    c = web_admin.app.test_client()
    monkeypatch.setattr(web_admin, "authed", lambda req: True)
    monkeypatch.setattr(web_admin, "session_role", lambda req: "admin")
    monkeypatch.setattr(web_admin, "_req_user_perms", lambda req: (web_admin.accounts.OWNER, {}))
    html = c.get("/notes").get_data(as_text=True)
    assert 'class="usage-chip"' in html and "u-seg bad" in html
    monkeypatch.setattr(web_admin, "session_role", lambda req: "user")
    monkeypatch.setattr(web_admin, "_req_user_perms", lambda req: ("someone", {}))
    assert web_admin._usage_chip("user", "someone") is None
