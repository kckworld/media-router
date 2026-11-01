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
        send_text(w, room, text)
        return {"ok": True, "image": image, "message": f"'{room}'에 보냄" + image_err}
    except Fail as e:
        return {"ok": False, "image": image, "message": str(e)}
    except Exception as e:
        return {"ok": False, "image": image, "message": f"알 수 없는 오류: {e}"}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False))
