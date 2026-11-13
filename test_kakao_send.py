# -*- coding: utf-8 -*-
"""kakao_send.py 안전장치: 창 제목이 정확히 같을 때만, 활성 창이 바뀌면 Enter 안 누름"""
import io
import json

import pytest

import kakao_send as ks


class FakeX:
    """xdotool/xclip 흉내. windows: {id: 제목}"""
    def __init__(self, windows, active="", steal_after_paste=False, stubborn=False, rows=None, late=False, broken_ctrl=0):
        self.windows, self.active_w, self.keys = dict(windows), active, []
        self.clip, self.input = "", ""   # 클립보드·입력칸(11-14: 붙여넣은 글을 다시 읽어 확인)
        self.broken_ctrl = broken_ctrl  # 앞에서 몇 번의 Ctrl 조합을 놓칠지(Wine이 Ctrl을 놓쳐 'av'만 간 일)
        self.late = late                # True: 방 창은 생기지만 앞으로 오지 않음(재시작 직후처럼 늦게 뜸)
        self.steal, self.stubborn = steal_after_paste, stubborn
        self.rows = rows or {}          # 채팅 목록 줄 y → 더블클릭하면 열리는 방 이름
        self.next_id = 100
        self.dialogs = set()            # 대화상자 창 id
        self.popup_on_row = {}          # 줄 y → 그 줄을 누르면 같이 뜨는 창 이름(인증 창 흉내)
        self.clicks = []

    def __call__(self, *args, stdin=None, timeout=15, check=True):
        a = list(args)
        if a[0] == "xprop":
            return "_NET_WM_WINDOW_TYPE(ATOM) = _NET_WM_WINDOW_TYPE_DIALOG" if a[2] in self.dialogs else "_NET_WM_WINDOW_TYPE_NORMAL"
        if a[:2] == ["xdotool", "mousemove"]:
            self.clicks.append(a[3:])
            pop = self.popup_on_row.get(int(a[5])) if len(a) > 5 and a[5].isdigit() and a[-1] == "1" else None
            if pop:
                self.next_id += 1
                self.windows[str(self.next_id)] = pop
                return ""
        if a[0] == "xclip":
            if "-i" in a:
                self.clip = (stdin or b"").decode()
                return ""
            return self.clip
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
        if a[:2] == ["xdotool", "mousemove"] and "--repeat" in a and a[-1] == "1":
            room = self.rows.get(int(a[5]))
            if room:
                same = [w for w, n in self.windows.items() if n == room]
                if same:
                    self.active_w = same[0]
                else:
                    self.next_id += 1
                    self.windows[str(self.next_id)] = room
                    if not self.late:
                        self.active_w = str(self.next_id)
            return ""
        if a[:2] == ["xdotool", "key"] and a[-1] == "alt+F4":
            self.keys.append(["alt+F4", self.windows.get(self.active_w)])
            self.windows.pop(self.active_w, None)
            self.active_w = "12"
            return ""
        if a[:2] == ["xdotool", "key"]:
            self.keys.append(a[3:])
            for k in a[2:]:
                if k.startswith("ctrl+") and self.broken_ctrl > 0:
                    self.broken_ctrl -= 1
                    self.input += k[5:]           # Ctrl을 놓치면 글자만 입력됨
                    continue
                if k == "ctrl+v":
                    self.input = self.clip
                elif k == "ctrl+c":
                    self.clip = self.input
                elif k in ("Delete", "BackSpace"):
                    self.input = ""
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
    assert not r["ok"] and "못 찾음" in r["message"] and not fake.keys      # 목록에도 없으면 아무것도 안 보냄


def test_late_windows_found_and_wrong_ones_all_closed(monkeypatch, tmp_path):
    """11-04 06:00 실패 재현: 방 창이 앞으로 안 오고 늦게 뜨는 상태 — 새 창 목록으로 찾고, 다른 방 창은 전부 닫음"""
    fake = FakeX({"12": "KakaoTalk"}, active="12", late=True,
                 rows={150: "김창기", 220: "김창기NAS 고객방", 290: "건이네가족"})
    r = run(monkeypatch, tmp_path, fake, {"room": "건이네가족", "text": "06시 날씨"})
    assert r["ok"], r
    names = set(fake.windows.values())
    assert names == {"KakaoTalk", "건이네가족"}                                # 찾다가 연 김창기·고객방 창은 닫힘
    assert {k[1] for k in fake.keys if k[0] == "alt+F4"} == {"김창기", "김창기NAS 고객방"}


def test_prepare_opens_only(monkeypatch, tmp_path):
    fake = FakeX({"12": "KakaoTalk"}, active="12", rows={150: "김창기NAS 고객방", 220: "건이네가족"})
    monkeypatch.setattr(ks, "x", fake)
    monkeypatch.setattr(ks.time, "sleep", lambda s: None)
    monkeypatch.setattr(ks, "LOCK", str(tmp_path / "lock"))
    r = ks.prepare("건이네가족")
    assert r["ok"] and "열어 둠" in r["message"] and set(fake.windows.values()) == {"KakaoTalk", "건이네가족"}
    assert ["Return"] not in fake.keys and not any(k and k[0] == "ctrl+v" for k in fake.keys)   # 보내지 않음
    assert ks.prepare("건이네가족")["message"].endswith("이미 열려 있음")


