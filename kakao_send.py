#!/usr/bin/env python3
"""요청 완료 안내를 카카오톡 채팅방에 보낸다 - kakao-desktop(webtop, KakaoTalk on Wine) 화면 조작 (2026-11-01).

카카오 공식 API로는 단톡방에 못 보내서, 호스트에서 docker exec로 xdotool·xclip을 써서
  열려 있는 채팅방 창 → 포스터 붙여넣기 → 미리보기 대화상자 Enter → 글 붙여넣기 → Enter.
Media Router(컨테이너)가 run_all.sh 강제 명령(SSH 'kakao-send')으로 부르고, JSON은 stdin으로 받는다:
  {"room": "방 이름", "poster": "https://...jpg" | "", "text": "안내 글"}
마지막 줄에 {"ok": bool, "message": "...", "image": bool} 을 찍는다.

안전장치 (시험방 "김창기"와 실제 단톡방 "김창기NAS 고객방"은 앞부분이 같다):
  - 방을 검색해서 열지 않는다. 이름이 **완전히 같은** 창이 이미 열려 있어야 하고, 정확히 하나여야 한다.
  - 붙여넣기·Enter 직전마다 활성 창 번호와 제목이 그 방인지 다시 확인하고, 아니면 아무것도 보내지 않고 멈춘다.
  - 이미지 미리보기 대화상자는 그 방 창에 딸린(WM_TRANSIENT_FOR) 것일 때만 Enter.
"""
from __future__ import annotations
import fcntl, json, subprocess, sys, time, urllib.request

DOCKER = ["sudo", "-n", "/usr/local/bin/docker"]
CONTAINER = "kakao-desktop"
LOCK = "/tmp/mr_kakao_send.lock"
POSTER_BMP = "/tmp/mr_kakao_poster.bmp"


class Fail(Exception):
    pass


class Blocker(Fail):
    """카톡에 인증·로그인·업데이트 같은 창이 떠 있음 - 그 창은 절대 건드리지 않고 멈춘다(11-04)"""
    def __init__(self, title: str, shot: str = ""):
        super().__init__(f"카톡에 '{title}' 창이 떠 있어요 - kakao-desktop에서 확인해 주세요 (보내지 않음)")
        self.title, self.shot = title, shot


# 이런 낱말이 든 창(또는 제목 있는 모르는 대화상자)이 보이면 손대지 않고 멈춘다(11-04 사용자: '카카오계정 인증' 창이 떠 있었음)
BLOCK_WORDS = ("인증", "로그인", "계정", "보안", "업데이트", "login", "log in", "update", "verif", "auth", "security")
BLOCK_EXCEPT = {"카카오계정"}          # 같은 이름의 채팅방(공식 알림 계정)은 막지 않음 — '카카오계정 인증' 같은 창은 막음
BLOCKER_PNG = "/volume1/docker/media-router/data/kakao_blocker.png"   # MR 컨테이너에선 /app/data/kakao_blocker.png
_XWD2PNG = (
    "import sys,struct\nfrom PIL import Image\nd=sys.stdin.buffer.read()\nh=struct.unpack('>25I',d[:100])\n"
    "o=h[0]+h[19]*12\nImage.frombuffer('RGBX',(h[4],h[5]),d[o:o+h[12]*h[5]],'raw','BGRX',h[12],1)"
    ".convert('RGB').save(sys.stdout.buffer,'PNG')\n")


def x(*args, stdin: bytes | None = None, timeout: int = 15, check: bool = True) -> str:
    """kakao-desktop 안에서 abc 사용자, DISPLAY=:1로 명령 실행"""
    cmd = DOCKER + ["exec"] + (["-i"] if stdin is not None else []) + ["-u", "abc", "-e", "DISPLAY=:1",
                                                                     CONTAINER] + list(args)
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Fail(f"카톡 화면 명령이 너무 오래 걸림: {args[0]}")
    if check and r.returncode != 0:
        raise Fail(f"카톡 화면 명령 실패({args[0]}): {r.stderr.decode(errors='replace').strip()[:150]}")
    return r.stdout.decode(errors="replace").strip()


def win_name(w: str) -> str:
    return x("xdotool", "getwindowname", w, check=False)


def active() -> str:
    return x("xdotool", "getactivewindow", check=False)


def find_room(room: str) -> str:
    out = x("xdotool", "search", "--class", "kakaotalk.exe", check=False)
    hits = [w for w in out.split() if w.isdigit() and win_name(w) == room]
    if not hits:
        raise Fail(f"'{room}' 채팅방 창이 열려 있지 않음 (kakao-desktop에서 그 방을 열어 두세요)")
    if len(hits) > 1:
        raise Fail(f"'{room}' 이름의 창이 {len(hits)}개라 고르지 않음")
    return hits[0]


