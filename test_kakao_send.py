# -*- coding: utf-8 -*-
"""kakao_send.py 안전장치: 창 제목이 정확히 같을 때만, 활성 창이 바뀌면 Enter 안 누름"""
import io
import json

import pytest

import kakao_send as ks


class FakeX:
    """xdotool/xclip 흉내. windows: {id: 제목}"""
    def __init__(self, windows, active="", steal_after_paste=False, stubborn=False):
        self.windows, self.active_w, self.keys = dict(windows), active, []
        self.steal, self.stubborn = steal_after_paste, stubborn

    def __call__(self, *args, stdin=None, timeout=15, check=True):
        a = list(args)
        if a[0] == "pgrep":
            return "3958 C:\\Program Files (x86)\\Kakao\\KakaoTalk\\KakaoTalk.exe"
        if a[:2] == ["xdotool", "search"]:
            return "\n".join(self.windows)
        if a[:2] == ["xdotool", "getwindowname"]:
            return self.windows.get(a[2], "")
        if a[:2] == ["xdotool", "getactivewindow"]:
            return self.active_w
        if a[:2] == ["xdotool", "windowactivate"]:
            if not self.stubborn:
                self.active_w = a[-1]
            return ""
        if a[:2] == ["xdotool", "key"]:
            self.keys.append(a[3:])
            if self.steal and "ctrl+v" in a:
                self.active_w = "999"                 # 붙여넣은 직후 다른 창이 앞으로
            return ""
        return ""


def run(monkeypatch, tmp_path, fake, req):
    monkeypatch.setattr(ks, "x", fake)
    monkeypatch.setattr(ks.time, "sleep", lambda s: None)
    monkeypatch.setattr(ks, "LOCK", str(tmp_path / "lock"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(req, ensure_ascii=False)))
    return ks.main()


def test_sends_text_only_to_exact_room(monkeypatch, tmp_path):
    fake = FakeX({"10": "김창기NAS 고객방", "11": "김창기", "12": "KakaoTalk"})
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert r["ok"] and fake.active_w == "11" and fake.keys[-1] == ["Return"]


def test_prefix_match_is_not_enough(monkeypatch, tmp_path):
    fake = FakeX({"10": "김창기NAS 고객방", "12": "KakaoTalk"})
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert not r["ok"] and "열려 있지 않음" in r["message"] and not fake.keys


def test_duplicate_room_windows_refused(monkeypatch, tmp_path):
    fake = FakeX({"11": "김창기", "13": "김창기"})
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert not r["ok"] and "2개" in r["message"] and not fake.keys


def test_focus_stolen_after_paste_no_enter(monkeypatch, tmp_path):
    fake = FakeX({"10": "김창기NAS 고객방", "11": "김창기"}, steal_after_paste=True)
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert not r["ok"] and "보내지 않음" in r["message"] and ["Return"] not in fake.keys


def test_window_that_will_not_focus(monkeypatch, tmp_path):
    fake = FakeX({"10": "김창기NAS 고객방", "11": "김창기"}, active="10", stubborn=True)
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert not r["ok"] and "앞으로 오지 않음" in r["message"] and not fake.keys


@pytest.mark.parametrize("req", [{"room": "", "text": "a"}, {"room": "김창기", "text": " "}])
def test_empty_input(monkeypatch, tmp_path, req):
    assert not run(monkeypatch, tmp_path, FakeX({}), req)["ok"]
