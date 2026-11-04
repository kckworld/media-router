# -*- coding: utf-8 -*-
"""로그인 잠금 우회 막기 (2026-11-04 인사팀 점검): X-Forwarded-For·CF-Connecting-IP를 바꿔 보내도 잠김 + 전체 실패 한도"""
import pytest

import web_admin

NGINX = {"REMOTE_ADDR": "127.0.0.1"}     # DSM nginx → localhost:5080


@pytest.fixture
def lg(monkeypatch):
    monkeypatch.setattr(web_admin, "PASSWORD", "right-pw")
    monkeypatch.setattr(web_admin, "VIEWER_PASSWORD", "")
    monkeypatch.setattr(web_admin, "_login_failures", {})
    monkeypatch.setattr(web_admin, "_login_global", {"fails": [], "locked_until": 0.0})
    sent = []
    monkeypatch.setattr(web_admin, "_tg_notify", lambda text, *a, **k: sent.append(text) or True)
    monkeypatch.setattr(web_admin.threading, "Thread", lambda target, daemon=None: type("T", (), {"start": lambda s: target()})())
    web_admin.app.config["TESTING"] = True
    return web_admin.app.test_client(), sent


def login(c, pw, real="203.0.113.5", **headers):
    h = {"X-Real-IP": real} if real else {}
    h.update(headers)
    return c.post("/login", data={"pw": pw}, headers=h, environ_base=NGINX)


def locked(r):
    return "error=2" in (r.headers.get("Location") or "")


def test_client_ip_rules():
    from werkzeug.test import EnvironBuilder
    def ip(env, **h):
        req = web_admin.app.request_class(EnvironBuilder(headers=h, environ_base=env).get_environ())
        return web_admin._client_ip(req)
    assert ip(NGINX, **{"X-Real-IP": "203.0.113.5", "X-Forwarded-For": "1.1.1.1, 203.0.113.5"}) == "203.0.113.5"
    assert ip(NGINX, **{"X-Real-IP": "203.0.113.5", "CF-Connecting-IP": "9.9.9.9"}) == "203.0.113.5"   # 443 직접: CF 무시
    assert ip(NGINX, **{"X-Real-IP": "172.17.0.3", "CF-Connecting-IP": "198.51.100.7"}) == "198.51.100.7"  # 터널 경유
    assert ip({"REMOTE_ADDR": "192.168.50.20"}, **{"X-Forwarded-For": "8.8.8.8"}) == "192.168.50.20"     # LAN 직접: 헤더 무시
    assert ip(NGINX) == "127.0.0.1"


def test_spoofed_forwarded_for_still_locks(lg):
    c, sent = lg
    for i in range(5):
        r = login(c, "wrong", **{"X-Forwarded-For": f"10.0.0.{i}"})       # 매번 다른 XFF
    assert locked(r)
    r = login(c, "right-pw", **{"X-Forwarded-For": "10.9.9.9"})              # 맞는 비번이어도 잠금 중엔 안 됨
    assert locked(r)


def test_spoofed_cf_header_via_direct_443_still_locks(lg):
    c, sent = lg
    for i in range(6):
        r = login(c, "wrong", **{"CF-Connecting-IP": f"198.51.100.{i}"})
    assert locked(r)


def test_global_limit_locks_everyone_and_alerts_once(lg):
    c, sent = lg
    for i in range(21):                                                      # 21개 다른 진짜 IP에서 한 번씩
        login(c, "wrong", real=f"203.0.113.{i + 10}")
    assert locked(login(c, "right-pw", real="198.51.100.200"))              # 처음 온 사람도 막힘
    assert len(sent) == 1 and "바깥 로그인을 막았어요" in sent[0]                  # 11-04: 집 LAN은 예외
    login(c, "wrong", real="203.0.113.99")
    assert len(sent) == 1                                                    # 잠금 중엔 알림 반복 안 함


def test_normal_login_works(lg):
    c, sent = lg
    r = login(c, "right-pw")
    assert r.status_code == 302 and "media_admin=" in r.headers.get("Set-Cookie", "")


def test_request_submit_limit_uses_same_ip(monkeypatch):
    """요청 페이지 제한도 CF-Connecting-IP를 바꿔서 피할 수 없음"""
    from werkzeug.test import EnvironBuilder
    req = web_admin.app.request_class(EnvironBuilder(headers={"X-Real-IP": "203.0.113.5", "CF-Connecting-IP": "1.2.3.4"},
                                                     environ_base=NGINX).get_environ())
    assert web_admin._public_ip(req) == "203.0.113.5"