def test_opens_room_from_chat_list_closing_wrong_ones(monkeypatch, tmp_path):
    """창이 없으면 채팅 목록을 위에서부터 열어 본다. 다른 방이 새로 열리면 그 창만 닫고, 이미 열려 있던 창은 그대로(11-03)"""
    fake = FakeX({"12": "KakaoTalk", "20": "건이네가족"}, active="12",
                 rows={150: "건이네가족", 220: "김창기NAS 고객방", 290: "김창기"})
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert r["ok"], r
    assert ["alt+F4", "김창기NAS 고객방"] in fake.keys                       # 잘못 연 단톡방 창은 닫음(보내지 않음)
    assert "건이네가족" in fake.windows.values()                               # 원래 열려 있던 창은 안 닫음
    assert fake.windows[fake.active_w] == "김창기" and fake.keys[-1] == ["Return"]


def test_no_main_window_no_open(monkeypatch, tmp_path):
    fake = FakeX({"10": "김창기NAS 고객방"})
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert not r["ok"] and "메인 창" in r["message"] and not fake.keys


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


# ── 인증·로그인 같은 창이 떠 있으면 손대지 않고 멈춤 (11-04) ──

def _blk(monkeypatch):
    shots = []
    monkeypatch.setattr(ks, "capture", lambda w: shots.append(w) or "/x/kakao_blocker.png")
    return shots


def test_auth_window_stops_before_anything(monkeypatch, tmp_path):
    shots = _blk(monkeypatch)
    fake = FakeX({"12": "KakaoTalk", "11": "김창기", "30": "카카오계정 인증"}, active="12")
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert not r["ok"] and r["blocker"] == "카카오계정 인증" and r["shot"].endswith(".png")
    assert "카카오계정 인증" in r["message"] and shots == ["30"]
    assert not fake.keys and not fake.clicks                                  # 클릭·입력 전혀 없음
    assert "30" in fake.windows                                               # 그 창은 그대로


def test_kakao_account_chat_room_is_not_blocker(monkeypatch, tmp_path):
    _blk(monkeypatch)
    fake = FakeX({"12": "KakaoTalk", "11": "김창기", "31": "카카오계정"}, active="12")
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert r["ok"], r


def test_unknown_titled_dialog_and_update_window(monkeypatch, tmp_path):
    _blk(monkeypatch)
    fake = FakeX({"12": "KakaoTalk", "11": "김창기", "40": "알림"}, active="12")
    fake.dialogs.add("40")
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert not r["ok"] and r["blocker"] == "알림"
    fake2 = FakeX({"12": "KakaoTalk", "11": "김창기", "41": "KakaoTalk Update"}, active="12")
    assert run(monkeypatch, tmp_path, fake2, {"room": "김창기", "text": "x"})["blocker"] == "KakaoTalk Update"


def test_auth_window_popping_up_while_searching(monkeypatch, tmp_path):
    """방을 찾다가 인증 창이 뜨면 더 누르지 않고, 그 창은 닫지도 않음"""
    _blk(monkeypatch)
    fake = FakeX({"12": "KakaoTalk"}, active="12", rows={150: "김창기NAS 고객방", 290: "김창기"})
    fake.popup_on_row = {150: "로그인"}
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert not r["ok"] and r["blocker"] == "로그인"
    assert "로그인" in fake.windows.values()
    assert not any(k[0] == "alt+F4" and k[1] == "로그인" for k in fake.keys)
    assert ["Return"] not in fake.keys


def test_prepare_reports_blocker(monkeypatch, tmp_path):
    _blk(monkeypatch)
    fake = FakeX({"12": "KakaoTalk", "30": "보안 인증"}, active="12")
    monkeypatch.setattr(ks, "x", fake)
    monkeypatch.setattr(ks.time, "sleep", lambda s: None)
    monkeypatch.setattr(ks, "LOCK", str(tmp_path / "lock"))
    r = ks.prepare("건이네가족")
    assert not r["ok"] and r["blocker"] == "보안 인증" and not fake.clicks


def test_paste_verified_before_enter(monkeypatch, tmp_path):
    # 11-14: Ctrl이 안 먹어 입력칸이 'av'가 되면 Enter 안 누르고 비운 뒤 다시 → 같아지면 그때 보냄
    fake = FakeX({"11": "김창기", "12": "KakaoTalk"}, broken_ctrl=3)
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "📅 오늘 날씨\n맑음"})
    assert r["ok"] and fake.keys[-1] == ["Return"] and fake.input.replace(" ", "") == "📅오늘날씨\n맑음".replace(" ", "")


def test_paste_never_matches_no_enter(monkeypatch, tmp_path):
    fake = FakeX({"11": "김창기", "12": "KakaoTalk"}, broken_ctrl=999)
    r = run(monkeypatch, tmp_path, fake, {"room": "김창기", "text": "안내"})
    assert not r["ok"] and "달라" in r["message"] and ["Return"] not in fake.keys