# 방 창이 없을 때 직접 열기 (2026-11-03, 11-04 고침): 매일 04:16 kakao-desktop 재시작 뒤엔 채팅방 창이 다 닫혀 있다.
# 카톡 검색칸은 붙여넣기로는 걸러지지 않고(Enter하면 맨 위 방이 열림) 버튼 위치가 상태마다 달라서,
# 채팅 목록 줄을 위에서부터 차례로 더블클릭해 열어 보고, 열린 창 제목이 방 이름과 **완전히 같을 때만** 쓴다.
# 11-04 06:00 실패: 재시작 직후라 방 창이 1.5초 안에 안 떠서 제목 확인이 빗나갔고, 늦게 뜬 창(김창기NAS 고객방 포함)이
# 안 닫힌 채 남았다 → 새 창이 뜰 때까지 기다리고, 찾는 동안 새로 생긴 다른 방 창은 끝에 전부 닫는다(늦게 뜬 것까지).
# 목록은 한 화면(6줄)씩 아래로 스크롤하며 최대 MAX_ROWS줄까지 본다. 메인 창은 절대 안 닫음(닫으면 트레이로 숨어 못 꺼냄).
# 좌표는 메인 창(392x642) 기준: 채팅 탭(32,117), 목록 줄 가운데 y=150부터 70px 간격, 아래 광고(y>540)는 피함.
CHAT_TAB = (32, 117)
ROW_X, ROW_YS = 200, (150, 220, 290, 360, 430, 500)
MAX_ROWS = 42
NEW_WAIT = 6.0          # 더블클릭 뒤 새 창을 기다리는 최대 시간(초)


def visible_windows() -> list[str]:
    return [w for w in x("xdotool", "search", "--onlyvisible", "--class", "kakaotalk.exe", check=False).split()
            if w.isdigit()]


def main_window() -> str:
    mains = [w for w in visible_windows() if win_name(w) == "KakaoTalk"]
    if len(mains) != 1:
        raise Fail("카톡 메인 창이 안 보임(트레이에 숨었거나 로그아웃) - 보내지 않음")
    return mains[0]


def _named_new(before: set, main: str) -> list[str]:
    """before 이후 새로 보이는 창 중 이름 있는 것(방 창). 이름 없는 작은 창(팝업·툴팁)은 뺀다"""
    return [w for w in visible_windows() if w not in before and w != main and win_name(w)]


def is_blocker(name: str, dialog: bool = False) -> bool:
    if not name or name == "KakaoTalk" or name in BLOCK_EXCEPT:
        return False
    low = name.lower()
    return any(k in low for k in BLOCK_WORDS) or dialog


def _is_dialog(w: str) -> bool:
    return "DIALOG" in x("xprop", "-id", w, "_NET_WM_WINDOW_TYPE", check=False)


def capture(w: str) -> str:
    """그 창만 찍어 BLOCKER_PNG로(클릭·입력 없이 xwd로 읽기만). 실패하면 ''"""
    try:
        raw = subprocess.run(DOCKER + ["exec", "-u", "abc", "-e", "DISPLAY=:1", CONTAINER, "xwd", "-id", w, "-silent"],
                             capture_output=True, timeout=20).stdout
        png = subprocess.run(DOCKER + ["exec", "-i", CONTAINER, "/lsiopy/bin/python3", "-c", _XWD2PNG],
                             input=raw, capture_output=True, timeout=30).stdout
        if png.startswith(b"\x89PNG"):
            with open(BLOCKER_PNG, "wb") as f:
                f.write(png)
            return BLOCKER_PNG
    except Exception:
        pass
    return ""


def check_blockers(rooms: tuple = ()) -> None:
    """보이는 카톡 창 중 인증·로그인 같은 창(또는 제목 있는 모르는 대화상자)이 있으면 Blocker. 그 창엔 아무것도 안 함"""
    for w in visible_windows():
        name = win_name(w)
        if not name or name in rooms or name == "KakaoTalk":
            continue
        if is_blocker(name, _is_dialog(w)):
            raise Blocker(name, capture(w))


def close_window(w: str, keep: str) -> bool:
    """방 창 w를 닫는다: 앞으로 가져와 활성 창이 정말 w이고 이름이 keep(찾는 방)·KakaoTalk이 아닐 때만 Alt+F4"""
    name = win_name(w)
    if not name or name in (keep, "KakaoTalk"):
        return False
    if is_blocker(name, _is_dialog(w)):
        raise Blocker(name, capture(w))                  # 인증·로그인 창은 닫지도 않는다
    x("xdotool", "windowactivate", "--sync", w, check=False, timeout=10)
    for _ in range(10):
        if active() == w:
            break
        time.sleep(0.2)
    else:
        return False
    if win_name(active()) != name:
        return False
    x("xdotool", "key", "--clearmodifiers", "alt+F4")
    for _ in range(15):
        time.sleep(0.2)
        if w not in visible_windows():
            return True
    return False


