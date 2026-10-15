# -*- coding: utf-8 -*-
from pathlib import Path
import os, hashlib, hmac, json, re, secrets, subprocess, time, threading, socket
from flask import Flask, request, redirect, render_template, abort, make_response, jsonify, send_file
from datetime import datetime
from zoneinfo import ZoneInfo
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import requests
import psycopg2
from contextlib import contextmanager
from db import load_cfg, save_cfg, DATA_DIR

# 사용자 관리 탭 - media-stats postgres 연동 (2026-09-18, media_stats_admin에서 이식)
STATS_DB_CONFIG = {
    "host": os.getenv("STATS_DB_HOST", "localhost"),
    "port": int(os.getenv("STATS_DB_PORT", "15433")),
    "dbname": os.getenv("STATS_DB_NAME", "media_stats"),
    "user": os.getenv("STATS_DB_USER", "media_admin"),
    "password": os.getenv("STATS_DB_PASSWORD", ""),
}


@contextmanager
def _stats_db():
    """성공 시 commit, 예외 시 rollback, 항상 connection close."""
    conn = psycopg2.connect(**STATS_DB_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

BASE = Path(__file__).resolve().parent
PASSWORD = os.getenv("MEDIA_ADMIN_PASSWORD", "")

app = Flask(__name__, template_folder=str(BASE / "templates"))

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
CATEGORIES = ["드라마", "예능", "애니메이션", "다큐"]
DAY_ORDER = {d: i for i, d in enumerate(WEEKDAYS)}
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Seoul"))

RENAME_RULES_PATH = Path(os.getenv("RENAME_RULES_PATH", "/volume1/web/video_auto/rename_rules.conf"))
BT4G_KEYWORDS_PATH = Path(os.getenv("BT4G_KEYWORDS_PATH", "/volume1/web/Python/bt4g/bt4g_keywords.txt"))

# 전체 배치(run_all.sh) 수동 실행 - 호스트로 SSH 접속해 강제 명령(run_all.sh)만 실행
RUN_BATCH_SSH_KEY = os.getenv("RUN_BATCH_SSH_KEY", "/app/secrets/run_batch_key")
RUN_BATCH_SSH_HOST = os.getenv("RUN_BATCH_SSH_HOST", "127.0.0.1")
RUN_BATCH_SSH_PORT = os.getenv("RUN_BATCH_SSH_PORT", "202")
RUN_BATCH_SSH_USER = os.getenv("RUN_BATCH_SSH_USER", "kck9010")
_run_batch_state = {"lock": threading.Lock(), "last_triggered": 0}

# PC(KCK-GAMING) Wake-on-LAN - 인코딩용 PC를 원격으로 켜기 위한 매직 패킷 전송.
# 컨테이너가 network_mode: host라서 브로드캐스트가 실제 LAN(eth0)으로 그대로
# 나간다(일반 bridge 네트워크였으면 NAT에 막혀 안 나갈 수 있음).
WOL_MAC = os.getenv("WOL_MAC", "74:56:3C:69:5F:19")
WOL_BROADCAST = os.getenv("WOL_BROADCAST", "192.168.50.255")
_wol_state = {"lock": threading.Lock(), "last_triggered": 0}

# 인코딩용 PC(KCK-GAMING)에 SSH로 encode_all.ps1을 원격 실행 + 켜져있는지 확인
GAMING_PC_HOST = os.getenv("GAMING_PC_HOST", "192.168.50.102")
GAMING_PC_USER = os.getenv("GAMING_PC_USER", "")
GAMING_PC_SSH_KEY = os.getenv("GAMING_PC_SSH_KEY", "/app/secrets/kck_gaming_key")


def _pc_text(raw) -> str:
    """KCK-GAMING(Windows)에서 SSH로 받은 출력 해석. 한글 Windows의 cmd/shutdown
    메시지는 CP949로 오기 때문에 text=True(UTF-8 고정)로 받으면 UnicodeDecodeError로
    요청 자체가 실패했다(2026-10-14 '예약종료 취소'/'인코딩 시작'). UTF-8 → CP949 순으로 시도."""
    if not raw:
        return ""
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")
GAMING_PC_SCRIPT_PATH = os.getenv("GAMING_PC_SCRIPT_PATH", r"D:\SynologyDrive\encode_all.ps1")
_run_encode_state = {"lock": threading.Lock(), "last_triggered": 0}
_cancel_shutdown_state = {"lock": threading.Lock(), "last_triggered": 0}
_shutdown_pc_state = {"lock": threading.Lock(), "last_triggered": 0}

def _is_pc_online(host: str, port: int = 22, timeout: float = 2.0) -> bool:
    """SSH 포트(22)로 TCP 연결이 되는지만 확인 — ping은 Windows 방화벽에
    기본적으로 막혀있는 경우가 많아서 SSH 포트 직접 확인이 더 정확하다."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def _send_wol_packet(mac: str, broadcast: str, port: int = 9) -> None:
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(mac_bytes) != 6:
        raise ValueError(f"잘못된 MAC 주소: {mac}")
    packet = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, (broadcast, port))

# run_all.sh 실행 로그 - 호스트의 logs/ 디렉토리를 컨테이너에 마운트해서 읽는다
# (docker-compose.yml의 ./logs:/app/logs:ro 참고)
RUN_ALL_LOGS_DIR = Path(os.getenv("RUN_ALL_LOGS_DIR", "/app/logs"))
RUN_ALL_LOG_NAME_RE = re.compile(r"^run_all\.(\d{4}-\d{2}-\d{2})\.log$")
LOG_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOG_INITIAL_MAX_BYTES = 2 * 1024 * 1024  # 최초 로딩 시 파일 끝에서 최대 이만큼만
ENCODE_LOG_PATH = Path(os.getenv("ENCODE_LOG_PATH", "/app/encode_logs/_encode_log.txt"))

# "PC예약종료": 다운로드 중(.crdownload 등)인 파일이 없어지고, 인코딩 중인
# 파일(.encoding.mp4)도 없을 때까지 기다렸다가 KCK-GAMING을 종료한다.
DRIVE_ROOT_PATH = Path(os.getenv("DRIVE_ROOT_PATH", "/app/drive_root"))
ENCODING_HOLD_PATH = DRIVE_ROOT_PATH / "인코딩"
# [완료] 표시된 원본의 재인코딩 결과물을 찾을 때 뒤질 범위 - ENCODING_HOLD_PATH가
# 예능 전용 보류함이라 예능 라이브러리만 보면 된다(전체 라이브러리를 훑으면
# 느려짐). /volume1/video는 docker-compose에서 이미 ro로 마운트돼 있음.
VARIETY_LIBRARY_ROOT = Path("/volume1/video/03.예능")


_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".ts", ".m4v", ".mov", ".wmv", ".webm"}


def _library_index() -> dict[str, Path]:
    """예능 라이브러리를 한 번만 훑어 stem(확장자 제외 파일명) → 경로 사전을 만든다.
    예전엔 "[완료]" 원본마다 rglob으로 라이브러리 전체를 다시 훑어서, [완료]가 58개
    쌓이자 화면 한 번 여는 데 20~30초 걸렸다(2026-10-14)."""
    index: dict[str, Path] = {}
    try:
        for dirpath, dirnames, filenames in os.walk(VARIETY_LIBRARY_ROOT):
            dirnames[:] = [d for d in dirnames if d != "@eaDir"]
            for fn in filenames:
                base, ext = os.path.splitext(fn)
                if ext.lower() in _VIDEO_EXTS:  # 같은 이름의 자막(.srt)이 짝으로 잡히지 않게
                    index.setdefault(base, Path(dirpath) / fn)
    except Exception:
        app.logger.exception("라이브러리 결과물 탐색 실패")
    return index


# 인코딩 전/후 코덱·비트레이트(pymediainfo). NAS 디스크가 바빠 파일마다 헤더를
# 읽는 비용이 커서, (크기, 수정시각)이 같으면 다시 읽지 않도록 data/에 캐시한다.
MEDIA_PROBE_CACHE_PATH = DATA_DIR / "media_probe_cache.json"
MEDIA_PROBE_BUDGET_SEC = 15  # 한 번의 요청에서 새로 읽는 데 쓸 최대 시간 (gunicorn timeout 60s)
_CODEC_NAMES = {"AVC": "H.264", "HEVC": "H.265", "AV1": "AV1", "VP9": "VP9", "MPEG-4 Visual": "MPEG-4"}
_probe_state = {"lock": threading.Lock(), "cache": None, "dirty": False}


def _probe_cache() -> dict:
    if _probe_state["cache"] is None:
        try:
            _probe_state["cache"] = json.loads(MEDIA_PROBE_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _probe_state["cache"] = {}
    return _probe_state["cache"]


def _media_brief(p: Path, deadline: float) -> dict | None:
    """{"codec": "H.264", "bitrate": bps} 반환. 캐시에 없고 deadline이 지났으면 None
    (화면에는 '…'로 보이고 다음 새로고침 때 채워짐)."""
    try:
        st = p.stat()
    except OSError:
        return None
    sig = f"{st.st_size}:{int(st.st_mtime)}"
    with _probe_state["lock"]:
        hit = _probe_cache().get(str(p))
        if hit and hit.get("sig") == sig:
            return hit["info"]
    if time.time() > deadline:
        return None
    info = {"codec": None, "bitrate": None}
    try:
        from pymediainfo import MediaInfo
        mi = MediaInfo.parse(str(p))
        general = next((t for t in mi.tracks if t.track_type == "General"), None)
        video = next((t for t in mi.tracks if t.track_type == "Video"), None)
        if video is not None and video.format:
            info["codec"] = _CODEC_NAMES.get(video.format, video.format)
        br = getattr(general, "overall_bit_rate", None) if general is not None else None
        dur_ms = getattr(general, "duration", None) if general is not None else None
        if not br and dur_ms:
            br = st.st_size * 8 / (float(dur_ms) / 1000)
        info["bitrate"] = int(float(br)) if br else None
    except Exception:
        app.logger.exception("미디어 정보 읽기 실패: %s", p)
    with _probe_state["lock"]:
        _probe_cache()[str(p)] = {"sig": sig, "info": info}
        _probe_state["dirty"] = True
    return info


def _save_probe_cache() -> None:
    with _probe_state["lock"]:
        if not _probe_state["dirty"]:
            return
        try:
            tmp = MEDIA_PROBE_CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(_probe_state["cache"], ensure_ascii=False), encoding="utf-8")
            tmp.replace(MEDIA_PROBE_CACHE_PATH)
            _probe_state["dirty"] = False
        except Exception:
            app.logger.exception("미디어 정보 캐시 저장 실패")


def _encoding_hold_stats() -> dict:
    """Drive/인코딩 폴더 현황: 대기중/완료표시 개수·용량 + 완료표시 원본과
    실제 재인코딩 결과물의 전/후 용량 비교. 인코딩 로그 페이지의 가시성
    배지용(2026-10-11 추가 - 불꽃야구 무한루프를 몇 시간 동안 못 알아챈
    사고 이후, 큐가 쌓이는 걸 한눈에 보려는 목적)."""
    pending_count = pending_bytes = done_count = done_bytes = 0
    comparisons = []
    probe_deadline = time.time() + MEDIA_PROBE_BUDGET_SEC
    library = None  # [완료] 원본이 있을 때만 한 번 만든다
    try:
        for p in ENCODING_HOLD_PATH.iterdir():
            if not p.is_file():
                continue
            size = p.stat().st_size
            if p.name.startswith("[완료] "):
                done_count += 1
                done_bytes += size
                stem = Path(p.name[len("[완료] "):]).stem
                if library is None:
                    library = _library_index()
                out = library.get(stem)
                if out is not None:
                    try:
                        before_info = _media_brief(p, probe_deadline) or {}
                        after_info = _media_brief(out, probe_deadline) or {}
                        comparisons.append({
                            "name": stem,
                            "before": size,
                            "after": out.stat().st_size,
                            "before_codec": before_info.get("codec"),
                            "before_bitrate": before_info.get("bitrate"),
                            "after_codec": after_info.get("codec"),
                            "after_bitrate": after_info.get("bitrate"),
                        })
                    except Exception:
                        pass
            else:
                pending_count += 1
                pending_bytes += size
    except Exception:
        app.logger.exception("인코딩 보류 폴더 통계 수집 실패")
    _save_probe_cache()

    total_before = sum(c["before"] for c in comparisons)
    total_after = sum(c["after"] for c in comparisons)
    return {
        "pending_count": pending_count, "pending_bytes": pending_bytes,
        "done_count": done_count, "done_bytes": done_bytes,
        "comparisons": comparisons,
        "total_before": total_before, "total_after": total_after,
    }


@app.route("/encode_hold_stats", methods=["GET"])
def encode_hold_stats():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    return jsonify({"success": True, **_encoding_hold_stats()})
_PARTIAL_DOWNLOAD_EXTS = (".crdownload", ".part", ".tmp", ".!qb")
_schedule_shutdown_state = {"lock": threading.Lock(), "active": False, "stop_event": None, "started_at": 0}


def _has_partial_downloads() -> bool:
    try:
        for p in DRIVE_ROOT_PATH.iterdir():
            if p.is_file() and p.name.lower().endswith(_PARTIAL_DOWNLOAD_EXTS):
                return True
    except Exception:
        app.logger.exception("Drive 루트 다운로드 중 파일 확인 실패")
    return False


def _is_encoding_in_progress() -> bool:
    # encode_all.ps1이 ".encoding.mp4" 임시파일을 "인코딩 완료" 폴더에 쓴다.
    # 로그 파일은 2026-10-11에 Program/Log로 옮겨서 ENCODE_LOG_PATH.parent와
    # 더 이상 같은 폴더가 아니므로, Drive 루트 마운트를 통해 직접 가리킨다.
    try:
        for p in (DRIVE_ROOT_PATH / "인코딩 완료").iterdir():
            if p.is_file() and p.name.lower().endswith(".encoding.mp4"):
                return True
    except Exception:
        app.logger.exception("인코딩 진행 여부 확인 실패")
    return False


def _tg_notify(text: str) -> None:
    try:
        from db import load_cfg as _load_cfg
        tg = _load_cfg().get("telegram", {})
        if not tg.get("enabled") or not tg.get("bot_token") or not tg.get("chat_id"):
            return
        requests.post(
            f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
            json={"chat_id": tg["chat_id"], "text": text}, timeout=10,
        )
    except Exception:
        app.logger.exception("PC예약종료 텔레그램 알림 실패")


# 텔레그램으로 "인코딩"이라고 회신하면 KCK-GAMING에서 인코딩을 원격 시작한다.
# PC가 꺼져있으면 먼저 WOL로 켜고 기다렸다가 시작한다. 인코딩이 끝나면
# encode_all.ps1 자체의 shutdownAfter가 있어서 PC는 알아서 꺼지므로, 여기서는
# 따로 종료를 예약하지 않는다. 인바운드 웹훅 없이 getUpdates 롱폴링만 쓰므로
# 포트포워딩/공인 도메인/SSL 없이도 동작한다.
_TG_ENCODE_TRIGGER_WORDS = ("인코딩",)


def _ensure_pc_on_and_trigger_encode(progress=None) -> tuple[bool, str]:
    """PC가 꺼져있으면 WOL로 켜고 최대 2분 기다렸다가, 켜지면(또는 이미
    켜져있으면) 인코딩을 원격 시작한다. 웹 버튼과 텔레그램 트리거가 공유.
    progress(선택): 중간 진행 상황을 알리고 싶을 때 쓰는 콜백(예: 텔레그램
    알림). 웹 버튼처럼 최종 결과만 필요하면 생략."""
    def _note(msg: str) -> None:
        if progress:
            progress(msg)

    if not _is_pc_online(GAMING_PC_HOST):
        _note("PC가 꺼져있어서 WOL로 켜는 중이에요. 부팅되면 인코딩을 시작할게요 (최대 2분 대기)...")
        try:
            _send_wol_packet(WOL_MAC, WOL_BROADCAST)
        except Exception as e:
            return False, f"WOL 패킷 전송 실패: {e}"
        waited = 0
        while waited < 120 and not _is_pc_online(GAMING_PC_HOST):
            time.sleep(5)
            waited += 5
        if not _is_pc_online(GAMING_PC_HOST):
            return False, "2분 기다렸는데 PC가 안 켜져요. 직접 확인해주세요."
        _note("PC 켜졌어요. 인코딩 시작할게요.")
        time.sleep(10)  # SSH 서비스가 완전히 올라올 시간 여유

    return _trigger_remote_encode()


def _handle_telegram_encode_request() -> None:
    with _run_encode_state["lock"]:
        elapsed = time.time() - _run_encode_state["last_triggered"]
        if elapsed < 30:
            _tg_notify(f"방금 요청했어요. {int(30 - elapsed)}초 후 다시 보내주세요.")
            return
        _run_encode_state["last_triggered"] = time.time()

    ok, message = _ensure_pc_on_and_trigger_encode(progress=_tg_notify)
    suffix = "\n끝나면 알아서 PC가 꺼질 거예요." if ok else ""
    _tg_notify(("✅ " if ok else "❌ ") + message + suffix)


def _telegram_listener_loop() -> None:
    offset = 0
    while True:
        try:
            from db import load_cfg as _load_cfg
            tg = _load_cfg().get("telegram", {})
            token = tg.get("bot_token")
            allowed_chat_id = str(tg.get("chat_id") or "")
            if not tg.get("enabled") or not token or not allowed_chat_id:
                time.sleep(30)
                continue

            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": 25}, timeout=35,
            )
            data = resp.json()
            if not data.get("ok"):
                time.sleep(5)
                continue

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat_id = str((msg.get("chat") or {}).get("id") or "")
                if chat_id != allowed_chat_id or not text:
                    continue
                if any(w in text for w in _TG_ENCODE_TRIGGER_WORDS):
                    threading.Thread(target=_handle_telegram_encode_request, daemon=True).start()
        except Exception:
            app.logger.exception("텔레그램 리스너 오류")
            time.sleep(10)


threading.Thread(target=_telegram_listener_loop, daemon=True).start()


def _schedule_shutdown_worker(stop_event: threading.Event) -> None:
    try:
        while not stop_event.is_set():
            if not _has_partial_downloads() and not _is_encoding_in_progress():
                if _is_pc_online(GAMING_PC_HOST):
                    try:
                        subprocess.run(
                            [
                                "ssh", "-i", GAMING_PC_SSH_KEY,
                                "-o", "StrictHostKeyChecking=accept-new",
                                "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                                f"{GAMING_PC_USER}@{GAMING_PC_HOST}",
                                'shutdown /s /t 300 /c "다운로드/인코딩 완료로 예약 종료"',
                            ],
                            capture_output=True, timeout=20,
                        )
                        _tg_notify("✅ 다운로드/인코딩이 모두 끝나서 KCK-GAMING을 5분 후 종료합니다. 취소하려면 '인코딩 관리' 페이지의 'PC 종료 취소' 버튼을 누르세요.")
                    except Exception:
                        app.logger.exception("PC예약종료 - 종료 명령 전송 실패")
                break
            stop_event.wait(30)
    finally:
        with _schedule_shutdown_state["lock"]:
            _schedule_shutdown_state["active"] = False
            _schedule_shutdown_state["stop_event"] = None


@app.route("/schedule_pc_shutdown", methods=["POST"])
def schedule_pc_shutdown():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    with _schedule_shutdown_state["lock"]:
        if _schedule_shutdown_state["active"]:
            return jsonify({"success": False, "message": "이미 예약되어 있어요."}), 409
        stop_event = threading.Event()
        _schedule_shutdown_state["active"] = True
        _schedule_shutdown_state["stop_event"] = stop_event
        _schedule_shutdown_state["started_at"] = time.time()

    threading.Thread(target=_schedule_shutdown_worker, args=(stop_event,), daemon=True).start()
    return jsonify({"success": True, "message": "예약했습니다. 다운로드/인코딩이 모두 끝나면 자동으로 PC를 종료해요."})


@app.route("/schedule_pc_shutdown/cancel", methods=["POST"])
def schedule_pc_shutdown_cancel():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    with _schedule_shutdown_state["lock"]:
        if not _schedule_shutdown_state["active"]:
            return jsonify({"success": True, "message": "예약된 게 없어요.", "active": False})
        if _schedule_shutdown_state["stop_event"] is not None:
            _schedule_shutdown_state["stop_event"].set()
        _schedule_shutdown_state["active"] = False
        _schedule_shutdown_state["stop_event"] = None

    return jsonify({"success": True, "message": "예약을 취소했습니다.", "active": False})


@app.route("/schedule_pc_shutdown/status", methods=["GET"])
def schedule_pc_shutdown_status():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    with _schedule_shutdown_state["lock"]:
        active = _schedule_shutdown_state["active"]
        started_at = _schedule_shutdown_state["started_at"]
    return jsonify({
        "success": True, "active": active, "started_at": started_at,
        "downloading": _has_partial_downloads() if active else None,
        "encoding": _is_encoding_in_progress() if active else None,
    })


def _load_or_create_session_secret() -> str:
    """세션 쿠키 서명용 비밀키. 관리자 비밀번호와 완전히 무관한 별도 랜덤값이다
    (예전엔 쿠키 값 자체가 "ok:"+비밀번호 라서, 쿠키가 유출되면 곧 비밀번호
    유출이었음). DATA_DIR에 한 번 생성해두고 재시작 후에도 재사용해서,
    컨테이너를 재시작해도 기존 로그인 세션이 전부 끊기지 않게 한다."""
    key_path = DATA_DIR / ".session_secret"
    try:
        if key_path.exists():
            existing = key_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except Exception:
        pass
    key = secrets.token_hex(32)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(key, encoding="utf-8")
        key_path.chmod(0o600)
    except Exception:
        pass  # 저장 실패해도 이번 프로세스 수명 동안은 메모리의 key로 동작
    return key


_SESSION_SECRET = _load_or_create_session_secret()
_session_serializer = URLSafeTimedSerializer(_SESSION_SECRET, salt="media-router-admin-session")
SESSION_MAX_AGE = 30 * 24 * 3600  # 쿠키 자체 max_age(12h/30d)와 별개로, 서명 토큰이 유효한 상한

# 로그인 브루트포스 방지: IP별로 짧은 시간 내 실패가 누적되면 일정 시간 잠금.
# (완벽한 방어는 아니지만 - 리버스 프록시 뒤라면 X-Forwarded-For가 스푸핑될 수
# 있음 - 무차별 대입 스크립트 정도는 충분히 막는다.)
_login_lock = threading.Lock()
_login_failures = {}  # ip -> 실패 타임스탬프 리스트
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300   # 이 시간 안에
LOGIN_LOCKOUT_SECONDS = 60   # 이만큼 잠금


def _client_ip(req) -> str:
    fwd = req.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return req.remote_addr or "unknown"


def _login_blocked_seconds(ip: str) -> int:
    """이 IP가 잠겨있으면 남은 초를, 아니면 0을 반환."""
    now = time.time()
    with _login_lock:
        fails = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
        _login_failures[ip] = fails
    if len(fails) < LOGIN_MAX_ATTEMPTS:
        return 0
    trigger_ts = fails[-LOGIN_MAX_ATTEMPTS]
    remain = LOGIN_LOCKOUT_SECONDS - (now - trigger_ts)
    return max(0, int(round(remain)))


def _register_login_failure(ip: str) -> None:
    with _login_lock:
        _login_failures.setdefault(ip, []).append(time.time())


def _clear_login_failures(ip: str) -> None:
    with _login_lock:
        _login_failures.pop(ip, None)

# TMDB API KEY 초기화 함수
def get_tmdb_api_key():
    """환경변수 또는 설정에서 TMDB API 키 가져오기"""
    # 우선 환경변수 확인
    env_key = os.getenv("TMDB_API_KEY", "").strip()
    if env_key:
        return env_key
    # 설정에서 확인
    try:
        cfg = load_cfg()
        return cfg.get("tmdb_api_key", "").strip()
    except:
        return ""

# 초기 값 (나중에 동적으로 업데이트됨)
TMDB_API_KEY = get_tmdb_api_key()


def current_weekday() -> int:
    return datetime.now(APP_TZ).weekday()

def authed(req):
    if not PASSWORD:
        return True
    if req.path == "/health":
        return True
    token = req.cookies.get("media_admin", "")
    if token:
        try:
            data = _session_serializer.loads(token, max_age=SESSION_MAX_AGE)
            if data.get("a") is True:
                return True
        except (BadSignature, SignatureExpired):
            pass
    return redirect("/login")

def split_title_and_year(name: str) -> tuple[str, int | None]:
    cleaned = (name or "").strip()
    m = re.search(r"\((\d{4})\)\s*$", cleaned)
    if m:
        year = int(m.group(1))
        title = re.sub(r"\s*\(\d{4}\)\s*$", "", cleaned).strip()
        return title, year
    return cleaned, None


def tmdb_auto_total_episodes(subfolder: str) -> int | None:
    api_key = get_tmdb_api_key()
    
    if not api_key:
        return None

    title, year = split_title_and_year(subfolder)
    if not title:
        return None

    try:
        params = {
            "api_key": api_key,
            "query": title,
            "language": "ko-KR",
        }
        if year:
            params["first_air_date_year"] = year

        res = requests.get("https://api.themoviedb.org/3/search/tv", params=params, timeout=8)
        if res.status_code != 200:
            return None

        results = res.json().get("results", [])
        if not results:
            return None

        title_key = title.lower().strip()
        picked = results[0]
        for item in results:
            ko_name = (item.get("name") or "").lower().strip()
            origin_name = (item.get("original_name") or "").lower().strip()
            if ko_name == title_key or origin_name == title_key:
                picked = item
                break

        tv_id = picked.get("id")
        if not tv_id:
            return None

        detail = requests.get(
            f"https://api.themoviedb.org/3/tv/{tv_id}",
            params={"api_key": api_key, "language": "ko-KR"},
            timeout=8,
        )
        if detail.status_code != 200:
            return None

        total = detail.json().get("number_of_episodes")
        if isinstance(total, int) and total > 0:
            return total
    except Exception:
        return None

    return None

def tmdb_search_info(query: str) -> dict:
    """TMDB에서 드라마 정보 검색"""
    # 최신 TMDB API 키 가져오기
    api_key = get_tmdb_api_key()
    
    if not api_key or not query:
        return {"success": False, "message": "TMDB API KEY가 없습니다. 설정 페이지에서 입력하세요."}
    
    query = query.strip()
    if not query:
        return {"success": False, "message": "검색어를 입력하세요."}
    
    try:
        # TMDB 검색
        params = {
            "api_key": api_key,
            "query": query,
            "language": "ko-KR",
        }
        
        res = requests.get("https://api.themoviedb.org/3/search/tv", params=params, timeout=8)
        if res.status_code == 401:
            return {"success": False, "message": "TMDB API 키가 유효하지 않습니다. 설정에서 키를 확인해주세요."}
        if res.status_code != 200:
            return {"success": False, "message": f"TMDB 검색 실패 (HTTP {res.status_code})"}
        
        results = res.json().get("results", [])
        if not results:
            return {"success": False, "message": "검색 결과가 없습니다."}
        
        # 첫 번째 결과 사용
        show = results[0]
        tv_id = show.get("id")
        ko_name = show.get("name", "")
        origin_name = show.get("original_name", "")
        air_date = show.get("first_air_date", "")
        
        # 년도 추출
        year = None
        if air_date:
            year = int(air_date.split("-")[0])
        
        # 폴더명 생성 (title (year) 형식)
        folder_name = ko_name if ko_name else origin_name
        if year:
            folder_name = f"{folder_name} ({year})"
        
        # 상세 정보 조회
        detail_res = requests.get(
            f"https://api.themoviedb.org/3/tv/{tv_id}",
            params={"api_key": api_key, "language": "ko-KR"},
            timeout=8,
        )
        
        total_episodes = None
        days = []
        
        if detail_res.status_code == 200:
            detail = detail_res.json()
            total_episodes = detail.get("number_of_episodes")
            
            # 방송 요일 정보 추출 (가능한 경우)
            networks = detail.get("networks", [])
            # networks에는 방송 요일 정보가 없으므로, air_date와 episode_run_time 등으로 추정만 가능
            # 정확한 요일 정보는 TMDB에서 직접 제공하지 않으므로 사용자가 수정하도록
        
        return {
            "success": True,
            "title": ko_name or origin_name,
            "year": year,
            "folder_name": folder_name,
            "total_episodes": total_episodes,
            "aired_date": air_date,
            "original_name": origin_name,
        }
    
    except requests.exceptions.Timeout:
        return {"success": False, "message": "TMDB 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."}
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "TMDB 서버에 연결할 수 없습니다. 인터넷/DNS 상태를 확인해주세요."}
    except requests.exceptions.RequestException:
        return {"success": False, "message": "TMDB 요청 중 네트워크 오류가 발생했습니다."}
    except Exception:
        return {"success": False, "message": "TMDB 처리 중 오류가 발생했습니다."}

def safe_int(value, default):
    """빈 문자열/None/숫자가 아닌 값이 와도 죽지 않고 default로 대체"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default

def parse_rename_rules_conf(path: Path):
    """rename_rules.conf(keyword|from_prefix|to_prefix 형식)를 파싱.
    drama_name.sh의 apply_rename_rules()와 동일한 규칙으로 라인을 해석한다:
    빈 줄은 무시, '#'로 시작하는 줄은 주석(메모)으로 취급."""
    memo_lines = []
    rules = []
    if not path.exists():
        return memo_lines, rules
    text = path.read_text(encoding="utf-8", errors="replace")
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped == "":
            continue
        if stripped.startswith("#"):
            memo_lines.append(stripped)
            continue
        # bash의 `IFS='|' read -r keyword from_prefix to_prefix`와 동일하게
        # 세 번째 필드는 남은 '|' 포함 나머지 전체를 가진다.
        parts = raw_line.split("|", 2)
        while len(parts) < 3:
            parts.append("")
        keyword, from_prefix, to_prefix = parts
        rules.append({"keyword": keyword, "from_prefix": from_prefix, "to_prefix": to_prefix})
    return memo_lines, rules


def render_rename_rules_conf(memo_text: str, rules: list) -> str:
    """편집 폼 데이터를 rename_rules.conf 텍스트로 변환.
    from_prefix/to_prefix의 앞쪽 공백은 의미가 있어 보존하고(예: ' 시즌2'),
    뒤쪽 공백은 drama_name.sh가 어차피 trim하므로 함께 제거한다."""
    lines = []
    for m in (memo_text or "").splitlines():
        m2 = m.rstrip()
        if m2.strip() == "":
            continue
        if not m2.lstrip().startswith("#"):
            m2 = "# " + m2.strip()
        lines.append(m2)
    if lines:
        lines.append("")
    for r in rules:
        from_prefix = (r.get("from_prefix") or "").rstrip()
        if not from_prefix:
            continue
        keyword = (r.get("keyword") or "").strip()
        to_prefix = (r.get("to_prefix") or "").rstrip()
        lines.append(f"{keyword}|{from_prefix}|{to_prefix}")
    return "\n".join(lines) + "\n"


def parse_bt4g_keywords(path: Path) -> list[str]:
    """bt4g_keywords.txt(한 줄에 검색어 하나)를 파싱. bt4g.py가 읽는 형식과 동일."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def render_bt4g_keywords(keywords: list[str]) -> str:
    lines = [k.strip() for k in keywords if k.strip()]
    return "\n".join(lines) + ("\n" if lines else "")


def rule_key(rule):
    raw = f"{(rule.get('category') or '').strip()}|" \
          f"{(rule.get('pattern') or '').strip()}|" \
          f"{(rule.get('subfolder') or '').strip()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def extract_episode_number(filename: str):
    """파일명에서 에피소드 번호 추출 (E01, E1, EP01, S01E05, 01화 등)"""
    # S01E05, S1E5 같은 패턴 (E 뒤 숫자 추출)
    match = re.search(r'[Ss]\d+[Ee](\d+)', filename)
    if match:
        return int(match.group(1))
    
    # E01, E1, EP01, EP1 같은 패턴
    match = re.search(r'\b[Ee][Pp]?\s*(\d+)\b', filename)
    if match:
        return int(match.group(1))
    
    # 01화, 1화 같은 패턴
    match = re.search(r'(\d+)\s*화', filename)
    if match:
        return int(match.group(1))
    
    return None

def get_next_episode(rule):
    """다음 받을 에피소드 번호 계산 (드라마 및 예능)"""
    category = rule.get("category")
    
    if category == "드라마":
        total = rule.get("total_episodes")
        if not total:
            return None
        received = rule.get("received_episodes") or []
        if not isinstance(received, list):
            received = []
        if not received:
            return 1
        max_received = max(received)
        if max_received >= total:
            return None  # 완료
        return max_received + 1
    
    elif category == "예능":
        # 예능: last_episode + 1
        last_episode = rule.get("last_episode")
        if last_episode is not None:
            return last_episode + 1
        return None
    
    return None

# load_cfg and save_cfg are imported from db module

def sort_rules_for_list(rules, sort_key, sort_dir):
    # 기본: 요일(가장 이른 요일) → 카테고리 → subfolder
    def day_rank(r):
        days = r.get("days") or []
        return min((DAY_ORDER.get(d, 999) for d in days), default=999)

    def key_default(r):
        return (day_rank(r), (r.get("category") or ""), (r.get("subfolder") or ""))

    def key_category(r):
        return ((r.get("category") or "").lower(), (r.get("subfolder") or "").lower())

    def key_pattern(r):
        return ((r.get("pattern") or "").lower(), (r.get("subfolder") or "").lower())

    def key_subfolder(r):
        return ((r.get("subfolder") or "").lower(), (r.get("category") or "").lower())

    def key_days(r):
        # 요일 문자열(가나다순)이 아니라 월화수목금토일 순서로 정렬
        days = r.get("days") or []
        day_ranks = tuple(sorted(DAY_ORDER.get(d, 999) for d in days)) if days else (999,)
        return (day_ranks, (r.get("category") or "").lower())

    keymap = {
        "category": key_category,
        "pattern":  key_pattern,
        "subfolder": key_subfolder,
        "days":     key_days,
        "default":  key_default,
    }
    keyfunc = keymap.get(sort_key, key_default)
    reverse = (sort_dir == "desc")
    return sorted(rules, key=keyfunc, reverse=reverse)

@app.after_request
def add_security_headers(r):
    r.headers["Content-Security-Policy"] = "upgrade-insecure-requests"
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    r.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return r

@app.route("/health")
def health(): return "ok", 200

@app.route("/", methods=["GET"])
def index():
    auth = authed(request)
    if auth is not True:
        return auth

    cfg = load_cfg()
    mode = request.args.get("mode", "check").strip()
    sort_key = request.args.get("sort", "default").strip()
    sort_dir = request.args.get("dir", "asc").strip()
    if sort_dir not in ("asc","desc"): sort_dir = "asc"

    w_today = current_weekday()
    today_name, yest_name = WEEKDAYS[w_today], WEEKDAYS[(w_today-1)%7]
    two_days_ago_name = WEEKDAYS[(w_today-2)%7]
    selected_day = request.args.get("day","").strip()
    selected_cat = request.args.get("cat","").strip()
    hide_no_days = request.args.get("hide_no_days", "1").strip()  # 기본값: 숨김
    edit_id_raw = request.args.get("edit_id", "").strip()
    try:
        edit_id = int(edit_id_raw) if edit_id_raw else None
    except ValueError:
        edit_id = None
    rules = cfg.get("rules", [])
    
    # 전체관리 모드 기본값 설정
    if mode != "check":
        if not selected_day:
            selected_day = "전체"
        if not selected_cat:
            selected_cat = "전체"

    if mode == "check":
        # 그저께+어제+오늘 중 미처리 항목을 '요일별 항목'으로 분해
        if selected_day in ("", "__three__"):
            days_to_show, selected_ui = [two_days_ago_name, yest_name, today_name], "__three__"
        else:
            days_to_show=[selected_day] if selected_day in WEEKDAYS else [today_name]
            selected_ui=selected_day if selected_day in WEEKDAYS else today_name

        entries=[]
        for i, r in enumerate(rules):
            days = r.get("days") or []
            if not days: continue
            umap = r.get("updated_map") or {}
            for d in days_to_show:
                if d not in days: continue
                if umap.get(d, "N") != "Y":
                    item = dict(r)
                    item["rkday"] = f"{rule_key(r)}|{d}"
                    item["day"] = d
                    item["_idx"] = r.get("id", i)  # DB id 우선 사용 (점검/삭제 정확도 보장)
                    item["next_episode"] = get_next_episode(r)  # 다음 에피소드 번호
                    # 릴리즈 정보도 포함 (dict(r)로 복사되지만 명시적으로 확인)
                    if "release" in r:
                        item["release"] = r["release"]
                    entries.append(item)

        # 체크모드는 고정 정렬(그제→어제→오늘 순서, 오늘이 항상 마지막)
        def check_key(x):
            day = x.get("day")
            # 오늘 요일이면 가장 큰 값으로 설정하여 마지막에 오도록
            if day == today_name:
                day_order = 999
            # 그제 요일이면 가장 작은 값
            elif day == two_days_ago_name:
                day_order = 0
            # 어제 요일이면 중간 값
            elif day == yest_name:
                day_order = 1
            # 그 외 요일은 일반 순서 사용
            else:
                day_order = DAY_ORDER.get(day, 998)
            return (day_order, (x.get("category") or ""), (x.get("subfolder") or ""))
        entries = sorted(entries, key=check_key)
        return render_template("index.html",
            authed=True, cfg=cfg, weekdays=WEEKDAYS,
            selected_day=selected_ui, selected_cat=selected_cat,
            rules=entries, mode="check",
            today_name=today_name, yest_name=yest_name, two_days_ago_name=two_days_ago_name, categories=CATEGORIES,
            sort_key=sort_key, sort_dir=sort_dir, edit_id=edit_id)

    # 전체 관리: 필터링 + 정렬
    def rule_match(r):
        days = r.get("days") or []
        has_no_days = len(days) == 0
        
        # 요일이 없는 규칙 숨김 처리
        if hide_no_days == "1" and has_no_days:
            return False
        
        day_ok=(not selected_day or selected_day=="전체" or (days and selected_day in days))
        cat_ok=(not selected_cat or selected_cat=="전체" or r.get("category")==selected_cat)
        return day_ok and cat_ok

    filtered=[{**r,"_idx":r["id"]} for r in rules if rule_match(r)]
    filtered=sort_rules_for_list(filtered, sort_key, sort_dir)

    return render_template("index.html",
        authed=True, cfg=cfg, weekdays=WEEKDAYS,
        selected_day=selected_day, selected_cat=selected_cat, hide_no_days=hide_no_days,
        rules=filtered, mode="",
        today_name=today_name, yest_name=yest_name, two_days_ago_name=two_days_ago_name, categories=CATEGORIES,
        sort_key=sort_key, sort_dir=sort_dir, edit_id=edit_id)

@app.route("/check", methods=["POST"])
def check_action():
    auth = authed(request)
    if auth is not True:
        return auth
    try:
        keys = set(request.form.getlist("rkday"))
        cfg = load_cfg()
        changed = False
        for r in cfg["rules"]:
            rk = rule_key(r)
            umap = r.get("updated_map") or {}
            for wd in WEEKDAYS:
                if f"{rk}|{wd}" in keys and umap.get(wd) != "Y":
                    umap[wd] = "Y"
                    changed = True
            r["updated_map"] = umap
        if changed: 
            save_cfg(cfg)
        day = request.form.get("day", "").strip()
        if day not in WEEKDAYS:
            day = "__three__"
        return redirect(f"/?mode=check&day={day}")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/reset_day", methods=["POST"])
def reset_day():
    auth = authed(request)
    if auth is not True:
        return auth
    try:
        target_day = request.form.get("target_day", "").strip()
        cfg = load_cfg()
        changed = False
        if target_day in WEEKDAYS:
            for r in cfg["rules"]:
                if target_day not in (r.get("days") or []):
                    continue
                umap = r.get("updated_map") or {}
                if umap.get(target_day) == "Y":
                    umap[target_day] = "N"
                    changed = True
                r["updated_map"] = umap
        if changed:
            save_cfg(cfg)
        day = request.form.get("day", "").strip()
        if day not in WEEKDAYS:
            day = "__three__"
        return redirect(f"/?mode=check&day={day}")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/check_episodes", methods=["POST"])
def check_episodes():
    try:
        auth = authed(request)
        if auth is not True:
            return auth
        idx = int(request.form.get("idx", "-1"))
        cfg = load_cfg()
        rule = next((r for r in cfg.get("rules", []) if r.get("id") == idx), None)
        if rule is not None:
            category = rule.get("category", "")
            
            if category in ("드라마", "예능"):
                # updated_map 보존 (check_episodes는 updated_map을 건드리지 않아야 함)
                original_updated_map = rule.get("updated_map", {}).copy() if isinstance(rule.get("updated_map"), dict) else {}
                
                base_paths = cfg.get("base_paths", {})
                subfolder = rule.get("subfolder", "")
                
                base_dir = base_paths.get(category)
                if base_dir and subfolder:
                    target_dir = Path(base_dir) / subfolder
                    
                    if target_dir.exists() and target_dir.is_dir():
                        # 폴더 내 모든 파일에서 에피소드 번호 추출
                        episodes_found = set()
                        for file_path in target_dir.iterdir():
                            if file_path.is_file():
                                ep_num = extract_episode_number(file_path.name)
                                if ep_num:
                                    episodes_found.add(ep_num)
                        
                        if episodes_found:
                            episodes_list = sorted(list(episodes_found))
                            
                            if category == "드라마":
                                # 드라마: 모든 에피소드 저장
                                rule["received_episodes"] = episodes_list
                                # updated_map 보존
                                rule["updated_map"] = original_updated_map
                                save_cfg(cfg)
                                return jsonify({
                                    "success": True,
                                    "message": f"에피소드 {len(episodes_list)}개 발견 및 업데이트 완료",
                                    "episodes": episodes_list
                                })
                            elif category == "예능":
                                # 예능: 최종 에피소드만 저장
                                last_episode = max(episodes_list)
                                rule["last_episode"] = last_episode
                                # updated_map 보존
                                rule["updated_map"] = original_updated_map
                                save_cfg(cfg)
                                return jsonify({
                                    "success": True,
                                    "message": f"최종 에피소드 E{last_episode} 발견 및 업데이트 완료",
                                    "last_episode": last_episode
                                })
                        else:
                            return jsonify({
                                "success": False,
                                "message": "에피소드 번호를 찾을 수 없습니다"
                            })
                    else:
                        return jsonify({
                            "success": False,
                            "message": f"폴더를 찾을 수 없습니다: {target_dir}"
                        })
        
        return jsonify({
            "success": False,
            "message": "드라마 또는 예능 규칙이 아닙니다"
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"에러가 발생했습니다: {str(e)}"
        }), 500

@app.route("/login", methods=["GET"])
def login_page():
    error_code = request.args.get("error", "")
    wait_seconds = 0
    if error_code == "2":
        try:
            wait_seconds = max(0, int(request.args.get("wait", "0")))
        except ValueError:
            wait_seconds = 0
    return render_template(
        "login.html",
        has_password=bool(PASSWORD),
        error=(error_code == "1"),
        locked=(error_code == "2"),
        wait_seconds=wait_seconds,
    )

@app.route("/login", methods=["POST"])
def login():
    pw=request.form.get("pw","")
    remember = request.form.get("remember") == "1"
    if not PASSWORD:
        return redirect("/")

    ip = _client_ip(request)
    blocked_for = _login_blocked_seconds(ip)
    if blocked_for > 0:
        return redirect(f"/login?error=2&wait={blocked_for}")

    if hmac.compare_digest(pw, PASSWORD):
        _clear_login_failures(ip)
        resp=make_response(redirect("/"))
        max_age = 30 * 24 * 3600 if remember else 12 * 3600
        token = _session_serializer.dumps({"a": True})
        resp.set_cookie("media_admin", token, max_age=max_age, httponly=True, samesite="Lax", secure=request.is_secure)
        return resp

    _register_login_failure(ip)
    blocked_for = _login_blocked_seconds(ip)  # 방금 실패로 임계치를 넘겼으면 바로 잠금 안내
    if blocked_for > 0:
        return redirect(f"/login?error=2&wait={blocked_for}")
    return redirect("/login?error=1")

@app.route("/add", methods=["POST"])
def add():
    auth = authed(request)
    if auth is not True:
        return auth
    cfg = load_cfg()
    pat_raw = request.form.get("pattern", "").strip()
    pattern = pat_raw
    pat_or_raw = request.form.get("pattern_or", "").strip()
    pattern_or = pat_or_raw if pat_or_raw else None
    pat2_raw = request.form.get("pattern2", "").strip()
    pattern2 = pat2_raw if pat2_raw else None
    pat2_or_raw = request.form.get("pattern2_or", "").strip()
    pattern2_or = pat2_or_raw if pat2_or_raw else None
    exclude_raw = request.form.get("exclude_pattern", "").strip()
    exclude_pattern = exclude_raw if exclude_raw else None
    sel_days = request.form.getlist("days")

    rule = {
        "category": request.form.get("category", "").strip(),
        "pattern":  pattern,
        "subfolder": request.form.get("subfolder", "").strip(),
        "days": [d for d in WEEKDAYS if d in sel_days],
        "updated": "N",
        "updated_map": {d: "N" for d in sel_days if d in WEEKDAYS},
    }
    
    if pattern_or:
        rule["pattern_or"] = pattern_or
    if pattern2:
        rule["pattern2"] = pattern2
    if pattern2_or:
        rule["pattern2_or"] = pattern2_or
    if exclude_pattern:
        rule["exclude_pattern"] = exclude_pattern
    
    # 릴리즈 정보 추가 (선택사항)
    release = request.form.get("release", "").strip()
    if release:
        rule["release"] = release
    
    # 드라마인 경우 total_episodes 설정
    if rule["category"] == "드라마":
        total_eps = request.form.get("total_episodes", "").strip()
        if total_eps:
            try:
                rule["total_episodes"] = int(total_eps)
            except ValueError:
                pass
        else:
            auto_total = tmdb_auto_total_episodes(rule["subfolder"])
            if auto_total:
                rule["total_episodes"] = auto_total
        rule.setdefault("received_episodes", [])
        epd_raw = request.form.get("episodes_per_day", "").strip()
        if epd_raw:
            try:
                epd = int(epd_raw)
                if epd >= 1:
                    rule["episodes_per_day"] = epd
            except ValueError:
                pass
    if not rule["category"] or not rule["pattern"] or not rule["subfolder"]:
        return abort(400)

    cfg.setdefault("rules", []).append(rule)
    save_cfg(cfg)
    
    # 현재 모드와 필터 조건 유지
    mode = request.form.get("mode", "").strip()
    day = request.form.get("day", "").strip()
    cat = request.form.get("cat", "").strip()
    sort = request.form.get("sort", "default").strip()
    sort_dir = request.form.get("dir", "asc").strip()
    hide_no_days = request.form.get("hide_no_days", "1").strip()
    
    params = []
    # mode는 항상 추가 (빈 문자열이어도 전체 관리 모드로 감)
    params.append(f"mode={mode if mode else ''}")
    if day: params.append(f"day={day}")
    if cat: params.append(f"cat={cat}")
    if sort != "default": params.append(f"sort={sort}")
    if sort_dir != "asc": params.append(f"dir={sort_dir}")
    # 요일 없는 규칙 표시/숨김 필터도 항상 유지
    params.append(f"hide_no_days={hide_no_days}")

    redirect_url = "/" + ("?" + "&".join(params) if params else "")
    return redirect(redirect_url)

@app.route("/delete", methods=["POST"])
def delete():
    auth = authed(request)
    if auth is not True:
        return auth
    idx=int(request.form.get("idx","-1"))
    cfg=load_cfg()
    cfg["rules"] = [r for r in cfg["rules"] if r.get("id") != idx]
    save_cfg(cfg)
    
    # 현재 모드와 필터 조건 유지
    mode = request.form.get("mode", "").strip()
    day = request.form.get("day", "").strip()
    cat = request.form.get("cat", "").strip()
    sort = request.form.get("sort", "default").strip()
    sort_dir = request.form.get("dir", "asc").strip()
    hide_no_days = request.form.get("hide_no_days", "1").strip()
    
    params = []
    # mode는 항상 추가 (빈 문자열이어도 전체 관리 모드로 감)
    params.append(f"mode={mode if mode else ''}")
    if day: params.append(f"day={day}")
    if cat: params.append(f"cat={cat}")
    if sort != "default": params.append(f"sort={sort}")
    if sort_dir != "asc": params.append(f"dir={sort_dir}")
    # 요일 없는 규칙 표시/숨김 필터도 항상 유지
    params.append(f"hide_no_days={hide_no_days}")

    redirect_url = "/" + ("?" + "&".join(params) if params else "")
    return redirect(redirect_url)

@app.route("/settings", methods=["GET", "POST"])
def settings():
    auth = authed(request)
    if auth is not True:
        return auth
    cfg = load_cfg()
    saved = False
    error = None
    if request.method == "POST":
        try:
            # TMDB API Key
            tmdb_key = request.form.get("tmdb_api_key", "").strip()
            clear_tmdb_key = request.form.get("clear_tmdb_key") == "1"
            if tmdb_key:
                cfg["tmdb_api_key"] = tmdb_key
            elif clear_tmdb_key and "tmdb_api_key" in cfg:
                del cfg["tmdb_api_key"]

            # telegram (빈 값이면 기존 저장값 유지)
            old_telegram = cfg.get("telegram") or {}
            bot_token_input = request.form.get("bot_token", "").strip()
            chat_id_input = request.form.get("chat_id", "").strip()
            cfg["telegram"] = {
                "bot_token": bot_token_input if bot_token_input else old_telegram.get("bot_token", ""),
                "chat_id": chat_id_input if chat_id_input else old_telegram.get("chat_id", ""),
                "enabled": request.form.get("tg_enabled") == "1",
            }
            # paths.sources
            sources_raw = request.form.get("sources", "")
            sources = [s.strip() for s in sources_raw.splitlines() if s.strip()]
            cfg.setdefault("paths", {})
            cfg["paths"]["sources"] = sources
            # base_paths
            base_paths = {}
            for cat in CATEGORIES:
                v = request.form.get(f"base_{cat}", "").strip()
                if v:
                    base_paths[cat] = v
            cfg["base_paths"] = base_paths
            # ownership (빈 값/비숫자 입력이 와도 기존 값 또는 기본값으로 안전하게 대체)
            old_ownership = cfg.get("ownership") or {}
            cfg["ownership"] = {
                "apply": request.form.get("own_apply") == "1",
                "user": request.form.get("own_user", "").strip(),
                "group": request.form.get("own_group", "").strip(),
                "file_mode": safe_int(request.form.get("own_file_mode"), old_ownership.get("file_mode", 664)),
                "dir_mode": safe_int(request.form.get("own_dir_mode"), old_ownership.get("dir_mode", 775)),
                "setgid_dirs": request.form.get("own_setgid") == "1",
                "enforce_inherit": request.form.get("own_enforce") == "1",
            }
            save_cfg(cfg)
            saved = True
        except Exception as e:
            app.logger.exception("설정 저장 중 오류")
            error = str(e)
    return render_template("index.html",
        authed=True, cfg=cfg, weekdays=WEEKDAYS,
        selected_day="", selected_cat="", hide_no_days="1",
        rules=[], mode="settings",
        today_name=WEEKDAYS[current_weekday()], yest_name=WEEKDAYS[(current_weekday()-1)%7],
        two_days_ago_name=WEEKDAYS[(current_weekday()-2)%7], categories=CATEGORIES,
        sort_key="default", sort_dir="asc", edit_id=None, saved=saved, error=error)

@app.route("/rename_rules", methods=["GET", "POST"])
def rename_rules():
    auth = authed(request)
    if auth is not True:
        return auth

    saved = False
    error = None

    if request.method == "POST":
        try:
            memo_text = request.form.get("memo", "")
            keywords = request.form.getlist("row_keyword")
            froms = request.form.getlist("row_from")
            tos = request.form.getlist("row_to")
            rules = []
            for i in range(len(froms)):
                rules.append({
                    "keyword": keywords[i] if i < len(keywords) else "",
                    "from_prefix": froms[i],
                    "to_prefix": tos[i] if i < len(tos) else "",
                })
            content = render_rename_rules_conf(memo_text, rules)

            RENAME_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
            if RENAME_RULES_PATH.exists():
                backup_path = RENAME_RULES_PATH.with_suffix(RENAME_RULES_PATH.suffix + ".bak")
                backup_path.write_text(
                    RENAME_RULES_PATH.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
            RENAME_RULES_PATH.write_text(content, encoding="utf-8")
            saved = True
        except Exception as e:
            app.logger.exception("rename_rules.conf 저장 중 오류")
            error = str(e)

    try:
        memo_lines, rules = parse_rename_rules_conf(RENAME_RULES_PATH)
        load_error = None
    except Exception as e:
        app.logger.exception("rename_rules.conf 로딩 중 오류")
        memo_lines, rules = [], []
        load_error = str(e)

    return render_template(
        "rename_rules.html",
        authed=True,
        memo_text="\n".join(memo_lines),
        rules=rules,
        saved=saved,
        error=error or load_error,
        conf_path=str(RENAME_RULES_PATH),
        conf_exists=RENAME_RULES_PATH.exists(),
    )


@app.route("/bt4g_keywords", methods=["GET", "POST"])
def bt4g_keywords():
    auth = authed(request)
    if auth is not True:
        return auth

    saved = False
    error = None

    if request.method == "POST":
        try:
            rows = [k.strip() for k in request.form.getlist("row_keyword")]
            content = render_bt4g_keywords(rows)

            BT4G_KEYWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
            if BT4G_KEYWORDS_PATH.exists():
                # 백업은 소스 파일 옆이 아니라 DATA_DIR(영구 볼륨)에 남긴다.
                # docker-compose가 bt4g_keywords.txt "파일 하나만" 바인드 마운트하므로,
                # 옆에 .bak을 쓰면 컨테이너의 임시 레이어에만 생겼다가 재배포 시 사라진다.
                backup_path = DATA_DIR / "bt4g_keywords.txt.bak"
                backup_path.write_text(
                    BT4G_KEYWORDS_PATH.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
            BT4G_KEYWORDS_PATH.write_text(content, encoding="utf-8")
            saved = True
        except Exception as e:
            app.logger.exception("bt4g_keywords.txt 저장 중 오류")
            error = str(e)

    try:
        keywords = parse_bt4g_keywords(BT4G_KEYWORDS_PATH)
        load_error = None
    except Exception as e:
        app.logger.exception("bt4g_keywords.txt 로딩 중 오류")
        keywords = []
        load_error = str(e)

    return render_template(
        "bt4g_keywords.html",
        authed=True,
        keywords=keywords,
        saved=saved,
        error=error or load_error,
        conf_path=str(BT4G_KEYWORDS_PATH),
        conf_exists=BT4G_KEYWORDS_PATH.exists(),
    )


@app.route("/edit", methods=["GET","POST"])
def edit():
    auth = authed(request)
    if auth is not True:
        return auth
    cfg = load_cfg()
    if request.method == "GET":
        ...
    else:
        idx = int(request.form.get("idx","-1"))
        old_rule = next((r for r in cfg.get("rules", []) if r.get("id") == idx), None)
        if old_rule is not None:
            pat_raw = request.form.get("pattern", "").strip()
            pattern = pat_raw
            pat_or_raw = request.form.get("pattern_or", "").strip()
            pattern_or = pat_or_raw if pat_or_raw else None
            pat2_raw = request.form.get("pattern2", "").strip()
            pattern2 = pat2_raw if pat2_raw else None
            pat2_or_raw = request.form.get("pattern2_or", "").strip()
            pattern2_or = pat2_or_raw if pat2_or_raw else None
            exclude_raw = request.form.get("exclude_pattern", "").strip()
            exclude_pattern = exclude_raw if exclude_raw else None
            sel_days = request.form.getlist("days")

            # 유지되는 요일의 체크 상태(updated_map)는 보존하고, 새로 추가된
            # 요일만 N으로 시작한다 (규칙만 손봤는데 오늘 체크가 날아가는 것 방지)
            old_umap = old_rule.get("updated_map") or {}
            new_rule = {
                "category": request.form.get("category","").strip(),
                "pattern":  pattern,
                "subfolder": request.form.get("subfolder","").strip(),
                "days": [d for d in WEEKDAYS if d in sel_days],
                "updated": "N",
                "updated_map": {
                    d: (old_umap.get(d, "N") if old_umap.get(d) in ("Y", "N") else "N")
                    for d in sel_days if d in WEEKDAYS
                },
            }
            
            if pattern_or:
                new_rule["pattern_or"] = pattern_or
            if pattern2:
                new_rule["pattern2"] = pattern2
            if pattern2_or:
                new_rule["pattern2_or"] = pattern2_or
            if exclude_pattern:
                new_rule["exclude_pattern"] = exclude_pattern
            
            # 릴리즈 정보 추가/수정/삭제 (선택사항)
            # 편집 폼의 release 입력칸은 항상 값을 전송하므로, 빈 문자열은
            # "지우고 저장"으로 취급해 old_rule 값을 복사하지 않는다.
            release = request.form.get("release", "").strip()
            if release:
                new_rule["release"] = release
            
            # 드라마인 경우 total_episodes 설정 및 received_episodes 유지
            if new_rule["category"] == "드라마":
                total_eps = request.form.get("total_episodes", "").strip()
                if total_eps:
                    try:
                        new_rule["total_episodes"] = int(total_eps)
                    except ValueError:
                        pass
                # 기존 received_episodes 유지
                if "received_episodes" in old_rule:
                    new_rule["received_episodes"] = old_rule["received_episodes"]
                else:
                    new_rule.setdefault("received_episodes", [])
                epd_raw = request.form.get("episodes_per_day", "").strip()
                if epd_raw:
                    try:
                        epd = int(epd_raw)
                        if epd >= 1:
                            new_rule["episodes_per_day"] = epd
                    except ValueError:
                        pass
                # 유지되는 요일의 하루편수 카운터(day_progress)도 보존
                old_prog = old_rule.get("day_progress")
                if isinstance(old_prog, dict) and old_prog:
                    kept = {d: old_prog[d] for d in new_rule["days"] if d in old_prog}
                    if kept:
                        new_rule["day_progress"] = kept

            new_rule["id"] = idx  # DB id 보존
            cfg["rules"] = [new_rule if r.get("id") == idx else r for r in cfg["rules"]]
            save_cfg(cfg)
        
        # 현재 모드와 필터 조건 유지
        mode = request.form.get("mode", "").strip()
        day = request.form.get("day", "").strip()
        cat = request.form.get("cat", "").strip()
        sort = request.form.get("sort", "default").strip()
        sort_dir = request.form.get("dir", "asc").strip()
        hide_no_days = request.form.get("hide_no_days", "1").strip()

        # 편집 결과가 현재 필터에서 사라지는 경우에는 필터를 완화해 저장 직후에도 보이게 한다.
        if old_rule is not None and mode != "check":
            new_days = new_rule.get("days") or []
            if day and day != "전체" and day not in new_days:
                day = new_days[0] if len(new_days) == 1 else "전체"
            if cat and cat != "전체" and cat != new_rule.get("category"):
                cat = new_rule.get("category") or "전체"
        
        params = []
        # mode는 항상 추가 (빈 문자열이어도 전체 관리 모드로 감)
        params.append(f"mode={mode if mode else ''}")
        if day: params.append(f"day={day}")
        if cat: params.append(f"cat={cat}")
        if sort != "default": params.append(f"sort={sort}")
        if sort_dir != "asc": params.append(f"dir={sort_dir}")
        # 요일 없는 규칙 표시/숨김 필터도 항상 유지
        params.append(f"hide_no_days={hide_no_days}")

        redirect_url = "/" + ("?" + "&".join(params) if params else "")
        return redirect(redirect_url)

@app.route("/run_batch", methods=["POST"])
def run_batch():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    if not Path(RUN_BATCH_SSH_KEY).exists():
        return jsonify({"success": False, "message": "SSH 키가 설정되지 않았습니다 (secrets/run_batch_key 없음)."}), 500

    with _run_batch_state["lock"]:
        elapsed = time.time() - _run_batch_state["last_triggered"]
        if elapsed < 30:
            return jsonify({"success": False, "message": f"방금 요청했습니다. {int(30 - elapsed)}초 후 다시 시도하세요."}), 429
        _run_batch_state["last_triggered"] = time.time()

    try:
        subprocess.Popen(
            [
                "ssh",
                "-i", RUN_BATCH_SSH_KEY,
                "-p", RUN_BATCH_SSH_PORT,
                "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=5",
                f"{RUN_BATCH_SSH_USER}@{RUN_BATCH_SSH_HOST}",
                # 강제 명령(authorized_keys의 command=)이 실제로 실행되는 명령을
                # 대체하지만, sshd는 클라이언트가 요청한 원본 명령을
                # SSH_ORIGINAL_COMMAND 환경변수로 넘겨준다. run_all.sh가 이 값을
                # 읽어 로그에 "웹에서 수동 실행"임을 남길 수 있게 표시값을 보낸다.
                "web-trigger",
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        app.logger.exception("배치 실행 요청 중 오류")
        return jsonify({"success": False, "message": f"실행 요청 실패: {e}"}), 500

    return jsonify({"success": True, "message": "배치 실행을 요청했습니다. 잠시 후 로그에서 진행 상황을 확인하세요."})


@app.route("/wake_pc", methods=["POST"])
def wake_pc():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    with _wol_state["lock"]:
        elapsed = time.time() - _wol_state["last_triggered"]
        if elapsed < 10:
            return jsonify({"success": False, "message": f"방금 요청했습니다. {int(10 - elapsed)}초 후 다시 시도하세요."}), 429
        _wol_state["last_triggered"] = time.time()

    try:
        _send_wol_packet(WOL_MAC, WOL_BROADCAST)
    except Exception as e:
        app.logger.exception("WOL 패킷 전송 중 오류")
        return jsonify({"success": False, "message": f"전송 실패: {e}"}), 500

    return jsonify({"success": True, "message": "KCK-GAMING으로 WOL 패킷을 보냈습니다. 부팅까지 1~2분 정도 걸려요."})


@app.route("/pc_status", methods=["GET"])
def pc_status():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    online = _is_pc_online(GAMING_PC_HOST)
    return jsonify({"success": True, "online": online})


def _trigger_remote_encode() -> tuple[bool, str]:
    """KCK-GAMING에서 인코딩 스크립트를 원격으로 시작시킨다 (/run_encode 라우트와
    텔레그램 리스너가 공유). PC가 켜져있어야 하고, SSH 키가 있어야 한다.

    SSH 세션 안에서 Start-Process로 직접 띄우면, Windows에서 SSH 세션이
    끊기는 순간 자식 프로세스까지 같이 종료/행(hang)되는 문제가 있어서
    (실제로 재현됨: 프로세스는 떠있는데 CPU 0%, 로그도 전혀 안 남음) 대신
    미리 등록해둔 작업 스케줄러 작업을 트리거만 한다. 작업 스케줄러로 뜬
    프로세스는 SSH 세션과 완전히 독립적이라 이 문제가 없다.
    (PC에서 한 번만 Register-ScheduledTask로 "EncodeAll" 작업을 만들어둬야 함)"""
    if not Path(GAMING_PC_SSH_KEY).exists():
        return False, "SSH 키가 설정되지 않았습니다 (secrets/kck_gaming_key 없음)."

    if not _is_pc_online(GAMING_PC_HOST):
        return False, "PC가 꺼져있는 것 같아요."

    try:
        result = subprocess.run(
            [
                "ssh",
                "-i", GAMING_PC_SSH_KEY,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=8",
                f"{GAMING_PC_USER}@{GAMING_PC_HOST}",
                'schtasks /run /tn "EncodeAll"',
            ],
            capture_output=True, timeout=20,
        )
    except Exception as e:
        app.logger.exception("인코딩 원격 실행 요청 중 오류")
        return False, f"실행 요청 실패: {e}"

    if result.returncode != 0:
        detail = (_pc_text(result.stderr) or _pc_text(result.stdout)).strip()[:300]
        return False, f"SSH 실행 실패(종료코드 {result.returncode}): {detail}"

    return True, "KCK-GAMING에서 인코딩 스크립트를 시작했습니다."


@app.route("/run_encode", methods=["POST"])
def run_encode():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    with _run_encode_state["lock"]:
        elapsed = time.time() - _run_encode_state["last_triggered"]
        if elapsed < 30:
            return jsonify({"success": False, "message": f"방금 요청했습니다. {int(30 - elapsed)}초 후 다시 시도하세요."}), 429
        _run_encode_state["last_triggered"] = time.time()

    ok, message = _ensure_pc_on_and_trigger_encode()
    return jsonify({"success": ok, "message": message}), (200 if ok else 500)


@app.route("/cancel_shutdown", methods=["POST"])
def cancel_shutdown():
    """encode_all.ps1이 끝나면 5분 후 자동으로 PC를 끄는데(shutdownAfter),
    인코딩이 끝났어도 PC를 계속 쓰고 싶을 때 그 예약 종료를 취소한다.
    자주 쓰는 기능이 아니라 헤더 버튼이 아니라 인코딩 관리 페이지 본문에만
    둔다."""
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    if not Path(GAMING_PC_SSH_KEY).exists():
        return jsonify({"success": False, "message": "SSH 키가 설정되지 않았습니다 (secrets/kck_gaming_key 없음)."}), 500

    with _cancel_shutdown_state["lock"]:
        elapsed = time.time() - _cancel_shutdown_state["last_triggered"]
        if elapsed < 10:
            return jsonify({"success": False, "message": f"방금 요청했습니다. {int(10 - elapsed)}초 후 다시 시도하세요."}), 429
        _cancel_shutdown_state["last_triggered"] = time.time()

    if not _is_pc_online(GAMING_PC_HOST):
        return jsonify({"success": False, "message": "PC가 꺼져있는 것 같아요."}), 409

    try:
        result = subprocess.run(
            [
                "ssh",
                "-i", GAMING_PC_SSH_KEY,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=8",
                f"{GAMING_PC_USER}@{GAMING_PC_HOST}",
                "shutdown /a",
            ],
            capture_output=True, timeout=20,
        )
    except Exception as e:
        app.logger.exception("종료 취소 요청 중 오류")
        return jsonify({"success": False, "message": f"실행 요청 실패: {e}"}), 500

    # shutdown /a는 예약된 종료가 없으면 종료코드 1089로 실패한다 - 이미 취소됐거나
    # 원래 예약이 없었던 정상적인 상황이므로 에러로 취급하지 않는다.
    if result.returncode not in (0, 1089):
        detail = (_pc_text(result.stderr) or _pc_text(result.stdout)).strip()[:300]
        return jsonify({"success": False, "message": f"SSH 실행 실패(종료코드 {result.returncode}): {detail}"}), 500

    if result.returncode == 1089:
        return jsonify({"success": True, "message": "예약된 종료가 없었어요(이미 취소됐거나 원래 없었음)."})
    return jsonify({"success": True, "message": "KCK-GAMING의 예약 종료를 취소했습니다."})


@app.route("/shutdown_pc", methods=["POST"])
def shutdown_pc():
    """헤더 "PC 끄기" 버튼. 바로 끄지 않고 1분 유예를 줘서, 잘못 눌렀으면
    인코딩 관리 페이지의 'PC 종료 취소'(shutdown /a)로 되돌릴 수 있게 한다.
    인코딩 중이면 409로 한 번 돌려보내고, 화면에서 다시 확인받으면 force로 끈다."""
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    if not Path(GAMING_PC_SSH_KEY).exists():
        return jsonify({"success": False, "message": "SSH 키가 설정되지 않았습니다 (secrets/kck_gaming_key 없음)."}), 500

    if not _is_pc_online(GAMING_PC_HOST):
        return jsonify({"success": False, "message": "PC가 이미 꺼져있는 것 같아요."}), 409

    force = bool((request.get_json(silent=True) or {}).get("force"))
    if not force and _is_encoding_in_progress():
        return jsonify({"success": False, "encoding": True, "message": "지금 인코딩이 진행 중이에요. 끄면 진행 중인 파일은 처음부터 다시 해야 해요."}), 409

    # 인코딩 확인 뒤에 둬야 "그래도 끌까요?" 재요청이 쿨다운에 막히지 않는다
    with _shutdown_pc_state["lock"]:
        elapsed = time.time() - _shutdown_pc_state["last_triggered"]
        if elapsed < 10:
            return jsonify({"success": False, "message": f"방금 요청했습니다. {int(10 - elapsed)}초 후 다시 시도하세요."}), 429
        _shutdown_pc_state["last_triggered"] = time.time()

    try:
        result = subprocess.run(
            [
                "ssh",
                "-i", GAMING_PC_SSH_KEY,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=8",
                f"{GAMING_PC_USER}@{GAMING_PC_HOST}",
                'shutdown /s /t 60 /c "Media Router에서 PC 끄기 요청"',
            ],
            capture_output=True, timeout=20,
        )
    except Exception as e:
        app.logger.exception("PC 끄기 요청 중 오류")
        return jsonify({"success": False, "message": f"실행 요청 실패: {e}"}), 500

    # 1190: 이미 종료가 예약돼 있음(인코딩 후 자동 종료 등) - 어차피 꺼지므로 실패로 보지 않는다
    if result.returncode == 1190:
        return jsonify({"success": True, "message": "이미 종료가 예약돼 있어요. 곧 꺼집니다."})
    if result.returncode != 0:
        detail = (_pc_text(result.stderr) or _pc_text(result.stdout)).strip()[:300]
        return jsonify({"success": False, "message": f"SSH 실행 실패(종료코드 {result.returncode}): {detail}"}), 500

    return jsonify({"success": True, "message": "KCK-GAMING을 1분 후 종료합니다. 취소하려면 인코딩 관리의 'PC 종료 취소'를 누르세요."})


def _list_run_all_log_dates() -> list[str]:
    """logs/ 디렉토리에서 run_all.YYYY-MM-DD.log 파일들의 날짜 목록(최신순)을 반환."""
    if not RUN_ALL_LOGS_DIR.is_dir():
        return []
    dates = []
    for p in RUN_ALL_LOGS_DIR.iterdir():
        m = RUN_ALL_LOG_NAME_RE.match(p.name)
        if m:
            dates.append(m.group(1))
    return sorted(dates, reverse=True)


def _run_all_log_path(date_str: str) -> Path | None:
    if not LOG_DATE_RE.match(date_str or ""):
        return None
    return RUN_ALL_LOGS_DIR / f"run_all.{date_str}.log"


@app.route("/logs", methods=["GET"])
def logs_page():
    auth = authed(request)
    if auth is not True:
        return auth

    dates = _list_run_all_log_dates()
    today_str = datetime.now(APP_TZ).strftime("%Y-%m-%d")
    requested_date = request.args.get("date", "").strip()

    if requested_date and LOG_DATE_RE.match(requested_date):
        selected_date = requested_date
    elif today_str in dates or not dates:
        selected_date = today_str
    else:
        selected_date = dates[0]  # 오늘자 로그가 아직 없으면 가장 최근 로그를 보여줌

    content = ""
    file_size = 0
    log_exists = False
    dir_mounted = RUN_ALL_LOGS_DIR.is_dir()
    path = _run_all_log_path(selected_date)
    if path and path.exists():
        log_exists = True
        file_size = path.stat().st_size
        try:
            with path.open("rb") as f:
                if file_size > LOG_INITIAL_MAX_BYTES:
                    f.seek(file_size - LOG_INITIAL_MAX_BYTES)
                raw = f.read()
            content = raw.decode("utf-8", errors="replace")
        except Exception as e:
            content = f"[로그 파일을 읽는 중 오류: {e}]"

    return render_template(
        "logs.html",
        authed=True,
        dates=dates,
        selected_date=selected_date,
        today_str=today_str,
        content=content,
        initial_offset=file_size,
        log_exists=log_exists,
        dir_mounted=dir_mounted,
        logs_dir=str(RUN_ALL_LOGS_DIR),
    )


@app.route("/logs/tail", methods=["GET"])
def logs_tail():
    auth = authed(request)
    if auth is not True:
        return jsonify({"error": "로그인이 필요합니다."}), 401

    date_str = request.args.get("date", "").strip()
    path = _run_all_log_path(date_str)
    if path is None:
        return jsonify({"error": "날짜 형식이 올바르지 않습니다."}), 400

    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        offset = 0

    if not path.exists():
        return jsonify({"exists": False, "content": "", "offset": 0})

    size = path.stat().st_size
    if offset < 0 or offset > size:
        offset = 0  # 파일이 교체되었거나 최초 조회

    new_text = ""
    if offset < size:
        try:
            with path.open("rb") as f:
                f.seek(offset)
                raw = f.read()
            new_text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return jsonify({"error": f"로그 파일을 읽는 중 오류: {e}"}), 500

    return jsonify({"exists": True, "content": new_text, "offset": size})


@app.route("/logs/download", methods=["GET"])
def logs_download():
    auth = authed(request)
    if auth is not True:
        return auth

    date_str = request.args.get("date", "").strip()
    path = _run_all_log_path(date_str)
    if path is None or not path.exists():
        return abort(404)

    return send_file(path, mimetype="text/plain", as_attachment=True, download_name=path.name)


QBIT_URL = os.getenv("QBIT_URL", "http://127.0.0.1:8080").rstrip("/")
QBIT_USERNAME = os.getenv("QBIT_USERNAME", "")
QBIT_PASSWORD = os.getenv("QBIT_PASSWORD", "")
# qBittorrent는 로그인 실패가 반복되면 그 IP를 차단한다. 이 컨테이너의 요청은
# DSM 역방향 프록시(bit.kckworld.com)와 같은 docker 게이트웨이 IP로 들어가므로,
# 비밀번호가 틀린 채 폴링을 계속하면 bit.kckworld.com까지 막힌다 → 실패 후 5분 쉼.
QBIT_LOGIN_BACKOFF_SEC = 300
_qbit_state = {"lock": threading.Lock(), "session": None, "login_failed_at": 0.0}


def _qbit_get(path: str, params: dict | None = None):
    """qBittorrent WebUI API GET. 세션 쿠키를 재사용하고 만료(403)되면 한 번 재로그인."""
    if not QBIT_USERNAME or not QBIT_PASSWORD:
        raise RuntimeError("qBittorrent 계정이 설정되지 않았습니다 (.env의 QBIT_USERNAME/QBIT_PASSWORD).")
    with _qbit_state["lock"]:
        for _ in range(2):
            sess = _qbit_state["session"]
            if sess is None:
                wait = QBIT_LOGIN_BACKOFF_SEC - (time.time() - _qbit_state["login_failed_at"])
                if wait > 0:
                    raise RuntimeError(f"qBittorrent 로그인 실패 후 대기 중입니다 ({int(wait)}초 후 재시도).")
                sess = requests.Session()
                r = sess.post(
                    f"{QBIT_URL}/api/v2/auth/login",
                    data={"username": QBIT_USERNAME, "password": QBIT_PASSWORD},
                    headers={"Referer": QBIT_URL},
                    timeout=8,
                )
                # qBittorrent 5.2+는 성공 시 빈 본문(204), 예전 버전은 "Ok.", 실패는 "Fails."/401
                if r.status_code not in (200, 204) or r.text.strip() == "Fails." or not sess.cookies:
                    _qbit_state["login_failed_at"] = time.time()
                    raise RuntimeError("qBittorrent 로그인 실패 - 계정 정보를 확인하세요.")
                _qbit_state["session"] = sess
            r = sess.get(f"{QBIT_URL}{path}", params=params, timeout=8)
            if r.status_code == 403:
                _qbit_state["session"] = None
                continue
            r.raise_for_status()
            return r.json()
    raise RuntimeError("qBittorrent 인증이 반복해서 거부됐습니다.")


@app.route("/downloads", methods=["GET"])
def downloads_page():
    auth = authed(request)
    if auth is not True:
        return auth
    return render_template("downloads.html", authed=True, qbit_public_url="https://bit.kckworld.com/")


@app.route("/downloads/data", methods=["GET"])
def downloads_data():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    try:
        info = _qbit_get("/api/v2/transfer/info")
        torrents = _qbit_get("/api/v2/torrents/info", {"sort": "added_on", "reverse": "true"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 502
    keys = ("hash", "name", "size", "progress", "dlspeed", "upspeed", "eta", "state",
            "num_seeds", "num_leechs", "added_on", "completion_on", "category", "ratio", "downloaded")
    return jsonify({
        "success": True,
        "transfer": {k: info.get(k) for k in ("dl_info_speed", "up_info_speed", "connection_status", "dht_nodes")},
        "torrents": [{k: t.get(k) for k in keys} for t in torrents],
    })


@app.route("/encode_log", methods=["GET"])
def encode_log_page():
    """PC(KCK-GAMING)에서 encode_all.ps1이 Synology Drive로 동기화해주는
    _encode_log.txt를 그대로 보여준다. run_all 로그와 달리 날짜별 파일이
    아니라 계속 이어지는 단일 파일이라 날짜 선택 없이 전체를 tail한다."""
    auth = authed(request)
    if auth is not True:
        return auth

    dir_mounted = ENCODE_LOG_PATH.parent.is_dir()
    content = ""
    file_size = 0
    log_exists = ENCODE_LOG_PATH.exists()
    if log_exists:
        file_size = ENCODE_LOG_PATH.stat().st_size
        try:
            with ENCODE_LOG_PATH.open("rb") as f:
                if file_size > LOG_INITIAL_MAX_BYTES:
                    f.seek(file_size - LOG_INITIAL_MAX_BYTES)
                raw = f.read()
            content = raw.decode("utf-8", errors="replace")
        except Exception as e:
            content = f"[로그 파일을 읽는 중 오류: {e}]"

    return render_template(
        "encode_log.html",
        authed=True,
        content=content,
        initial_offset=file_size,
        log_exists=log_exists,
        dir_mounted=dir_mounted,
        log_path=str(ENCODE_LOG_PATH),
    )


@app.route("/encode_log/tail", methods=["GET"])
def encode_log_tail():
    auth = authed(request)
    if auth is not True:
        return jsonify({"error": "로그인이 필요합니다."}), 401

    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        offset = 0

    if not ENCODE_LOG_PATH.exists():
        return jsonify({"exists": False, "content": "", "offset": 0})

    size = ENCODE_LOG_PATH.stat().st_size
    if offset < 0 or offset > size:
        offset = 0  # 파일이 교체되었거나 최초 조회

    new_text = ""
    if offset < size:
        try:
            with ENCODE_LOG_PATH.open("rb") as f:
                f.seek(offset)
                raw = f.read()
            new_text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return jsonify({"error": f"로그 파일을 읽는 중 오류: {e}"}), 500

    return jsonify({"exists": True, "content": new_text, "offset": size})


@app.route("/encode_log/download", methods=["GET"])
def encode_log_download():
    auth = authed(request)
    if auth is not True:
        return auth

    if not ENCODE_LOG_PATH.exists():
        return abort(404)

    return send_file(ENCODE_LOG_PATH, mimetype="text/plain", as_attachment=True, download_name=ENCODE_LOG_PATH.name)


@app.route("/search_tmdb", methods=["POST"])
def search_tmdb():
    auth = authed(request)
    if auth is not True:
        return auth
    
    query = request.form.get("q", "").strip()
    result = tmdb_search_info(query)
    return jsonify(result)

# ─── 사용자 관리 (Jellyfin/Plex 계정 매핑, media-stats postgres 연동) ───────
@app.route("/users", methods=["GET"])
def users_page():
    auth = authed(request)
    if auth is not True:
        return auth
    return render_template("users.html", authed=True)


@app.route("/api/users/summary", methods=["GET"])
def api_users_summary():
    auth = authed(request)
    if auth is not True:
        return auth
    with _stats_db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM persons), (SELECT count(*) FROM service_accounts), "
            "(SELECT count(*) FROM play_history), (SELECT count(*) FROM play_history WHERE account_id IS NULL)"
        )
        persons, accounts, plays, unmatched = cur.fetchone()
    return jsonify({"persons": persons, "accounts": accounts, "plays": plays, "unmatched": unmatched})


@app.route("/api/users/persons", methods=["GET"])
def api_users_persons_list():
    auth = authed(request)
    if auth is not True:
        return auth
    with _stats_db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.name, p.memo, p.created_at, p.updated_at,
              COALESCE(json_agg(json_build_object('id', sa.id, 'service', sa.service, 'username', sa.username, 'user_id', sa.user_id))
                       FILTER (WHERE sa.id IS NOT NULL), '[]') AS accounts
            FROM persons p
            LEFT JOIN service_accounts sa ON sa.person_id = p.id
            GROUP BY p.id
            ORDER BY p.name
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return jsonify(rows)


@app.route("/api/users/persons", methods=["POST"])
def api_users_persons_create():
    auth = authed(request)
    if auth is not True:
        return auth
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    memo = (data.get("memo") or "").strip() or None
    if not name:
        return jsonify({"error": "이름을 입력해주세요."}), 400
    with _stats_db() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO persons (name, memo) VALUES (%s, %s) RETURNING id, name, memo, created_at, updated_at",
            (name, memo),
        )
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
    return jsonify(dict(zip(cols, row)))


@app.route("/api/users/persons/<int:person_id>", methods=["PUT"])
def api_users_persons_update(person_id):
    auth = authed(request)
    if auth is not True:
        return auth
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    memo = (data.get("memo") or "").strip() or None
    if not name:
        return jsonify({"error": "이름을 입력해주세요."}), 400
    with _stats_db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE persons SET name=%s, memo=%s, updated_at=NOW() WHERE id=%s "
            "RETURNING id, name, memo, created_at, updated_at",
            (name, memo, person_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "사용자를 찾을 수 없습니다."}), 404
        cols = [d[0] for d in cur.description]
    return jsonify(dict(zip(cols, row)))


@app.route("/api/users/persons/<int:person_id>", methods=["DELETE"])
def api_users_persons_delete(person_id):
    auth = authed(request)
    if auth is not True:
        return auth
    with _stats_db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM persons WHERE id=%s", (person_id,))
    return jsonify({"ok": True})


@app.route("/api/users/accounts", methods=["POST"])
def api_users_accounts_create():
    auth = authed(request)
    if auth is not True:
        return auth
    data = request.get_json(force=True, silent=True) or {}
    person_id = data.get("person_id")
    service = (data.get("service") or "").strip()
    username = (data.get("username") or "").strip()
    user_id = (data.get("user_id") or "").strip()
    if not (person_id and service and username and user_id):
        return jsonify({"error": "모든 항목을 입력해주세요."}), 400
    try:
        with _stats_db() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO service_accounts (person_id, service, user_id, username) VALUES (%s,%s,%s,%s) "
                "RETURNING id, person_id, service, user_id, username, created_at",
                (person_id, service, user_id, username),
            )
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
        return jsonify(dict(zip(cols, row)))
    except psycopg2.IntegrityError as e:
        if e.pgcode == "23505":
            return jsonify({"error": "이미 등록된 계정입니다."}), 409
        app.logger.exception("계정 추가 중 오류")
        return jsonify({"error": str(e)}), 500


@app.route("/api/users/accounts/<int:account_id>", methods=["PUT"])
def api_users_accounts_update(account_id):
    auth = authed(request)
    if auth is not True:
        return auth
    data = request.get_json(force=True, silent=True) or {}
    service = (data.get("service") or "").strip()
    username = (data.get("username") or "").strip()
    user_id = (data.get("user_id") or "").strip()
    if not (service and username and user_id):
        return jsonify({"error": "모든 항목을 입력해주세요."}), 400
    try:
        with _stats_db() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE service_accounts SET service=%s, user_id=%s, username=%s WHERE id=%s "
                "RETURNING id, person_id, service, user_id, username, created_at",
                (service, user_id, username, account_id),
            )
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "계정을 찾을 수 없습니다."}), 404
            cols = [d[0] for d in cur.description]
        return jsonify(dict(zip(cols, row)))
    except psycopg2.IntegrityError as e:
        if e.pgcode == "23505":
            return jsonify({"error": "이미 등록된 계정입니다."}), 409
        app.logger.exception("계정 수정 중 오류")
        return jsonify({"error": str(e)}), 500


@app.route("/api/users/accounts/<int:account_id>", methods=["DELETE"])
def api_users_accounts_delete(account_id):
    auth = authed(request)
    if auth is not True:
        return auth
    with _stats_db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM service_accounts WHERE id=%s", (account_id,))
    return jsonify({"ok": True})


if __name__=="__main__":
    app.run(host="0.0.0.0", port=5080)