def open_room(room: str) -> str:
    main = main_window()
    start = set(visible_windows())
    found = ""
    try:
        focus(main, "KakaoTalk")
        x("xdotool", "mousemove", "--window", main, str(CHAT_TAB[0]), str(CHAT_TAB[1]), "click", "1")
        time.sleep(0.8)
        x("xdotool", "mousemove", "--window", main, str(ROW_X), "300", "click", "--repeat", "15", "--delay", "40", "4")
        time.sleep(0.5)                                            # 목록 맨 위로
        tried, seen = 0, set()
        while tried < MAX_ROWS and not found:
            page = set()
            for y in ROW_YS:
                tried += 1
                check_blockers((room,))                          # 새로 뜬 인증 창이 있으면 더 누르지 않음
                before = set(visible_windows())
                focus(main, "KakaoTalk")
                x("xdotool", "mousemove", "--window", main, str(ROW_X), str(y),
                  "click", "--repeat", "2", "--delay", "100", "1")
                waited, new = 0.0, []
                while waited < NEW_WAIT:
                    time.sleep(0.3)
                    waited += 0.3
                    new = _named_new(before, main)
                    a = active()
                    if new or win_name(a) == room or (a in before and a != main):
                        break                                       # 새 창, 찾는 방, 이미 열려 있던 창이 앞으로 옴
                page.update(win_name(w) for w in new)
                if not new and active() in before and active() != main:
                    page.add(win_name(active()))
                hit = [w for w in new if win_name(w) == room]
                if not hit and win_name(active()) == room:          # 이미 열려 있던 창이 앞으로 온 경우
                    hit = [active()]
                if hit:
                    found = hit[0]
                    break
            if not found and page <= seen:
                break                                              # 스크롤해도 새 방이 없음 = 목록 끝
            seen |= page
            if not found:
                focus(main, "KakaoTalk")                           # 다음 6줄로
                x("xdotool", "mousemove", "--window", main, str(ROW_X), "300", "click", "--repeat", "6", "--delay", "40", "5")
                time.sleep(0.6)
    finally:
        # 찾는 동안 새로 생긴 다른 방 창은 전부 닫는다(늦게 뜬 것까지 보려고 잠깐 기다린 뒤)
        time.sleep(1.0)
        left = []
        for w in _named_new(start, main):
            if w != found and not close_window(w, room):
                left.append(win_name(w))
        if left and found:
            raise Fail(f"찾다가 연 창을 못 닫음: {left} - 보내지 않음")
    if not found:
        raise Fail(f"채팅 목록에서 '{room}' 방을 못 찾음 - 보내지 않음" + (f" (못 닫은 창: {left})" if left else ""))
    return found


def focus(w: str, room: str) -> None:
    """방 창을 앞으로 가져오고, 활성 창이 정말 그 방인지 확인 (아니면 Fail - 아무것도 안 보냄)"""
    x("xdotool", "windowactivate", "--sync", w, check=False, timeout=10)
    for _ in range(10):
        if active() == w and win_name(w) == room:
            return
        time.sleep(0.2)
    raise Fail(f"'{room}' 창이 앞으로 오지 않음 (활성 창: {win_name(active())!r}) - 보내지 않음")


def transient_for(w: str) -> str:
    out = x("xprop", "-id", w, "WM_TRANSIENT_FOR", check=False)
    # WM_TRANSIENT_FOR(WINDOW): window id # 0x3a00053
    try:
        return str(int(out.rsplit("#", 1)[1].strip(), 16))
    except (IndexError, ValueError):
        return ""


def send_image(w: str, room: str, url: str) -> None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "media-router"}),
                                    timeout=20) as r:
            data = r.read()
    except Exception as e:
        raise Fail(f"포스터 받기 실패: {e}")
    # Wine 클립보드는 image/bmp를 CF_DIB로 넘겨 준다(image/png는 카톡이 무시함)
    x("/lsiopy/bin/python3", "-c",
      "import sys,io;from PIL import Image;"
      f"Image.open(io.BytesIO(sys.stdin.buffer.read())).convert('RGB').save('{POSTER_BMP}')", stdin=data)
    x("sh", "-c", f"xclip -selection clipboard -t image/bmp -i {POSTER_BMP}")
    focus(w, room)
    x("xdotool", "key", "--clearmodifiers", "ctrl+v")
    dlg = ""
    for _ in range(25):   # 미리보기 대화상자('Clipped Image')가 그 방 창에 딸려 뜰 때까지
        time.sleep(0.2)
        a = active()
        if a and a != w and transient_for(a) == w:
            dlg = a
            break
    if not dlg:
        raise Fail("이미지 미리보기 창이 안 뜸")
    if active() != dlg or transient_for(dlg) != w:
        raise Fail("미리보기 창이 바뀜 - 보내지 않음")
    x("xdotool", "key", "--clearmodifiers", "Return")
    for _ in range(25):
        time.sleep(0.2)
        if dlg not in x("xdotool", "search", "--onlyvisible", "--class", "kakaotalk.exe", check=False).split():
            return
    # Esc로 닫지 않는다: 11-01 시험 때 Esc(--window)가 카톡 채팅창들까지 닫고 메인 창을 트레이로 숨겼다.
    # 그대로 두면 아래 글 보내기의 focus()가 '창이 앞으로 오지 않음'으로 멈춘다(아무것도 안 보냄).
    raise Fail("이미지 미리보기 창이 닫히지 않음 - 보내지 않음")


def send_text(w: str, room: str, text: str) -> None:
    x("xclip", "-selection", "clipboard", "-i", stdin=text.encode())
    focus(w, room)
    x("xdotool", "key", "--clearmodifiers", "ctrl+a", "Delete", "ctrl+v")   # 입력칸에 남은 글은 지우고
    time.sleep(0.5)
    if active() != w or win_name(w) != room:
        raise Fail("붙여넣은 뒤 활성 창이 바뀜 - 보내지 않음")
    x("xdotool", "key", "--clearmodifiers", "Return")


def prepare(room: str) -> dict:
    """방 창만 미리 열어 둔다(보내지 않음). 05:30 Media Router가 SSH 'kakao-prepare'로 부른다(11-04)."""
    lock = open(LOCK, "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        if "KakaoTalk.exe" not in x("pgrep", "-af", "KakaoTalk.exe", check=False):
            raise Fail("카톡이 실행 중이 아님")
        check_blockers((room,))
        try:
            find_room(room)
            return {"ok": True, "message": f"'{room}' 창이 이미 열려 있음"}
        except Fail as e:
            if "열려 있지 않음" not in str(e):
                raise
        open_room(room)
        find_room(room)
        return {"ok": True, "message": f"'{room}' 창을 열어 둠"}
    except Blocker as e:
        return {"ok": False, "message": str(e), "blocker": e.title, "shot": e.shot}
    except Fail as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"알 수 없는 오류: {e}"}


def main() -> dict:
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {"ok": False, "message": "요청 JSON을 못 읽음"}
    room = str(req.get("room") or "").strip()
    text = str(req.get("text") or "").strip()
    poster = str(req.get("poster") or "").strip()
    if not room or not text:
        return {"ok": False, "message": "방 이름이나 글이 비어 있음"}
    lock = open(LOCK, "w")
    fcntl.flock(lock, fcntl.LOCK_EX)   # 둘이 동시에 클립보드·창을 건드리지 않게
    image = False
    try:
        if "KakaoTalk.exe" not in x("pgrep", "-af", "KakaoTalk.exe", check=False):
            raise Fail("카톡이 실행 중이 아님")
        check_blockers((room,))
        try:
            w = find_room(room)
        except Fail as e:
            if "열려 있지 않음" not in str(e):
                raise
            open_room(room)                       # 창이 없으면 직접 열고, 아래에서 다시 완전 일치로 찾는다
            w = find_room(room)
        image_err = ""
        if poster.startswith("https://"):
            try:
                send_image(w, room, poster)
                image = True
            except Fail as e:
                if "보내지 않음" in str(e):
                    raise
                image_err = f" (포스터는 못 보냄: {e})"
            w = find_room(room)   # 그 사이 창이 닫혔거나 바뀌었으면 여기서 멈춘다
        check_blockers((room,))                  # 보내기 직전에도 한 번 더
        send_text(w, room, text)
        return {"ok": True, "image": image, "message": f"'{room}'에 보냄" + image_err}
    except Blocker as e:
        return {"ok": False, "image": image, "message": str(e), "blocker": e.title, "shot": e.shot}
    except Fail as e:
        return {"ok": False, "image": image, "message": str(e)}
    except Exception as e:
        return {"ok": False, "image": image, "message": f"알 수 없는 오류: {e}"}


PREPARE_ROOMS = ("건이네가족",)      # 05:30 미리 열어 둘 방(06:00 날씨). 다른 방은 여기서 안 연다


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "prepare":
        r = prepare(sys.argv[2]) if sys.argv[2] in PREPARE_ROOMS + ("김창기",) else {"ok": False, "message": "허용 안 된 방"}
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(json.dumps(main(), ensure_ascii=False))
