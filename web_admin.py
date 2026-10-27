# -*- coding: utf-8 -*-
from pathlib import Path
import os, base64, hashlib, hmac, json, re, secrets, subprocess, time, threading, socket
from flask import Flask, request, redirect, render_template, abort, make_response, jsonify, send_file
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import requests
import psycopg2
from contextlib import contextmanager
from urllib.parse import urlparse
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
# 보기 전용 계정 비밀번호 (2026-10-16). 이걸로 로그인하면 모든 화면을 볼 수만 있고(개인정보 화면 제외)
# 저장·실행 같은 변경 요청은 _readonly_guard가 전부 403으로 막는다. 비워두면 보기 전용 로그인 없음.
VIEWER_PASSWORD = os.getenv("MEDIA_VIEWER_PASSWORD", "")

app = Flask(__name__, template_folder=str(BASE / "templates"))

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
CATEGORIES = ["드라마", "예능", "애니메이션", "다큐"]
DAY_ORDER = {d: i for i, d in enumerate(WEEKDAYS)}
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Seoul"))

RENAME_RULES_PATH = Path(os.getenv("RENAME_RULES_PATH", "/volume1/web/video_auto/rename_rules.conf"))
BT4G_KEYWORDS_PATH = Path(os.getenv("BT4G_KEYWORDS_PATH", "/volume1/web/Python/bt4g/bt4g_keywords.txt"))
# 드라마 자동 다운로드 키워드 (2026-10-26, bt4g/tfreeca.py가 티프리카 드라마 게시판에서 찾음)
DRAMA_KEYWORDS_PATH = Path(os.getenv("DRAMA_KEYWORDS_PATH", "/volume1/web/Python/bt4g/drama_keywords.txt"))

# 전체 배치(run_all.sh) 수동 실행 - 호스트로 SSH 접속해 강제 명령(run_all.sh)만 실행
RUN_BATCH_SSH_KEY = os.getenv("RUN_BATCH_SSH_KEY", "/app/secrets/run_batch_key")
RUN_BATCH_SSH_HOST = os.getenv("RUN_BATCH_SSH_HOST", "127.0.0.1")
RUN_BATCH_SSH_PORT = os.getenv("RUN_BATCH_SSH_PORT", "202")
RUN_BATCH_SSH_USER = os.getenv("RUN_BATCH_SSH_USER", "kck9010")
_run_batch_state = {"lock": threading.Lock(), "last_triggered": 0}

# NAS 백그라운드 claude 세션 재로그인 (2026-10-24) - 같은 호스트 SSH지만 키가 따로라
# 강제 명령(claude_relogin.sh)의 status/start/code 만 쓸 수 있다
CLAUDE_LOGIN_SSH_KEY = os.getenv("CLAUDE_LOGIN_SSH_KEY", "/app/secrets/claude_login_key")

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
# 받는 중인 파일 확장자: 크롬/엣지 .crdownload, JDownloader·파이어폭스 .part
_PARTIAL_DOWNLOAD_EXTS = (".crdownload", ".part", ".tmp", ".!qb")
# 하위 폴더와 PC 쪽에서는 .tmp를 빼고 본다 (문서 폴더의 오피스 임시파일 같은 게 남아 있으면
# 예약 종료가 영영 안 될 수 있어서). .tmp는 예전처럼 Drive 맨 위에서만 센다.
_PARTIAL_DEEP_EXTS = (".crdownload", ".part", ".!qb")
_schedule_shutdown_state = {"lock": threading.Lock(), "active": False, "stop_event": None, "started_at": 0}
# Drive 안에서 다운로드가 아닌 폴더 (인코딩 작업 폴더·보관·시놀로지 내부)
_PARTIAL_SKIP_DIRS = {"인코딩", "인코딩 완료", "보관", "@eaDir", "#recycle", ".SynologyWorkingDirectory"}
# PC(KCK-GAMING)에서 직접 확인할 다운로드 폴더 (Synology Drive로 NAS와 동기화되는 곳)
PC_DOWNLOAD_DIR = os.getenv("PC_DOWNLOAD_DIR", r"D:\SynologyDrive")


def _nas_partial_files(max_depth: int = 3) -> list[str]:
    """NAS의 Drive 폴더에서 받는 중인 파일. JDownloader는 기본으로 다운로드마다 하위 폴더를
    만들기 때문에(2026-10-19) 맨 위만이 아니라 하위 폴더도 본다."""
    found = []

    def walk(d: Path, depth: int):
        try:
            for p in d.iterdir():
                if p.is_dir():
                    if depth < max_depth and p.name not in _PARTIAL_SKIP_DIRS:
                        walk(p, depth + 1)
                elif p.name.lower().endswith(_PARTIAL_DOWNLOAD_EXTS if depth == 1 else _PARTIAL_DEEP_EXTS):
                    found.append(str(p.relative_to(DRIVE_ROOT_PATH)))
        except Exception:
            app.logger.exception("Drive 다운로드 중 파일 확인 실패: %s", d)

    walk(DRIVE_ROOT_PATH, 1)
    return found


_pc_partial_cache = {"t": 0.0, "v": None}


def _pc_partial_files() -> list[str] | None:
    """PC의 다운로드 폴더를 SSH로 직접 확인. Drive 동기화가 늦거나 큰 임시 파일을 늦게 올려도
    정확하다. PC가 꺼져 있거나 확인이 안 되면 None(=모름). 화면 폴링과 예약 종료 감시가
    같이 부르므로 10초 동안은 지난 결과를 쓴다."""
    if time.time() - _pc_partial_cache["t"] < 10:
        return _pc_partial_cache["v"]
    v = _pc_partial_files_uncached()
    _pc_partial_cache.update(t=time.time(), v=v)
    return v


def _pc_partial_files_uncached() -> list[str] | None:
    if not (GAMING_PC_USER and Path(GAMING_PC_SSH_KEY).exists()) or not _is_pc_online(GAMING_PC_HOST):
        return None
    import base64
    exts = "|".join(e.lstrip(".").replace("!", "\\!") for e in _PARTIAL_DEEP_EXTS)
    ps = ("$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          f"Get-ChildItem -LiteralPath '{PC_DOWNLOAD_DIR}' -Recurse -Depth 2 -File -Force -ErrorAction SilentlyContinue "
          f"| Where-Object {{ $_.Name -match '\\.({exts})$' }} | Select-Object -First 50 | ForEach-Object {{ $_.FullName }}")
    # 한글·$_ 가 셸에서 깨지지 않도록 UTF-16LE base64로 보낸다
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    try:
        r = _pc_ssh(f"powershell -NoProfile -NonInteractive -EncodedCommand {enc}")
    except Exception:
        app.logger.exception("PC 다운로드 중 파일 확인 실패")
        return None
    if r.returncode != 0:
        return None
    files = []
    for line in _pc_text(r.stdout).splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.replace("/", "\\").split("\\")
        if any(seg in _PARTIAL_SKIP_DIRS for seg in parts[:-1]):
            continue
        files.append(line)
    return files


def _partial_downloads() -> list[str]:
    """받는 중인 파일 목록 (PC + NAS). 하나라도 있으면 '다운로드 중'."""
    pc = _pc_partial_files() or []
    nas = _nas_partial_files()
    pc_names = {p.replace("/", "\\").rsplit("\\", 1)[-1] for p in pc}
    # NAS에 동기화된 같은 파일은 한 번만 센다
    return pc + [n for n in nas if n.replace("/", "\\").rsplit("\\", 1)[-1] not in pc_names]


def _has_partial_downloads() -> bool:
    return bool(_partial_downloads())


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


def _tg_cfg() -> dict:
    from db import load_cfg as _load_cfg
    return _load_cfg().get("telegram") or {}


def _tg_call(method: str, payload: dict, token: str | None = None) -> dict | None:
    """텔레그램 Bot API 호출. 예외 메시지에는 토큰이 든 URL이 섞이므로 종류만 로그에 남긴다."""
    token = token or _tg_cfg().get("bot_token")
    if not token:
        return None
    try:
        return requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=10).json()
    except Exception as e:
        app.logger.warning("텔레그램 %s 호출 실패: %s", method, type(e).__name__)
        return None


def _tg_notify(text: str, reply_markup: dict | None = None, token: str | None = None) -> bool:
    tg = _tg_cfg()
    token = token or tg.get("bot_token")
    if not tg.get("enabled") or not token or not tg.get("chat_id"):
        return False
    payload = {"chat_id": tg["chat_id"], "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return bool((_tg_call("sendMessage", payload, token) or {}).get("ok"))


# 텔레그램 대화창 하단 고정 메뉴. 버튼을 누르면 그 글자가 메시지로 오므로
# 아래 리스너가 글자로 구분해서 처리한다. 인바운드 웹훅 없이 getUpdates
# 롱폴링만 쓰므로 포트포워딩/공인 도메인/SSL 없이도 동작한다.
TG_BTN_ENCODE = "🎬 인코딩 시작"
TG_BTN_BATCH = "▶ 배치 실행"
TG_BTN_WAKE = "⚡ PC 켜기"
TG_BTN_STATUS = "❔ PC 상태"
TG_BTN_OFF = "🔌 PC 끄기"
TG_BTN_MOVIES = "🎞 영화 베스트"
# 아이콘을 ⏻에서 바꾸기 전(10-15)에 띄운 메뉴가 대화창에 남아 있을 수 있어 옛 글자도 받는다
_TG_BTN_OFF_LEGACY = "⏻ PC 끄기"
TG_MENU = {
    "keyboard": [
        [{"text": TG_BTN_ENCODE}, {"text": TG_BTN_BATCH}],
        [{"text": TG_BTN_WAKE}, {"text": TG_BTN_STATUS}, {"text": TG_BTN_OFF}],
        [{"text": TG_BTN_MOVIES}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}
# 메뉴 도입 전처럼 "인코딩"이 들어간 아무 메시지로도 인코딩을 시작할 수 있게 유지
_TG_ENCODE_TRIGGER_WORDS = ("인코딩",)
# 확인 버튼(인라인)은 오래된 걸 실수로 누르지 않게 5분 지나면 무시
TG_CONFIRM_TTL_SEC = 300
# 영화 베스트 목록 버튼은 천천히 골라도 되게 하루 동안 유효
TG_MOVIE_TTL_SEC = 24 * 3600
# 단톡방 요청 알림의 [완료]/[보류] 버튼
TG_REQUEST_TTL_SEC = 30 * 24 * 3600
# 메뉴 버튼을 바꾸면 올린다 → 이미 메뉴를 받은 대화창에도 새 메뉴를 한 번 띄움
TG_MENU_VERSION = 2
# 메뉴 안내는 봇마다 한 번 - 토큰을 바꾸면(새 봇) 그 봇에도 한 번 띄운다
def _tg_bot_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _tg_menu_marker(token: str) -> Path:
    return DATA_DIR / f"telegram_menu_{_tg_bot_key(token)}_v{TG_MENU_VERSION}.sent"
# 리스너 스레드는 모듈 중간에서 시작되므로, 아래쪽 라우트 헬퍼(_request_*)까지
# 정의가 끝난 뒤(파일 맨 끝에서 set)에야 업데이트를 처리한다
_tg_ready = threading.Event()
# 확인 버튼 연타 시 같은 메시지의 버튼이 두 번 실행되지 않게
_tg_handled = {"lock": threading.Lock(), "messages": set(), "menu_tried": {}, "conflict_logged": set(), "unknown_chats": set()}
TG_MENU_RETRY_SEC = 300


def _tg_confirm(*buttons: tuple[str, str]) -> dict:
    now = int(time.time())
    return {"inline_keyboard": [[{"text": label, "callback_data": f"{action}:{now}"} for label, action in buttons]]}


# ── 인코딩 전 Drive 동기화 대기 (2026-10-19) ──
# PC를 켜자마자 인코딩을 시작하면 Synology Drive가 아직 NAS의 Drive/인코딩 파일을 PC로
# 내려받기 전이라 encode_all.ps1이 "대상 0개"로 보고 PC를 바로 꺼버렸다. 그래서 NAS에 있는
# 인코딩 대상이 PC의 같은 폴더에 같은 크기로 다 나타날 때까지 기다린 뒤 시작한다.
ENCODE_SYNC_TIMEOUT_SEC = 30 * 60
ENCODE_SYNC_POLL_SEC = 15
# encode_all.ps1의 $exts / $inDir 와 같아야 한다
_ENCODE_EXTS = {".mp4", ".mkv", ".ts", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".webm"}
PC_ENCODE_DIR = os.getenv("PC_ENCODE_DIR", r"D:\SynologyDrive\인코딩")


def _is_encode_target(name: str) -> bool:
    # [완료]·[손상]으로 시작하는 건 스크립트가 건너뛰는 파일
    return Path(name).suffix.lower() in _ENCODE_EXTS and not name.startswith(("[완료]", "[손상]"))


def _nas_encode_targets() -> dict[str, int]:
    """NAS Drive/인코딩 맨 위의 인코딩 대상 {파일명: 크기}."""
    out = {}
    try:
        for p in ENCODING_HOLD_PATH.iterdir():
            if p.is_file() and _is_encode_target(p.name):
                out[p.name] = p.stat().st_size
    except OSError:
        app.logger.exception("Drive/인코딩 목록 확인 실패")
    return out


def _parse_pc_listing(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        name, sep, size = line.rstrip("\r").rpartition("\t")
        if sep and size.strip().isdigit():
            out[name] = int(size.strip())
    return out


def _pc_encode_files() -> dict[str, int] | None:
    """PC의 인코딩 폴더 {파일명: 크기}. PC가 꺼져 있거나 확인 실패면 None."""
    if not (GAMING_PC_USER and Path(GAMING_PC_SSH_KEY).exists()) or not _is_pc_online(GAMING_PC_HOST):
        return None
    import base64
    ps = ("$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          f"Get-ChildItem -LiteralPath '{PC_ENCODE_DIR}' -File -Force -ErrorAction SilentlyContinue "
          "| ForEach-Object { $_.Name + \"`t\" + $_.Length }")
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    try:
        r = _pc_ssh(f"powershell -NoProfile -NonInteractive -EncodedCommand {enc}")
    except Exception:
        app.logger.exception("PC 인코딩 폴더 확인 실패")
        return None
    if r.returncode != 0:
        return None
    return _parse_pc_listing(_pc_text(r.stdout))


def _missing_on_pc(nas: dict[str, int], pc: dict[str, int]) -> list[str]:
    """NAS에는 있는데 PC에는 아직 없거나 크기가 다른(받는 중인) 파일."""
    return sorted(name for name, size in nas.items() if pc.get(name) != size)


def _wait_encode_files_synced(progress=None, timeout: int | None = None) -> tuple[bool, str]:
    """NAS의 인코딩 대상이 PC에 다 내려올 때까지 기다린다. (성공 여부, 안내 메시지)
    NAS에 대상이 없으면 바로 통과 - 그때는 스크립트가 "대상 0개 → 즉시 종료"로
    동작하는데, 그게 원격 종료 스위치로 쓰는 원래 의도다."""
    def _note(msg: str) -> None:
        if progress:
            progress(msg)

    timeout = timeout or ENCODE_SYNC_TIMEOUT_SEC
    deadline = time.time() + timeout
    noted = False
    missing: list[str] = []
    while True:
        nas = _nas_encode_targets()
        if not nas:
            return True, ""
        pc = _pc_encode_files()
        if pc is not None:
            missing = _missing_on_pc(nas, pc)
            if not missing:
                return True, f"PC로 동기화 완료 ({len(nas)}개)"
        if time.time() >= deadline:
            names = "\n- ".join(missing[:5]) if missing else "(PC 폴더를 확인하지 못함)"
            return False, (f"{timeout // 60}분 기다렸는데 PC로 동기화가 안 끝났어요. 인코딩은 시작하지 않았고 PC는 켜 둔 채예요.\n"
                           f"Synology Drive 상태를 확인해 주세요.\n- {names}")
        if not noted:
            noted = True
            done = len(nas) - len(missing) if pc is not None else 0
            _note(f"Synology Drive가 인코딩할 파일을 PC로 옮기는 중이에요 ({done}/{len(nas)}). 다 올라오면 시작할게요 (최대 {timeout // 60}분).")
        time.sleep(ENCODE_SYNC_POLL_SEC)


def _ensure_pc_on_and_trigger_encode(progress=None, sync_timeout: int | None = None) -> tuple[bool, str]:
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
        _note("PC 켜졌어요. 인코딩할 파일이 PC로 다 올라왔는지 확인할게요.")
        time.sleep(10)  # SSH 서비스가 완전히 올라올 시간 여유

    synced, sync_msg = _wait_encode_files_synced(progress, sync_timeout)
    if not synced:
        return False, sync_msg
    ok, message = _trigger_remote_encode()
    return ok, (f"{sync_msg}. {message}" if ok and sync_msg else message)


def _handle_telegram_encode_request() -> None:
    with _run_encode_state["lock"]:
        elapsed = time.time() - _run_encode_state["last_triggered"]
        if elapsed < 30:
            _tg_notify(f"방금 요청했어요. {int(30 - elapsed)}초 후 다시 보내주세요.")
            return
        _run_encode_state["last_triggered"] = time.time()

    _run_encode_with_notify()


def _run_encode_with_notify() -> None:
    ok, message = _ensure_pc_on_and_trigger_encode(progress=_tg_notify)
    suffix = "\n끝나면 알아서 PC가 꺼질 거예요." if ok else ""
    _tg_notify(("✅ " if ok else "❌ ") + message + suffix)


def _tg_result(ok: bool, message: str, reply_markup: dict | None = None) -> None:
    _tg_notify(("✅ " if ok else "❌ ") + message, reply_markup)


def _tg_pc_status_text() -> str:
    online = _is_pc_online(GAMING_PC_HOST)
    lines = ["🟢 KCK-GAMING 켜져 있어요" if online else "🔴 KCK-GAMING 꺼져 있어요"]
    if _is_encoding_in_progress():
        lines.append("🎬 인코딩 진행 중")
    if _has_partial_downloads():
        lines.append("⬇ Drive로 다운로드 중인 파일 있음")
    with _schedule_shutdown_state["lock"]:
        if _schedule_shutdown_state["active"]:
            lines.append("⏳ PC예약종료 대기 중 (다운로드·인코딩 끝나면 종료)")
    return "\n".join(lines)


def _tg_shutdown(force: bool) -> None:
    ok, message, _code, encoding = _request_pc_shutdown(force)
    if encoding:
        _tg_notify("⚠ " + message + "\n그래도 끌까요?", _tg_confirm(("🔌 그래도 끄기", "off_force"), ("취소", "dismiss")))
    elif ok:
        _tg_result(True, message, _tg_confirm(("⏸ PC 종료 취소", "cancel_off")))
    else:
        _tg_result(False, message)


def _handle_telegram_text(text: str) -> None:
    # 유튜브 앱에서 공유한 링크 → 영상/음악 중 고르게 한다 (아래 youtube_* 콜백)
    vid = ytdl.extract_video_id(text)
    if vid:
        _tg_notify("유튜브 링크를 받았어요. 어떻게 받을까요?\n영상은 Drive에, 음악은 Drive/음악에 넣어요.",
                   _tg_confirm(("🎬 영상 (1080p)", f"ytv_{vid}"), ("🎵 음악 (mp3)", f"yta_{vid}"), ("취소", "dismiss")))
        return
    if text == TG_BTN_BATCH:
        _tg_notify("배치(run_all.sh)를 지금 실행할까요?\n파일 이동·이름변경이 바로 진행돼요.",
                   _tg_confirm(("▶ 실행", "batch"), ("취소", "dismiss")))
    elif text == TG_BTN_WAKE:
        ok, message, _code = _request_wol()
        _tg_result(ok, message)
    elif text == TG_BTN_STATUS:
        _tg_notify(_tg_pc_status_text())
    elif text == TG_BTN_MOVIES:
        _tg_movie_best()
    elif text in (TG_BTN_OFF, _TG_BTN_OFF_LEGACY):
        _tg_shutdown(force=False)
    elif text == TG_BTN_ENCODE or any(w in text for w in _TG_ENCODE_TRIGGER_WORDS):
        _handle_telegram_encode_request()
    else:
        # /start, /menu, 그 밖의 아무 메시지 → 메뉴를 다시 띄워준다 (키보드를 닫았을 때 대비)
        _tg_notify("아래 버튼으로 조작할 수 있어요.", TG_MENU)


def _handle_telegram_callback(cq: dict) -> None:
    """인라인 확인 버튼. 누른 메시지의 버튼은 지우고, 결과는 새 메시지로 보낸다."""
    action, _, ts = (cq.get("data") or "").partition(":")
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    is_movie = action.startswith("mv_")
    ttl = (TG_MOVIE_TTL_SEC if is_movie or action.startswith("re_")
           else TG_REQUEST_TTL_SEC if action.startswith("rq_") else TG_CONFIRM_TTL_SEC)
    expired = action != "cancel_off" and (not ts.isdigit() or time.time() - int(ts) > ttl)
    key = (chat_id, msg.get("message_id"), action if is_movie else "")
    with _tg_handled["lock"]:
        duplicate = key in _tg_handled["messages"]
        _tg_handled["messages"].add(key)
        if len(_tg_handled["messages"]) > 200:
            _tg_handled["messages"].clear()
    if duplicate:
        _tg_call("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": "이미 처리했어요."})
        return
    _tg_call("answerCallbackQuery", {"callback_query_id": cq.get("id"),
                                     "text": "만료된 버튼이에요. 메뉴에서 다시 눌러주세요." if expired else ""})
    if chat_id and msg.get("message_id") and not is_movie:
        _tg_call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg["message_id"],
                                            "reply_markup": {"inline_keyboard": []}})
    if expired or action == "dismiss":
        return
    if is_movie:
        _tg_movie_download(action[3:])
        return
    if action.startswith("re_"):
        _tg_reencode_action(action)
        return
    if action[:8] in ("rq_done_", "rq_hold_"):
        status = "done" if action.startswith("rq_done_") else "hold"
        item = _set_request_status(action[8:], status)
        if item:
            _tg_result(True, f"'{item['title']}' 요청을 {media_requests.STATUSES[status]}(으)로 표시했어요.")
        return
    if action == "batch":
        ok, message, _code = _request_batch_run("telegram-trigger")
        _tg_result(ok, message)
    elif action == "off_force":
        _tg_shutdown(force=True)
    elif action == "cancel_off":
        ok, message, _code = _request_cancel_shutdown()
        _tg_result(ok, message)
    elif action[:4] in ("ytv_", "yta_"):
        mode = "video" if action.startswith("ytv_") else "audio"
        try:
            youtube.enqueue(ytdl.video_url(action[4:]), mode, "telegram")
        except ValueError as e:
            _tg_result(False, str(e))
            return
        _tg_notify("⬇ 받기 시작했어요. 끝나면 알려 드릴게요.\n진행 상황은 Media Router > 작업 > 유튜브에서 볼 수 있어요.")


def _tg_show_menu_once(token: str) -> None:
    """봇마다 처음 한 번만 메뉴를 띄워준다 (재시작마다 보내면 스팸).
    getUpdates가 성공한 뒤에만 부른다 - 다른 서비스가 웹훅으로 쓰는 봇이면
    그 서비스의 메뉴를 덮어쓰게 되므로(2026-10-15 nas-bot 사고) 보내지 않는다."""
    key = _tg_bot_key(token)
    marker = _tg_menu_marker(token)
    if marker.exists() or time.time() - _tg_handled["menu_tried"].get(key, 0) < TG_MENU_RETRY_SEC:
        return
    # 실패하면(예: 새 봇에서 아직 Start를 안 눌러 "chat not found") 5분 뒤 다시 시도
    _tg_handled["menu_tried"][key] = time.time()
    # getUpdates가 성공한 바로 그 토큰으로 보낸다 (그 사이 설정이 바뀌어도 엉뚱한 봇에 안 가게)
    _tg_call("setMyCommands", {"commands": [{"command": "menu", "description": "버튼 메뉴 열기"}]}, token)
    if not _tg_notify("이제 아래 버튼으로 인코딩·배치·PC 켜기/끄기와 영화 베스트 받기를 할 수 있어요.\n메뉴가 안 보이면 /menu 를 보내세요.", TG_MENU, token):
        return  # 5분 뒤 다시 시도 (그 전에도 봇에 아무 메시지나 보내면 메뉴가 뜸)
    try:
        marker.touch()
    except OSError:
        app.logger.warning("텔레그램 메뉴 표시 기록 실패")


def _tg_safe(handler, *args) -> None:
    """핸들러 스레드에서 예외가 나도 조용히 죽지 않게 - 로그엔 종류만, 사용자에겐 짧게 알림."""
    try:
        handler(*args)
    except Exception as e:
        app.logger.warning("텔레그램 버튼 처리 오류(%s): %s", handler.__name__, type(e).__name__)
        _tg_notify(f"❌ 처리 중 오류가 났어요 ({type(e).__name__}). 웹 화면에서 다시 시도해주세요.")


def _telegram_listener_loop() -> None:
    offset = 0
    offset_bot = None
    _tg_ready.wait()
    while True:
        try:
            tg = _tg_cfg()
            token = tg.get("bot_token")
            allowed_chat_id = str(tg.get("chat_id") or "")
            if not tg.get("enabled") or not token or not allowed_chat_id:
                time.sleep(30)
                continue
            if offset_bot != _tg_bot_key(token):
                # 봇을 바꾸면 update_id 체계가 달라서, 예전 봇의 offset을 쓰면 새 봇 메시지가 무시될 수 있다
                offset, offset_bot = 0, _tg_bot_key(token)

            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": 25,
                        "allowed_updates": json.dumps(["message", "callback_query"])},
                timeout=35,
            )
            data = resp.json()
            if not data.get("ok"):
                # error_code/description에는 토큰이 없다. 409 = 다른 서비스가 같은 봇을
                # 웹훅/폴링으로 쓰는 중 → 버튼 수신 불가. 설정에서 전용 봇으로 바꿔야 하므로
                # 5분마다만 다시 보고, 로그는 봇마다 한 번만 남긴다.
                # 409라도 "terminated by other getUpdates"는 잠깐 겹친 폴링이라 오래 쉴 필요 없다
                if data.get("error_code") == 409 and "webhook" in str(data.get("description")).lower():
                    if offset_bot not in _tg_handled["conflict_logged"]:
                        _tg_handled["conflict_logged"].add(offset_bot)
                        app.logger.warning("텔레그램 봇을 다른 서비스가 쓰고 있어 버튼을 받을 수 없음: %s", str(data.get("description"))[:200])
                    # 5분 대기 중에도 설정에서 토큰을 바꾸면 바로 새 봇으로 넘어가게 5초씩 쪼개서 잔다
                    for _ in range(60):
                        time.sleep(5)
                        if _tg_bot_key(_tg_cfg().get("bot_token") or "") != offset_bot:
                            break
                else:
                    app.logger.warning("텔레그램 getUpdates 실패: %s %s", data.get("error_code"), str(data.get("description"))[:200])
                    time.sleep(5)
                continue
            _tg_show_menu_once(token)

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                cq = upd.get("callback_query")
                if cq:
                    if str(((cq.get("message") or {}).get("chat") or {}).get("id") or "") == allowed_chat_id:
                        threading.Thread(target=_tg_safe, args=(_handle_telegram_callback, cq), daemon=True).start()
                    continue
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat_id = str((msg.get("chat") or {}).get("id") or "")
                if chat_id != allowed_chat_id:
                    # 설정의 Chat ID가 틀렸을 때 원인을 알 수 있게, 채팅마다 한 번 끝 4자리만 남긴다
                    if chat_id and chat_id not in _tg_handled["unknown_chats"]:
                        _tg_handled["unknown_chats"].add(chat_id)
                        app.logger.warning("텔레그램: 설정에 없는 채팅(ID 끝자리 …%s)에서 온 메시지 무시", chat_id[-4:])
                    continue
                if not text:
                    continue
                threading.Thread(target=_tg_safe, args=(_handle_telegram_text, text), daemon=True).start()
        except Exception as e:
            # traceback에는 토큰이 든 요청 URL이 찍히므로 예외 종류만 남긴다
            app.logger.warning("텔레그램 리스너 오류: %s", type(e).__name__)
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
                        _tg_notify("✅ 다운로드/인코딩이 모두 끝나서 KCK-GAMING을 5분 후 종료합니다. 계속 쓰려면 아래 'PC 종료 취소'를 누르세요.",
                                   _tg_confirm(("⏸ PC 종료 취소", "cancel_off")))
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
    downloads = _partial_downloads() if active else []
    return jsonify({
        "success": True, "active": active, "started_at": started_at,
        "downloading": bool(downloads) if active else None,
        "download_count": len(downloads),
        "download_files": [d.replace("/", "\\").rsplit("\\", 1)[-1] for d in downloads[:5]],
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

def session_role(req):
    """로그인 역할: "admin"(관리자) / "viewer"(보기 전용) / None(로그인 안 됨).
    관리자 비밀번호가 없으면(인증 끔) 누구나 관리자."""
    if not PASSWORD:
        return "admin"
    token = req.cookies.get("media_admin", "")
    if token:
        try:
            data = _session_serializer.loads(token, max_age=SESSION_MAX_AGE)
            if data.get("a") is True:
                return "viewer" if data.get("r") == "viewer" else "admin"
        except (BadSignature, SignatureExpired):
            pass
    return None


def authed(req):
    if req.path == "/health" or session_role(req):
        return True
    return redirect("/login")


# 보기 전용 계정에 숨기는 화면 (가족 Jellyfin·Plex 계정명, 서버 경로·연결 정보)
VIEWER_HIDDEN_PREFIXES = ("/settings", "/users", "/api/users", "/skills", "/api/skills",
                          "/requests", "/api/requests")  # 요청 목록엔 단톡방 사람 이름이 있다
READONLY_MESSAGE = "읽기 전용 계정이라 바꿀 수 없어요."


@app.before_request
def _readonly_guard():
    """보기 전용 계정의 변경 요청(GET 외 전부)과 숨긴 화면 접근을 서버에서 막는다.
    화면에서 버튼을 숨기는 건 보기 좋으라고 하는 것이고, 실제 차단은 여기서 한다."""
    if session_role(request) != "viewer":
        return None
    path = request.path
    wants_html = "text/html" in (request.headers.get("Accept") or "")
    if path.startswith(VIEWER_HIDDEN_PREFIXES):
        if request.method == "GET" and wants_html and not path.startswith("/api/"):
            return redirect("/?mode=check")
        return jsonify({"success": False, "ok": False, "error": "보기 전용 계정은 볼 수 없는 화면이에요.",
                        "message": "보기 전용 계정은 볼 수 없는 화면이에요."}), 403
    if request.method in ("GET", "HEAD", "OPTIONS") or path in ("/login", "/logout"):
        return None
    if wants_html:
        return make_response(
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<p style='font-family:sans-serif;padding:24px'>{READONLY_MESSAGE} "
            "<a href='javascript:history.back()'>돌아가기</a></p>", 403)
    return jsonify({"success": False, "ok": False, "error": READONLY_MESSAGE, "message": READONLY_MESSAGE}), 403


@app.context_processor
def _inject_role():
    role = session_role(request)
    return {"viewer": role == "viewer", "has_login": bool(PASSWORD), "processing_pause": processing_pause_state()}


# ── 자동 정리 일시정지 (2026-10-19) ──────────────────────────
# 호스트의 run_all.sh가 이 파일을 보고 이름 변환(drama_name)·이동(media_router)·aniname을 건너뛴다.
# data/ 는 컨테이너(/app/data)와 호스트(/volume1/docker/media-router/data)가 같은 폴더다.
PROCESSING_PAUSE_PATH = DATA_DIR / "processing_paused.json"


def processing_pause_state() -> dict:
    try:
        data = json.loads(PROCESSING_PAUSE_PATH.read_text(encoding="utf-8"))
        if data.get("paused") is True:
            return {"paused": True, "since": data.get("since"), "note": data.get("note") or ""}
    except Exception:
        pass
    return {"paused": False}


@app.route("/api/processing_pause", methods=["GET", "POST"])
def api_processing_pause():
    auth = authed(request)
    if auth is not True:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        if body.get("paused"):
            PROCESSING_PAUSE_PATH.write_text(json.dumps(
                {"paused": True, "since": time.time(), "note": str(body.get("note") or "")[:200]},
                ensure_ascii=False), encoding="utf-8")
            # run_all.sh는 root(예약작업)와 kck9010(웹 버튼) 양쪽에서 읽는다
            try:
                os.chmod(PROCESSING_PAUSE_PATH, 0o664)
            except OSError:
                pass
        else:
            try:
                PROCESSING_PAUSE_PATH.unlink()
            except FileNotFoundError:
                pass
    return jsonify({"ok": True, **processing_pause_state()})


# ── 유튜브 다운로드 (2026-10-19) ──────────────────────────
# 휴대폰 유튜브 앱 → 공유 → 텔레그램 봇, 또는 웹 "작업 > 유튜브" 화면에서 링크를 넣으면
# yt-dlp로 Drive(영상) / Drive/음악(mp3)에 받는다. 자세한 건 youtube_dl.py
import movies
MOVIE_JOBS_PATH = DATA_DIR / "movie_jobs.json"
MOVIE_TMDB_CACHE = DATA_DIR / "movie_tmdb_cache.json"
KOBIS_CACHE = DATA_DIR / "kobis_cache.json"
KOBIS_API_KEY = os.getenv("KOBIS_API_KEY", "").strip()


def _movie_best_data(force: bool = False) -> dict:
    """일간·주간 베스트 + TMDB 정보 + 같은 영화 묶기 + 국내 관객수 (텔레그램과 웹이 같이 씀)"""
    best = _movie_cached("best", movies.fetch_best, force)
    key = get_tmdb_api_key()
    day, week = movies.dedupe_best(movies.enrich(best["day"], key, MOVIE_TMDB_CACHE),
                                   movies.enrich(best["week"], key, MOVIE_TMDB_CACHE))
    _add_audience(day + week)
    return {"day": day, "week": week}


def _add_audience(items: list[dict]) -> None:
    import kobis
    from concurrent.futures import ThreadPoolExecutor
    if not KOBIS_API_KEY:
        return
    with ThreadPoolExecutor(max_workers=4) as ex:
        auds = list(ex.map(lambda m: kobis.audience(KOBIS_API_KEY, m.get("tmdb_title") or "", m.get("year"), KOBIS_CACHE), items))
    for m, a in zip(items, auds):
        m["audience"] = a
        m["audience_text"] = kobis.fmt(a)


def _tg_movie_best() -> None:
    """티프리카 영화 일간·주간 베스트 = 포스터 모음 사진 1장 + 번호 목록 + ⬇번호 버튼 (누르면 _tg_movie_download).
    영화마다 사진을 따로 보내면 메시지가 수십 개라 목록 같지 않아서(10-27) 한 장으로 모았다."""
    try:
        data = _movie_best_data(force=True)
    except Exception as e:
        _tg_result(False, f"티프리카 영화 베스트를 못 가져왔어요 ({type(e).__name__}). 주소가 바뀌었는지 확인해 주세요.")
        return
    if not data["day"] and not data["week"]:
        _tg_result(False, "티프리카 영화 베스트 목록이 비어 있어요. 페이지 구조가 바뀌었는지 확인해 주세요.")
        return
    items = [dict(m, kind="day") for m in data["day"]] + [dict(m, kind="week") for m in data["week"]]
    now = int(time.time())
    caption = _movie_best_caption(data["day"], data["week"])
    buttons = [{"text": f"⬇{i}", "callback_data": f"mv_{m['id']}:{now}"} for i, m in enumerate(items, 1)]
    markup = {"inline_keyboard": [buttons[i:i + 5] for i in range(0, len(buttons), 5)]}
    try:
        import movie_collage
        image = movie_collage.make(items)
    except Exception:
        app.logger.exception("영화 베스트 포스터 모음 만들기 실패")
        image = None
    tg = _tg_cfg()
    if image and tg.get("enabled") and tg.get("chat_id") and tg.get("bot_token"):
        try:
            res = requests.post(f"https://api.telegram.org/bot{tg['bot_token']}/sendPhoto",
                                data={"chat_id": tg["chat_id"], "caption": caption,
                                      "reply_markup": json.dumps(markup, ensure_ascii=False)},
                                files={"photo": ("best.jpg", image, "image/jpeg")}, timeout=30).json()
            if res.get("ok"):
                return
        except Exception as e:
            app.logger.warning("영화 베스트 사진 전송 실패: %s", type(e).__name__)
    _tg_notify(caption, markup)   # 사진을 못 보내면 글 목록 + 버튼만


def _movie_best_caption(day: list[dict], week: list[dict]) -> str:
    """사진 설명(1024자 제한)에 들어갈 번호 목록"""
    def line(i, m):
        title = m["display"] if len(m["display"]) <= 24 else m["display"][:23] + "…"
        extra = "".join(x for x in (f" ★{m['rating']}" if m.get("rating") else "",
                                    f" 👥{m['audience_text']}" if m.get("audience_text") else ""))
        return f"{i}. {title}{extra}"
    lines = ["🎞 티프리카 영화 베스트 — 번호를 누르면 받아요 (하루 유효)", "🔥 일간"]
    lines += [line(i, m) for i, m in enumerate(day, 1)]
    lines.append("📅 주간")
    lines += [line(i, m) for i, m in enumerate(week, len(day) + 1)]
    text = "\n".join(lines)
    return text if len(text) <= 1024 else text[:1023] + "…"


def _tg_movie_download(post_id: str) -> None:
    if not post_id.isdigit():
        return
    try:
        ok, message = start_movie_download(post_id)
    except Exception as e:
        app.logger.exception("영화 받기 실패")
        ok, message = False, f"영화를 받지 못했어요 ({type(e).__name__}: {e})"
    _tg_result(ok, message)


def start_movie_download(post_id: str) -> tuple[bool, str]:
    """티프리카 영화 글 → TMDB 제목·연도 → qBittorrent (_movies/<해시>) + movie_jobs.json"""
    view = movies.fetch_view(post_id)
    if not view["btih"]:
        return False, "글에서 토렌트(마그넷)를 찾지 못했어요."
    post_title = re.sub(r"^토렌트\s*-\s*", "", view["page_title"]).strip() or post_id
    ko, en, year = movies.split_post_title(post_title)
    found = movies.tmdb_lookup(get_tmdb_api_key(), ko, en, year)
    if found:
        title, year = found["title"], found["year"]
    else:
        title = ko or en or post_title
    display = movies.make_prefix(title, year)
    prefix = movies.make_prefix(title, year, found["id"] if found else None)
    folder = movies.initial_folder(title)
    btih = view["btih"]

    # 기록만 보지 않고 qBittorrent를 확인한다 (qBittorrent 화면에서 지우거나 멈춘 경우)
    _movie_reconcile()
    if any(j.get("btih") == btih and j.get("status") == "downloading" for j in movies.load_jobs(MOVIE_JOBS_PATH)):
        info = _qbit_torrents([btih])
        t = info.get(btih) if info else None
        if t and t.get("state") in _QBIT_STOPPED_STATES:
            _qbit_post("/api/v2/torrents/start", {"hashes": btih})
            return True, f"⏯ 멈춰 있던 '{display}'을(를) 다시 받기 시작했어요."
        if t:
            pct = f" ({t['progress'] * 100:.1f}%)"
        else:  # 목록에 없는데 취소로 안 바뀌었으면 다 받고 옮기기 기다리는 중 (연결 실패면 표시 없이)
            pct = "" if info is None else " (다 받고 옮기기 기다리는 중)"
        return False, f"'{display}'은(는) 이미 받는 중이에요{pct}."
    _qbit_post("/api/v2/torrents/add", {
        "urls": movies.magnet(btih),
        "savepath": f"{movies.QBIT_MOVIE_DIR}/{btih}",
        "autoTMM": "false",
        "tags": "영화",
        "rename": post_title,
    })
    job = {"btih": btih, "post_id": post_id, "post_title": post_title, "prefix": prefix, "display": display,
           "folder": folder, "tmdb_id": found["id"] if found else None,
           "subs": view["subs"], "added": int(time.time()), "status": "downloading"}
    movies.update_jobs(MOVIE_JOBS_PATH, lambda jobs: jobs.append(job))

    lines = [f"⬇ '{display}'(으)로 받기 시작했어요 → 영화/{folder} 폴더."]
    if not found:
        lines.append("⚠ TMDB에서 못 찾아서 글 제목으로 이름을 붙여요. Plex에서 확인해 주세요.")
    if view["subs"]:
        lines.append("💬 자막이 따로 붙어 있어요 (" + ", ".join(view["subs"][:2]) + ").\n"
                     "아래 글에서 받아 Drive에 넣어 주시면 영화 파일과 같은 이름으로 옆에 옮길게요.\n"
                     + movies.post_url(post_id))
    lines.append("다 받으면 이름을 붙여 옮기고 알려 드릴게요 (배치 20분 주기).")
    return True, "\n".join(lines)


# qBittorrent 5.x는 멈춤이 stopped, 4.x는 paused
_QBIT_STOPPED_STATES = {"stoppedDL", "pausedDL", "stoppedUP", "pausedUP"}


def _qbit_torrents(hashes: list[str]) -> dict | None:
    """{해시: 토렌트 정보}. qBittorrent에 연결이 안 되면 None (그때는 기록을 건드리지 않음)"""
    if not hashes:
        return {}
    try:
        return {t["hash"].lower(): t for t in _qbit_get("/api/v2/torrents/info", {"hashes": "|".join(hashes)})}
    except Exception:
        app.logger.warning("qBittorrent 목록 확인 실패 (영화 상태)")
        return None


def _movie_reconcile() -> dict:
    """받는 중인 영화 중 qBittorrent에서 사라졌고 다 받은 파일도 없는 건 '취소됨'으로.
    (다 받으면 qBittorrent가 공유 비율 제한으로 바로 지우므로, 목록에 없다고 다 취소는 아니다)
    돌려주는 값: {해시: qBittorrent 토렌트 정보} (진행률 표시용)"""
    jobs = movies.load_jobs(MOVIE_JOBS_PATH)
    hashes = [j["btih"] for j in jobs if j.get("status") == "downloading"]
    torrents = _qbit_torrents(hashes)
    if torrents is None:
        return {}
    gone = {h for h in hashes if h not in torrents and not movies.is_complete(movies.MOVIE_DL_DIR / h)}
    if gone:
        def apply(current):
            for j in current:
                if j.get("btih") in gone and j.get("status") == "downloading":
                    j.update(status="canceled", canceled=int(time.time()))
        movies.update_jobs(MOVIE_JOBS_PATH, apply)
    return torrents


def _movie_cancel_job(btih: str) -> None:
    def apply(current):
        for j in current:
            if j.get("btih") == btih and j.get("status") == "downloading":
                j.update(status="canceled", canceled=int(time.time()))
    if MOVIE_JOBS_PATH.exists():
        movies.update_jobs(MOVIE_JOBS_PATH, apply)


def _movie_reconcile_loop() -> None:
    """화면을 안 열어도 qBittorrent에서 지운 영화가 '받는 중'으로 남아 일주일 뒤 경고가 가지 않게"""
    while True:
        time.sleep(600)
        try:
            if MOVIE_JOBS_PATH.exists():
                _movie_reconcile()
                _announce_finished_movie_requests()
        except Exception:
            app.logger.warning("영화 상태 정리 실패")


threading.Thread(target=_movie_reconcile_loop, daemon=True).start()


# ── 영화 화면 (작업 > 영화) ──
# 티프리카를 매번 긁지 않게 목록은 5분 캐시 (새로고침 버튼은 캐시 무시)
MOVIE_CACHE_SEC = 300
_movie_cache: dict = {"lock": threading.Lock(), "at": {}, "data": {}}


def _movie_cached(key: str, fn, force: bool = False):
    with _movie_cache["lock"]:
        if not force and time.time() - _movie_cache["at"].get(key, 0) < MOVIE_CACHE_SEC:
            return _movie_cache["data"][key]
    data = fn()
    with _movie_cache["lock"]:
        _movie_cache["at"][key], _movie_cache["data"][key] = time.time(), data
    return data


@app.route("/movies", methods=["GET"])
def movies_page():
    auth = authed(request)
    if auth is not True:
        return auth
    return render_template("movies.html", authed=True)


@app.route("/api/movies", methods=["GET"])
def api_movies():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    force = request.args.get("refresh") == "1"
    pg = request.args.get("page", "1")
    page = min(10, max(1, int(pg))) if pg.isdigit() else 1
    torrents = _movie_reconcile()
    jobs = movies.load_jobs(MOVIE_JOBS_PATH)[-30:][::-1]
    for j in jobs:
        if j.get("status") != "downloading":
            continue
        t = torrents.get(j["btih"])
        if t:
            j["progress"] = t.get("progress", 0)
            j["qbit_state"] = t.get("state")
        elif movies.is_complete(movies.MOVIE_DL_DIR / j["btih"]):
            j["progress"] = 1
            j["qbit_state"] = "waiting_move"
    out = {"ok": True, "jobs": jobs, "errors": []}
    try:
        out.update(_movie_best_data(force))
    except Exception as e:
        out.update(day=[], week=[])
        out["errors"].append(f"베스트 목록을 못 가져왔어요 ({type(e).__name__})")
    try:
        out["list"] = movies.enrich(_movie_cached(f"list{page}", lambda: movies.fetch_list(page), force),
                                    get_tmdb_api_key(), MOVIE_TMDB_CACHE)
        _add_audience(out["list"])
    except Exception as e:
        out["list"] = []
        out["errors"].append(f"최신 목록을 못 가져왔어요 ({type(e).__name__})")
    out["page"] = page
    out["base"] = movies.TFREECA_BASE
    return jsonify(out)


@app.route("/api/movies/download", methods=["POST"])
def api_movies_download():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    post_id = str((request.get_json(silent=True) or {}).get("post_id") or "")
    if not post_id.isdigit():
        return jsonify({"ok": False, "error": "잘못된 글 번호예요."}), 400
    try:
        ok, message = start_movie_download(post_id)
    except Exception as e:
        app.logger.exception("영화 받기 실패")
        return jsonify({"ok": False, "error": f"받지 못했어요 ({type(e).__name__}: {e})"}), 502
    return jsonify({"ok": ok, "message": message, "error": None if ok else message}), (200 if ok else 409)


# ── 단톡방 영상 요청 (2026-10-26) ──
# /request 는 로그인 없이 연다. REQUEST_PUBLIC_HOSTS(별도 주소, 예: request.kckworld.com)로
# 들어오면 요청 페이지만 열리고 관리 화면은 404 - 공개 주소에서 로그인 화면조차 안 보이게.
import media_requests
MEDIA_REQUESTS_PATH = DATA_DIR / "media_requests.json"
MEDIA_REQUEST_SETTINGS = DATA_DIR / "media_request_settings.json"
REQUEST_PUBLIC_HOSTS = {h.strip().lower() for h in os.getenv("REQUEST_PUBLIC_HOSTS", "request.kckworld.com").split(",") if h.strip()}
# '/requests'(관리 화면)가 걸리지 않게 '/request'는 정확히 비교하고, 하위 경로는 '/request/'로
_REQUEST_PUBLIC_PATHS = ("/request/", "/static/")
_rq_search_limit = media_requests.RateLimiter(30, 60)        # 검색: 1분에 30번
_rq_submit_limit = media_requests.RateLimiter(5, 3600)       # 요청: 1시간에 5번
_rq_code_fail_limit = media_requests.RateLimiter(10, 3600)   # 코드 틀림: 1시간에 10번
# 443이 공유기에서 NAS로 바로 열려 있어 Cloudflare를 안 거치면 CF-Connecting-IP를 위조할 수 있다
# → 접속지와 상관없는 전체 한도도 둔다 (코드 맞히기·도배 방지)
_rq_code_fail_total = media_requests.RateLimiter(50, 3600)
_rq_submit_total = media_requests.RateLimiter(30, 3600)


def _public_ip(req) -> str:
    # cloudflared가 붙이는 실제 접속 IP를 먼저 본다 (DSM 리버스 프록시 뒤라 remote_addr는 127.0.0.1)
    return req.headers.get("CF-Connecting-IP") or _client_ip(req)


@app.before_request
def _public_host_guard():
    host = (request.host or "").split(":")[0].lower()
    if host not in REQUEST_PUBLIC_HOSTS:
        return None
    if request.path == "/":
        return media_request_page()
    if request.path == "/request" or request.path.startswith(_REQUEST_PUBLIC_PATHS):
        return None
    return make_response("Not Found", 404)


def _request_code() -> str:
    try:
        return str(json.loads(MEDIA_REQUEST_SETTINGS.read_text(encoding="utf-8")).get("code") or "")
    except (OSError, ValueError):
        return ""


@app.route("/request", methods=["GET"])
def media_request_page():
    return render_template("request.html", categories=media_requests.CATEGORIES,
                           platforms=media_requests.PLATFORMS, open=bool(_request_code()))


@app.route("/request/search", methods=["GET"])
def media_request_search():
    if not _rq_search_limit.allow(_public_ip(request)):
        return jsonify({"ok": False, "error": "검색이 너무 많아요. 잠시 뒤에 다시 해 주세요."}), 429
    try:
        results = media_requests.tmdb_search(get_tmdb_api_key(), request.args.get("q", ""))
    except Exception:
        return jsonify({"ok": False, "error": "검색이 잠시 안 돼요. 제목을 직접 적어 주세요."}), 502
    return jsonify({"ok": True, "results": results})


@app.route("/request/submit", methods=["POST"])
def media_request_submit():
    form = request.get_json(silent=True) or {}
    ip = _public_ip(request)
    if form.get("website"):  # 사람은 못 보는 칸 - 봇이 채움
        return jsonify({"ok": True})
    code = _request_code()
    if not code:
        return jsonify({"ok": False, "error": "지금은 요청을 받지 않아요."}), 403
    if not hmac.compare_digest(str(form.get("code") or "").strip(), code):
        if not (_rq_code_fail_limit.allow(ip) and _rq_code_fail_total.allow("*")):
            return jsonify({"ok": False, "error": "잠시 뒤에 다시 해 주세요."}), 429
        return jsonify({"ok": False, "error": "단톡방 코드가 맞지 않아요."}), 403
    data, err = media_requests.validate(form)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if not (_rq_submit_limit.allow(ip) and _rq_submit_total.allow("*")):
        return jsonify({"ok": False, "error": "요청이 너무 많아요. 한 시간 뒤에 다시 해 주세요."}), 429
    detail = None
    if data["tmdb_id"]:
        try:
            detail = media_requests.tmdb_detail(get_tmdb_api_key(), data["media_type"], data["tmdb_id"])
        except Exception:
            detail = None
    if detail:
        data.update(title=detail["title"], year=detail["year"], poster=detail["poster"])
    else:
        data.update(tmdb_id=None, media_type="", year=None, poster=None)
        if not data["title"]:
            return jsonify({"ok": False, "error": "작품을 찾지 못했어요. 제목을 직접 적어 주세요."}), 400
    item = media_requests.add(MEDIA_REQUESTS_PATH, data)
    _notify_media_request(item)
    return jsonify({"ok": True})


def _request_label(it: dict) -> str:
    return f"{it['title']} ({it['year']})" if it.get("year") else it["title"]


def _notify_media_request(it: dict) -> None:
    lines = [f"🙋 {it['name']}님 요청", f"{_request_label(it)}",
             " · ".join(x for x in (it["category"], it.get("platform")) if x)]
    if it.get("memo"):
        lines.append(f"메모: {it['memo']}")
    if it.get("tmdb_id"):
        lines.append(f"https://www.themoviedb.org/{it['media_type']}/{it['tmdb_id']}")
    text = "\n".join(lines)
    markup = _tg_confirm(("✅ 완료", f"rq_done_{it['id']}"), ("⏸ 보류", f"rq_hold_{it['id']}"))
    if it.get("poster"):
        tg = _tg_cfg()
        if tg.get("enabled") and tg.get("chat_id"):
            res = _tg_call("sendPhoto", {"chat_id": tg["chat_id"], "photo": it["poster"].replace("/w185/", "/w342/"),
                                         "caption": text[:1000], "reply_markup": markup})
            if (res or {}).get("ok"):
                return
    _tg_notify(text, markup)


def _set_request_status(req_id: str, status: str, background: bool = False) -> dict | None:
    """상태를 바꾸고, 완료인데 아직 안내를 안 보냈으면 카톡방에 옮길 안내를 텔레그램으로 보낸다.
    '보냈는지'는 상태와 따로 announced로 남긴다 - 다른 경로(기록 파일 직접 수정 등)로 완료돼도 한 번은 가게."""
    item = media_requests.update(MEDIA_REQUESTS_PATH, req_id, status=status, handled=int(time.time()))
    if item and status == "done" and not item.get("announced"):
        if background:   # 웹 버튼은 TMDB·라이브러리 확인을 기다리지 않게
            threading.Thread(target=_tg_safe, args=(_announce_request, item), daemon=True).start()
        else:
            _announce_request(item)
    return item


def _announce_request(it: dict) -> None:
    if _notify_request_done(it):
        media_requests.update(MEDIA_REQUESTS_PATH, it["id"], announced=int(time.time()))


def _announce_finished_movie_requests() -> None:
    """요청한 영화(같은 TMDB 번호)가 영화 받기로 라이브러리에 들어왔으면 요청을 완료하고 안내를 보낸다"""
    done_ids = {j.get("tmdb_id") for j in movies.load_jobs(MOVIE_JOBS_PATH)
                if j.get("status") == "done" and j.get("tmdb_id")}
    for it in media_requests.load(MEDIA_REQUESTS_PATH):
        if (it.get("category") == "영화" and it.get("tmdb_id") in done_ids
                and not it.get("announced") and it.get("status") != "hold"):
            _set_request_status(it["id"], "done")


def _notify_request_done(it: dict) -> bool:
    """포스터 + '요청하신 작품이 올라왔어요' 안내 (사진은 길게 눌러 전달, 글은 복사해서 카톡방에)"""
    detail = None
    if it.get("tmdb_id"):
        try:
            detail = media_requests.tmdb_detail(get_tmdb_api_key(), it["media_type"], it["tmdb_id"])
        except Exception:
            detail = None
    try:
        cfg = load_cfg()
        library = media_requests.library_line(it, cfg.get("rules") or [], cfg.get("base_paths") or {},
                                              movies.MOVIE_LIBRARY, movies.load_jobs(MOVIE_JOBS_PATH))
    except Exception:
        app.logger.exception("요청 완료: 라이브러리 확인 실패")
        library = ""
    caption = media_requests.done_caption(it, detail, library)
    poster = (detail or {}).get("poster") or it.get("poster")
    if poster:
        tg = _tg_cfg()
        if tg.get("enabled") and tg.get("chat_id"):
            res = _tg_call("sendPhoto", {"chat_id": tg["chat_id"], "photo": poster.replace("/w185/", "/w500/"),
                                         "caption": caption[:1000]})
            if (res or {}).get("ok"):
                return True
    return _tg_notify(caption)


@app.route("/requests", methods=["GET"])
def media_requests_page():
    auth = authed(request)
    if auth is not True:
        return auth
    return render_template("requests.html", authed=True, statuses=media_requests.STATUSES,
                           code=_request_code(), public_hosts=sorted(REQUEST_PUBLIC_HOSTS))


@app.route("/api/requests", methods=["GET"])
def api_media_requests():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    return jsonify({"ok": True, "items": media_requests.load(MEDIA_REQUESTS_PATH)[::-1]})


@app.route("/api/requests/<req_id>", methods=["POST"])
def api_media_request_update(req_id):
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    body = request.get_json(silent=True) or {}
    if body.get("delete"):
        ok = media_requests.delete(MEDIA_REQUESTS_PATH, req_id)
    else:
        status = body.get("status")
        if status not in media_requests.STATUSES:
            return jsonify({"ok": False, "error": "잘못된 상태예요."}), 400
        ok = _set_request_status(req_id, status, background=True) is not None
    return jsonify({"ok": ok}), (200 if ok else 404)


@app.route("/api/requests/code", methods=["POST"])
def api_media_request_code():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    code = str((request.get_json(silent=True) or {}).get("code") or "").strip()
    if len(code) > 20:
        return jsonify({"ok": False, "error": "코드는 20자까지예요."}), 400
    MEDIA_REQUEST_SETTINGS.write_text(json.dumps({"code": code}, ensure_ascii=False), encoding="utf-8")
    return jsonify({"ok": True, "code": code})


# ── 예능 일괄 재인코딩 (2026-10-27, /volume1/docker/reencode — 미디어 정리 세션의 reencode.py) ──
# run_all.sh가 20분마다 reencode.py run(완료분 회수 + Drive/인코딩에 넣기). 여기서는 새벽 자동 인코딩,
# 아침 진행 요약, 텔레그램·웹 버튼(일시정지/재개/비우기/상태)을 맡는다.
import reencode_ctl
REENCODE_NIGHT_AT = os.getenv("REENCODE_NIGHT_AT", "01:00")
REENCODE_MORNING_AT = os.getenv("REENCODE_MORNING_AT", "08:00")
REENCODE_SYNC_TIMEOUT_SEC = 2 * 3600          # Drive가 PC로 내려받는 걸 새벽엔 넉넉히 기다린다
REENCODE_SCHED_PATH = DATA_DIR / "reencode_sched.json"


def _reencode_buttons() -> dict:
    now = int(time.time())
    pause = ("▶ 재개", "re_resume") if reencode_ctl.paused() else ("⏸ 일시정지", "re_stop")
    return {"inline_keyboard": [[{"text": t, "callback_data": f"{a}:{now}"}
                                 for t, a in (pause, ("🗑 비우기", "re_clear"), ("📊 상태", "re_status"))]]}


def _reencode_status_text(head: str = "♻️ 예능 재인코딩") -> str:
    st = reencode_ctl.state()
    if not st.get("available"):
        return f"{head}\n(queue.json을 못 읽었어요)"
    lines = [head, st["summary"]]
    if st["failed"]:
        lines.append("실패: " + ", ".join(f"#{f['id']} {f['name'][:20]}" for f in st["failed"][:5])
                     + (f" 외 {len(st['failed']) - 5}개" if len(st["failed"]) > 5 else ""))
    waiting = reencode_ctl.waiting_files(ENCODING_HOLD_PATH)
    if waiting:
        lines.append(f"Drive/인코딩에서 인코딩 기다리는 중: {len(waiting)}개")
    return "\n".join(lines)


def _reencode_clear() -> tuple[bool, str]:
    """Drive 복사본을 지우는 일이라 호스트에서: run_all.sh가 SSH 요청 이름 'reencode-clear'를 보고 처리"""
    if not Path(RUN_BATCH_SSH_KEY).exists():
        return False, "SSH 키가 없어서 호스트에 비우기를 요청하지 못했어요."
    try:
        subprocess.Popen(["ssh", "-i", RUN_BATCH_SSH_KEY, "-p", RUN_BATCH_SSH_PORT, "-o", "StrictHostKeyChecking=no",
                          "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                          f"{RUN_BATCH_SSH_USER}@{RUN_BATCH_SSH_HOST}", "reencode-clear"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return False, f"비우기 요청 실패: {e}"
    return True, "비우기를 요청했어요. 아직 인코딩 안 된 복사본을 Drive/인코딩에서 빼고 대기로 돌려요(끝나면 알림)."


def _tg_reencode_action(action: str) -> None:
    act = action.split(":", 1)[0]
    if act == "re_stop":
        reencode_ctl.set_paused(True)
        _tg_notify("⏸ 재인코딩 일시정지 — 새로 넣지 않아요. 이미 넣어 둔 건 인코딩되면 마무리해요.", _reencode_buttons())
    elif act == "re_resume":
        reencode_ctl.set_paused(False)
        _tg_notify("▶ 재인코딩 재개 — 다음 배치(20분 이내)부터 다시 넣어요.", _reencode_buttons())
    elif act == "re_clear":
        ok, msg = _reencode_clear()
        _tg_result(ok, msg)
    elif act == "re_status":
        _tg_notify(_reencode_status_text(), _reencode_buttons())
    elif act == "re_start":
        _run_reencode_encode(force=True)


def _run_reencode_encode(force: bool = False) -> None:
    """새벽 자동 인코딩. PC가 이미 켜져 있으면(쓰는 중일 수 있고 끝나면 5분 뒤 꺼짐) 묻는다."""
    if reencode_ctl.paused() and not force:
        return
    waiting = reencode_ctl.waiting_files(ENCODING_HOLD_PATH)
    if not waiting:
        return
    if not force and _is_pc_online(GAMING_PC_HOST):
        now = int(time.time())
        _tg_notify(f"🌙 재인코딩할 파일 {len(waiting)}개가 기다리는데 PC가 켜져 있어요.\n"
                   "지금 인코딩을 시작할까요? (끝나면 5분 뒤 PC가 꺼져요)",
                   {"inline_keyboard": [[{"text": "▶ 지금 시작", "callback_data": f"re_start:{now}"},
                                         {"text": "건너뛰기", "callback_data": f"dismiss:{now}"}]]})
        return
    _tg_notify(f"🌙 재인코딩할 파일 {len(waiting)}개 — PC를 켜서 인코딩할게요.")
    ok, message = _ensure_pc_on_and_trigger_encode(progress=_tg_notify, sync_timeout=REENCODE_SYNC_TIMEOUT_SEC)
    _tg_notify(("✅ " if ok else "❌ ") + message + ("\n끝나면 알아서 PC가 꺼질 거예요." if ok else ""))


def _reencode_due(slot: str, now: datetime, state: dict) -> bool:
    """오늘 그 시각이 지났고(1시간 안), 오늘 아직 안 했으면 True - 컨테이너 재시작으로 두 번 돌지 않게 기록"""
    hh, mm = (int(x) for x in slot.split(":"))
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return at <= now < at + timedelta(hours=1) and state.get(slot) != now.strftime("%Y-%m-%d")


def _reencode_scheduler_loop() -> None:
    while True:
        time.sleep(30)
        try:
            if not reencode_ctl.available():
                continue
            now = datetime.now(APP_TZ)
            try:
                state = json.loads(REENCODE_SCHED_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            for slot, job in ((REENCODE_NIGHT_AT, lambda: _run_reencode_encode()),
                              (REENCODE_MORNING_AT, lambda: _tg_notify(_reencode_status_text("☀️ 예능 재인코딩 아침 요약"),
                                                                       _reencode_buttons()))):
                if _reencode_due(slot, now, state):
                    state[slot] = now.strftime("%Y-%m-%d")
                    REENCODE_SCHED_PATH.write_text(json.dumps(state), encoding="utf-8")
                    _tg_safe(job)
        except Exception:
            app.logger.warning("재인코딩 예약 작업 오류")


threading.Thread(target=_reencode_scheduler_loop, daemon=True).start()


@app.route("/api/reencode", methods=["GET"])
def api_reencode():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    st = reencode_ctl.state()
    st.update(ok=True, log=reencode_ctl.log_tail(), waiting=len(reencode_ctl.waiting_files(ENCODING_HOLD_PATH)),
              night_at=REENCODE_NIGHT_AT, morning_at=REENCODE_MORNING_AT)
    return jsonify(st)


@app.route("/api/reencode/pause", methods=["POST"])
def api_reencode_pause():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    reencode_ctl.set_paused(bool((request.get_json(silent=True) or {}).get("paused")))
    return jsonify({"ok": True, "paused": reencode_ctl.paused()})


@app.route("/api/reencode/clear", methods=["POST"])
def api_reencode_clear():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    ok, msg = _reencode_clear()
    return jsonify({"ok": ok, "message": msg, "error": None if ok else msg}), (200 if ok else 500)


import youtube_dl as ytdl

youtube = ytdl.Downloader(DATA_DIR / "youtube_jobs.json", notify=lambda text: _tg_notify(text),
                          log=lambda msg: app.logger.warning("%s", msg))


@app.route("/youtube", methods=["GET"])
def youtube_page():
    auth = authed(request)
    if auth is not True:
        return auth
    return render_template("youtube.html", authed=True)


@app.route("/api/youtube", methods=["GET", "POST"])
def api_youtube():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        try:
            job = youtube.enqueue(str(body.get("url") or ""), str(body.get("mode") or "video"), "web")
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        return jsonify({"ok": True, "job": job})
    return jsonify({"ok": True, "jobs": youtube.list_jobs()})


# ── Claude 스킬 관리 (2026-10-24) ──────────────────────────
# NAS claude의 스킬 현황 보기 + k-skill 개별 설치/삭제. 자세한 건 skills_mgr.py
import skills_mgr


@app.route("/skills", methods=["GET"])
def skills_page():
    auth = authed(request)
    if auth is not True:
        return auth
    return render_template("skills.html", authed=True)


@app.route("/api/skills", methods=["GET"])
def api_skills():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    return jsonify({"ok": True, "installed": skills_mgr.list_installed()})


@app.route("/api/skills/catalog", methods=["GET"])
def api_skills_catalog():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    try:
        return jsonify({"ok": True, **skills_mgr.catalog(DATA_DIR, refresh=request.args.get("refresh") == "1")})
    except Exception as e:
        app.logger.exception("k-skill 목록 불러오기 실패")
        return jsonify({"ok": False, "error": f"k-skill 목록을 불러오지 못했어요 ({type(e).__name__})."}), 502


@app.route("/api/skills/install", methods=["POST"])
def api_skills_install():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    name = str((request.get_json(silent=True) or {}).get("name") or "")
    try:
        return jsonify({"ok": True, "skill": skills_mgr.install(DATA_DIR, name)})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/skills/remove", methods=["POST"])
def api_skills_remove():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    name = str((request.get_json(silent=True) or {}).get("name") or "")
    try:
        return jsonify({"ok": True, "trash": skills_mgr.remove(name)})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# "클로드 필수 도구 50" 가이드 원문 보기 + 목록(스킬 화면에서 씀) + 가이드 속 깃허브 스킬 설치
@app.route("/skills/guide", methods=["GET"])
def skills_guide_page():
    auth = authed(request)
    if auth is not True:
        return auth
    g = skills_mgr.guide()
    return render_template("skills_guide.html", authed=True, guide=g, guide_html=skills_mgr.guide_html())


@app.route("/api/skills/guide", methods=["GET"])
def api_skills_guide():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    return jsonify({"ok": True, "guide": skills_mgr.guide()})


@app.route("/api/skills/guide/install", methods=["POST"])
def api_skills_guide_install():
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "error": "로그인이 필요합니다."}), 401
    name = str((request.get_json(silent=True) or {}).get("name") or "")
    try:
        return jsonify({"ok": True, "skill": skills_mgr.install_guide_skill(DATA_DIR, name)})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": f"깃허브에서 받지 못했어요 ({type(e).__name__})."}), 502

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


_MODE_INPUT_RE = re.compile(r"^(?:0o|0)?([0-7]{3,4})$")


def parse_mode_input(value) -> str | None:
    """설정 화면의 권한 입력("664", "0664", "0o664")을 "0o664" 문자열로 정규화.
    예전엔 safe_int로 int("664")=십진수 664를 저장했는데, media_router.parse_mode는
    int를 이미 8진 모드값으로 보고 그대로 써서 664 → 0o1230 같은 엉뚱한 권한이
    될 수 있었다. 문자열로 저장하면 parse_mode가 int(…, 8)로 올바르게 읽는다."""
    m = _MODE_INPUT_RE.match(str(value or "").strip().lower())
    return f"0o{m.group(1)}" if m else None


def mode_display(value, default: str) -> str:
    """저장된 권한값(int 0o664 또는 "0o664" 문자열)을 화면용 "664"로."""
    if isinstance(value, int):
        return format(value, "o")
    m = _MODE_INPUT_RE.match(str(value or "").strip().lower())
    return m.group(1) if m else default


def _container_mount_points() -> list[str]:
    try:
        with open("/proc/self/mounts", encoding="utf-8") as f:
            # 공백은 \040처럼 8진 이스케이프로 나온다
            points = [re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), line.split()[1]) for line in f]
    except OSError:
        return []
    return [p for p in points if p != "/"]


def check_dir_path(path: str, mounts: list[str]) -> dict:
    """설정 경로가 실제로 있는지. 배치(media_router.py)는 호스트에서 돌고 이 웹은
    컨테이너라, 컨테이너에 마운트 안 된 경로(/volume2 등)는 "없음"이 아니라
    "확인 불가"로 구분해야 오해가 없다."""
    p = (path or "").strip()
    if not p.startswith("/"):
        return {"path": p, "state": "invalid", "message": "/로 시작하는 절대 경로여야 해요"}
    target = Path(p)
    try:
        if target.is_dir():
            return {"path": p, "state": "ok", "message": "폴더 있음"}
        if target.exists():
            return {"path": p, "state": "invalid", "message": "폴더가 아니라 파일이에요"}
    except OSError:
        pass
    if any(p == m or p.startswith(m.rstrip("/") + "/") for m in mounts):
        return {"path": p, "state": "missing", "message": "폴더가 없어요"}
    return {"path": p, "state": "unknown", "message": "웹 컨테이너에 연결 안 된 위치라 확인 불가 (배치는 호스트에서 돌아서 정상일 수 있음)"}

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

# 브라우저·아이폰은 페이지의 <link>와 상관없이 루트 경로로 아이콘을 찾기도 해서 static 파일을 그대로 준다
@app.route("/favicon.ico")
def favicon(): return app.send_static_file("favicon.ico")

@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def apple_touch_icon(): return app.send_static_file("apple-touch-icon.png")

@app.route("/", methods=["GET"])
def index():
    auth = authed(request)
    if auth is not True:
        return auth

    cfg = load_cfg()
    mode = request.args.get("mode", "check").strip()
    w_today = current_weekday()
    today_name, yest_name = WEEKDAYS[w_today], WEEKDAYS[(w_today-1)%7]
    two_days_ago_name = WEEKDAYS[(w_today-2)%7]
    selected_day = request.args.get("day","").strip()
    selected_cat = request.args.get("cat","").strip()
    hide_no_days = request.args.get("hide_no_days", "1").strip()  # 기본값: 요일 없는 규칙 숨김
    edit_id_raw = request.args.get("edit_id", "").strip()
    try:
        edit_id = int(edit_id_raw) if edit_id_raw else None
    except ValueError:
        edit_id = None
    rules = cfg.get("rules", [])

    if mode == "check":
        entries, selected_ui = _check_entries(rules, selected_day)
        return render_template("today.html",
            weekdays=WEEKDAYS, selected_day=selected_ui, rules=entries,
            today_name=today_name, yest_name=yest_name, two_days_ago_name=two_days_ago_name,
            rule_count=len(rules), today_label=_today_label())

    # 분류 규칙: 목록 필터·정렬·검색은 화면(JS)에서 한다. 처음 필터 값만 넘겨준다.
    listed = [{**r, "_idx": r["id"], "next_episode": get_next_episode(r)} for r in rules]
    listed = sort_rules_for_list(listed, "default", "asc")
    return render_template("rules.html",
        weekdays=WEEKDAYS, categories=CATEGORIES, rules=listed,
        init_day=selected_day if selected_day in WEEKDAYS else "",
        init_cat=selected_cat if selected_cat in CATEGORIES else "",
        init_show_no_days=(hide_no_days == "0"), edit_id=edit_id)


def _today_label() -> str:
    now = datetime.now(APP_TZ)
    return f"{now.month}월 {now.day}일 {WEEKDAYS[now.weekday()]}요일"


def _check_entries(rules, selected_day=""):
    """체크 목록: 고른 요일(기본은 그저께+어제+오늘)의 미처리 항목을 '요일별 항목'으로 펼친다.
    반환: (항목 목록, 화면에 표시할 선택값)"""
    w_today = current_weekday()
    today_name, yest_name = WEEKDAYS[w_today], WEEKDAYS[(w_today-1)%7]
    two_days_ago_name = WEEKDAYS[(w_today-2)%7]
    if selected_day in ("", "__three__"):
        days_to_show, selected_ui = [two_days_ago_name, yest_name, today_name], "__three__"
    else:
        days_to_show = [selected_day] if selected_day in WEEKDAYS else [today_name]
        selected_ui = selected_day if selected_day in WEEKDAYS else today_name

    rel = {two_days_ago_name: "two", yest_name: "yest", today_name: "today"}
    entries = []
    for i, r in enumerate(rules):
        days = r.get("days") or []
        if not days:
            continue
        umap = r.get("updated_map") or {}
        for d in days_to_show:
            if d not in days or umap.get(d, "N") == "Y":
                continue
            item = dict(r)
            item["rkday"] = f"{rule_key(r)}|{d}"
            item["day"] = d
            item["rel"] = rel.get(d, "")
            item["_idx"] = r.get("id", i)  # DB id 우선 사용 (점검/삭제 정확도 보장)
            item["next_episode"] = get_next_episode(r)
            entries.append(item)

    # 고정 정렬: 그저께 → 어제 → 오늘 (오늘이 항상 마지막)
    def check_key(x):
        day = x.get("day")
        order = {two_days_ago_name: 0, yest_name: 1, today_name: 999}.get(day, DAY_ORDER.get(day, 998))
        return (order, (x.get("category") or ""), (x.get("subfolder") or ""))
    return sorted(entries, key=check_key), selected_ui


@app.route("/api/gulbang", methods=["GET"])
def api_gulbang():
    """체크 목록 항목들의 결방 여부 (네이버 방송 정보 기준, 3시간 캐시)."""
    auth = authed(request)
    if auth is not True:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    import gulbang
    entries, _ = _check_entries(load_cfg().get("rules", []), request.args.get("day", "").strip())
    items = [{"rkday": e["rkday"], "subfolder": e.get("subfolder", ""), "day": e["day"]} for e in entries]
    try:
        result = gulbang.check_items(items, today=datetime.now(APP_TZ).date(),
                                     cache_file=DATA_DIR / "gulbang_cache.json")
    except Exception as e:
        app.logger.exception("결방 확인 실패")
        return jsonify({"error": f"결방 확인 실패: {e}"}), 500
    return jsonify({"items": result})


@app.route("/api/check", methods=["POST"])
def api_check():
    """체크 목록 항목을 받음/결방 처리 (해당 요일을 Y로). body: {"rkday": [...]}"""
    auth = authed(request)
    if auth is not True:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    keys = set((request.get_json(silent=True) or {}).get("rkday") or [])
    if not keys:
        return jsonify({"error": "처리할 항목이 없습니다."}), 400
    cfg = load_cfg()
    count = 0
    for r in cfg["rules"]:
        rk = rule_key(r)
        umap = r.get("updated_map") or {}
        for wd in WEEKDAYS:
            if f"{rk}|{wd}" in keys and umap.get(wd) != "Y":
                umap[wd] = "Y"
                count += 1
        r["updated_map"] = umap
    if count:
        save_cfg(cfg)
    return jsonify({"ok": True, "count": count})

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

    role = None
    if hmac.compare_digest(pw, PASSWORD):
        role = "admin"
    elif VIEWER_PASSWORD and hmac.compare_digest(pw, VIEWER_PASSWORD):
        role = "viewer"
    if role:
        _clear_login_failures(ip)
        resp=make_response(redirect("/"))
        max_age = 30 * 24 * 3600 if remember else 12 * 3600
        token = _session_serializer.dumps({"a": True} if role == "admin" else {"a": True, "r": "viewer"})
        resp.set_cookie("media_admin", token, max_age=max_age, httponly=True, samesite="Lax", secure=request.is_secure)
        return resp

    _register_login_failure(ip)
    blocked_for = _login_blocked_seconds(ip)  # 방금 실패로 임계치를 넘겼으면 바로 잠금 안내
    if blocked_for > 0:
        return redirect(f"/login?error=2&wait={blocked_for}")
    return redirect("/login?error=1")

@app.route("/logout", methods=["GET", "POST"])
def logout():
    resp = make_response(redirect("/login"))
    resp.delete_cookie("media_admin")
    return resp

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
    if rule["category"] in ("드라마", "예능"):
        try:
            add_rule_keyword(rule["category"], rule["subfolder"], rule.get("release", ""))
        except Exception:
            app.logger.exception("키워드 자동 추가 실패")
    
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
    removed = [r for r in cfg["rules"] if r.get("id") == idx]
    cfg["rules"] = [r for r in cfg["rules"] if r.get("id") != idx]
    save_cfg(cfg)
    for r in removed:
        if r.get("category") in ("드라마", "예능"):
            try:
                remove_rule_keyword(r["category"], r.get("subfolder", ""), cfg["rules"])
            except Exception:
                app.logger.exception("키워드 자동 삭제 실패")
    
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
            # ownership - 권한값은 잘못 들어오면 조용히 넘기지 않고 저장 자체를 막는다
            old_ownership = cfg.get("ownership") or {}
            file_mode = parse_mode_input(request.form.get("own_file_mode"))
            dir_mode = parse_mode_input(request.form.get("own_dir_mode"))
            if file_mode is None or dir_mode is None:
                raise ValueError("권한은 664, 775처럼 0~7 숫자 3~4자리로 입력하세요.")
            cfg["ownership"] = {
                "apply": request.form.get("own_apply") == "1",
                "user": request.form.get("own_user", "").strip() or old_ownership.get("user", "plex"),
                "group": request.form.get("own_group", "").strip() or old_ownership.get("group", "users"),
                "file_mode": file_mode,
                "dir_mode": dir_mode,
                "setgid_dirs": request.form.get("own_setgid") == "1",
                "enforce_inherit": request.form.get("own_enforce") == "1",
            }
            save_cfg(cfg)
            saved = True
        except Exception as e:
            if isinstance(e, ValueError):
                app.logger.warning("설정 저장 거부: %s", e)
            else:
                app.logger.exception("설정 저장 중 오류")
            error = str(e)
            cfg = load_cfg()  # 반쯤 바뀐 cfg가 화면에 저장된 것처럼 보이지 않게
    own = cfg.get("ownership") or {}
    return render_template("settings.html",
        own_file_mode=mode_display(own.get("file_mode"), "664"),
        own_dir_mode=mode_display(own.get("dir_mode"), "775"),
        tmdb_env_override=bool(os.getenv("TMDB_API_KEY", "").strip()),
        cfg=cfg, categories=CATEGORIES, saved=saved, error=error)

@app.route("/settings/check_paths", methods=["POST"])
def settings_check_paths():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    paths = (request.get_json(silent=True) or {}).get("paths") or []
    mounts = _container_mount_points()
    return jsonify({"success": True, "results": [check_dir_path(str(p), mounts) for p in paths[:50]]})


@app.route("/settings/test_telegram", methods=["POST"])
def settings_test_telegram():
    """입력칸에 새 값이 있으면 그걸로, 비어 있으면 저장된 값으로 테스트 메시지를 보낸다."""
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    body = request.get_json(silent=True) or {}
    saved_tg = load_cfg().get("telegram") or {}
    token = (body.get("bot_token") or "").strip() or saved_tg.get("bot_token", "")
    chat_id = (body.get("chat_id") or "").strip() or saved_tg.get("chat_id", "")
    if not token or not chat_id:
        return jsonify({"success": False, "message": "Bot Token과 Chat ID가 모두 있어야 해요."}), 400
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": "✅ Media Router 설정 화면에서 보낸 테스트 메시지입니다."},
            timeout=10,
        )
        data = r.json()
    except Exception as e:
        # 예외 메시지에는 토큰이 든 URL이 섞일 수 있어서 종류만 알려준다
        return jsonify({"success": False, "message": f"텔레그램 서버에 연결하지 못했어요 ({type(e).__name__})."}), 502
    if data.get("ok"):
        note = "" if saved_tg.get("enabled") else " (알림이 꺼져 있어서 평소 알림은 안 가요 - '활성화' 체크 후 저장하세요)"
        return jsonify({"success": True, "message": "테스트 메시지를 보냈어요. 텔레그램을 확인하세요." + note})
    desc = str(data.get("description") or f"HTTP {r.status_code}")[:200]
    return jsonify({"success": False, "message": f"전송 실패: {desc}"}), 400


@app.route("/settings/test_tmdb", methods=["POST"])
def settings_test_tmdb():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    typed = ((request.get_json(silent=True) or {}).get("api_key") or "").strip()
    api_key = typed or get_tmdb_api_key()
    if not api_key:
        return jsonify({"success": False, "message": "확인할 TMDB 키가 없어요."}), 400
    try:
        r = requests.get("https://api.themoviedb.org/3/configuration", params={"api_key": api_key}, timeout=8)
    except Exception as e:
        return jsonify({"success": False, "message": f"TMDB 서버에 연결하지 못했어요 ({type(e).__name__})."}), 502
    which = "입력한 키" if typed else (".env의 키" if os.getenv("TMDB_API_KEY", "").strip() else "저장된 키")
    if r.status_code == 200:
        return jsonify({"success": True, "message": f"{which}가 정상이에요."})
    if r.status_code == 401:
        return jsonify({"success": False, "message": f"{which}가 유효하지 않아요."}), 400
    return jsonify({"success": False, "message": f"TMDB 응답 오류 (HTTP {r.status_code})"}), 502


CLAUDE_LOGIN_CODE_RE = re.compile(r"^[A-Za-z0-9_#-]{10,512}$")


def _claude_login_ssh(command: str, timeout: int) -> dict:
    """호스트의 claude_relogin.sh(강제 명령)를 실행하고 JSON 결과를 돌려준다."""
    if not Path(CLAUDE_LOGIN_SSH_KEY).exists():
        return {"ok": False, "message": "SSH 키가 설정되지 않았습니다 (secrets/claude_login_key 없음)."}
    try:
        r = subprocess.run(
            ["ssh", "-i", CLAUDE_LOGIN_SSH_KEY, "-p", RUN_BATCH_SSH_PORT,
             "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             f"{RUN_BATCH_SSH_USER}@{RUN_BATCH_SSH_HOST}", command],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "NAS 응답이 너무 늦어요. 잠시 후 상태를 다시 확인해 주세요."}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        detail = (r.stderr or r.stdout).strip()[:200]
        return {"ok": False, "message": f"NAS 응답을 읽지 못했어요 (종료코드 {r.returncode}): {detail}"}


@app.route("/settings/claude_login", methods=["GET", "POST"])
def settings_claude_login():
    """NAS 백그라운드 claude 세션 재로그인. GET=상태, POST {action:start} 로그인 주소 받기,
    POST {action:code, code} 브라우저에서 받은 코드 넣기. 보기 전용은 /settings 접두어로 막힌다."""
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "message": "로그인이 필요합니다."}), 401
    if request.method == "GET":
        return jsonify(_claude_login_ssh("status", 30))
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action == "start":
        result = _claude_login_ssh("start", 40)
    elif action == "code":
        code = (body.get("code") or "").strip()
        if not CLAUDE_LOGIN_CODE_RE.match(code):
            return jsonify({"ok": False, "message": "코드 형식이 이상해요. 브라우저에 나온 코드를 그대로 붙여 넣어 주세요."}), 400
        result = _claude_login_ssh(f"code {code}", 45)  # gunicorn timeout(60초)보다 짧게
    else:
        return jsonify({"ok": False, "message": "모르는 요청이에요."}), 400
    return jsonify(result), (200 if result.get("ok") else 502)


CLAUDE_SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CLAUDE_SESSION_NAME_MAX = 40


@app.route("/settings/claude_sessions", methods=["GET", "POST"])
def settings_claude_sessions():
    """NAS 백그라운드 claude 세션 목록과 다시 열기 (2026-10-26). 데몬은 8시간 쉰 세션을 정리하는데,
    대화 기록은 남아 있어서 같은 ID로 다시 띄우면 이어서 대화할 수 있다. 같은 강제 명령 키를 쓴다.
    GET=목록, POST {action:resume, session_id} 다시 열기, POST {action:new, name} 새 세션."""
    auth = authed(request)
    if auth is not True:
        return jsonify({"ok": False, "message": "로그인이 필요합니다."}), 401
    if request.method == "GET":
        return jsonify(_claude_login_ssh("sessions", 30))
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action == "resume":
        session_id = (body.get("session_id") or "").strip()
        if not CLAUDE_SESSION_ID_RE.match(session_id):
            return jsonify({"ok": False, "message": "세션 ID 형식이 이상해요."}), 400
        result = _claude_login_ssh(f"resume {session_id}", 50)
    elif action == "new":
        name = " ".join((body.get("name") or "").split())
        if not name:
            name = "새 세션 " + datetime.now(APP_TZ).strftime("%m-%d %H:%M")
        if len(name) > CLAUDE_SESSION_NAME_MAX:
            return jsonify({"ok": False, "message": f"세션 이름은 {CLAUDE_SESSION_NAME_MAX}자 이내로 적어 주세요."}), 400
        # 강제 명령은 공백으로 인자를 나누므로 이름은 base64로 넘긴다
        encoded = base64.b64encode(name.encode("utf-8")).decode("ascii")
        result = _claude_login_ssh(f"new {encoded}", 50)
    else:
        return jsonify({"ok": False, "message": "모르는 요청이에요."}), 400
    return jsonify(result), (200 if result.get("ok") else 502)


@app.route("/settings/status", methods=["GET"])
def settings_status():
    """.env/docker-compose.yml로만 바꿀 수 있는 연동 설정을 읽기 전용으로 보여준다.
    비밀값(비밀번호·키 내용)은 내보내지 않고 '설정됨' 여부만 준다."""
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401

    qbit = {"url": QBIT_URL, "account_set": bool(QBIT_USERNAME and QBIT_PASSWORD), "ok": False}
    try:
        info = _qbit_get("/api/v2/transfer/info")
        qbit["ok"] = True
        qbit["message"] = f"연결됨 (네트워크: {info.get('connection_status', '?')})"
    except Exception as e:
        qbit["message"] = str(e)[:200]

    tg_status = {"configured": False}
    tg = load_cfg().get("telegram") or {}
    if tg.get("bot_token"):
        tg_status["configured"] = True
        me = _tg_call("getMe", {}, tg["bot_token"]) or {}
        hook = _tg_call("getWebhookInfo", {}, tg["bot_token"]) or {}
        tg_status["ok"] = bool(me.get("ok"))
        tg_status["username"] = (me.get("result") or {}).get("username")
        hook_url = (hook.get("result") or {}).get("url") or ""
        # 웹훅이 걸려 있으면 다른 서비스가 이 봇의 메시지를 가져가서 버튼이 동작하지 않는다
        tg_status["webhook_host"] = (urlparse(hook_url).hostname or "?") if hook_url else ""
        tg_status["enabled"] = bool(tg.get("enabled"))

    return jsonify({
        "success": True,
        "telegram": tg_status,
        "pc": {
            "host": GAMING_PC_HOST, "user": GAMING_PC_USER, "mac": WOL_MAC, "wol_broadcast": WOL_BROADCAST,
            "script_path": GAMING_PC_SCRIPT_PATH,
            "ssh_key": Path(GAMING_PC_SSH_KEY).exists(),
            "online": _is_pc_online(GAMING_PC_HOST),
        },
        "batch": {"ssh_key": Path(RUN_BATCH_SSH_KEY).exists(), "host": f"{RUN_BATCH_SSH_USER}@{RUN_BATCH_SSH_HOST}:{RUN_BATCH_SSH_PORT}"},
        "qbit": qbit,
        "tmdb_source": "env" if os.getenv("TMDB_API_KEY", "").strip() else ("settings" if (load_cfg().get("tmdb_api_key") or "").strip() else "none"),
    })


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


# ── BT4G 스위치 (2026-10-26) ──────────────────────────
# 예능은 티프리카 RSS(bt4g/tfreeca.py)로 받고, BT4G는 필요할 때만 켠다.
# 호스트의 bt4g.sh가 이 파일이 있으면 BT4G 검색도 돌린다(data/는 호스트와 같은 폴더).
BT4G_ENABLED_PATH = DATA_DIR / "bt4g_enabled"
QBIT_VARIETY_RULE = "예능"
QBIT_BT4G_FEED_NAME = "통합 BT4G 검색 RSS"
QBIT_BT4G_FEED_URL = "http://192.168.50.153/Python/bt4g/xml/bt4g_filtered_sorted_rss.xml"


def bt4g_state() -> dict:
    state = {"enabled": BT4G_ENABLED_PATH.exists(), "in_rule": None, "rule_error": None}
    try:
        rule = _qbit_get("/api/v2/rss/rules").get(QBIT_VARIETY_RULE)
        if rule is None:
            state["rule_error"] = f"qBittorrent에 '{QBIT_VARIETY_RULE}' 규칙이 없어요."
        else:
            state["in_rule"] = QBIT_BT4G_FEED_URL in (rule.get("affectedFeeds") or [])
    except Exception as e:
        state["rule_error"] = str(e)
    return state


def _set_bt4g_in_rule(enabled: bool) -> None:
    rule = _qbit_get("/api/v2/rss/rules").get(QBIT_VARIETY_RULE)
    if rule is None:
        raise RuntimeError(f"qBittorrent에 '{QBIT_VARIETY_RULE}' 규칙이 없어요.")
    feeds = [f for f in (rule.get("affectedFeeds") or []) if f != QBIT_BT4G_FEED_URL]
    if enabled:
        # 꺼져 있던 동안 쌓인 BT4G 기사는 이미 티프리카로 받은 것들이다. 읽음 처리하지 않고
        # 규칙에 넣으면 qBittorrent가 안 읽은 기사를 전부 새로 받아 버린다.
        _qbit_post("/api/v2/rss/markAsRead", {"itemPath": QBIT_BT4G_FEED_NAME})
        feeds.append(QBIT_BT4G_FEED_URL)
    rule["affectedFeeds"] = feeds
    _qbit_post("/api/v2/rss/setRule", {
        "ruleName": QBIT_VARIETY_RULE,
        "ruleDef": json.dumps(rule, ensure_ascii=False),
    })


@app.route("/api/bt4g_enabled", methods=["POST"])
def api_bt4g_enabled():
    auth = authed(request)
    if auth is not True:
        return jsonify({"error": "로그인이 필요합니다."}), 401
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    try:
        # 규칙을 먼저 바꾼다. qBittorrent가 안 되면 파일도 그대로 둬서 둘이 어긋나지 않게
        _set_bt4g_in_rule(enabled)
    except Exception as e:
        app.logger.exception("BT4G 스위치: qBittorrent 규칙 변경 실패")
        return jsonify({"error": f"qBittorrent 규칙을 바꾸지 못했어요: {e}"}), 502
    if enabled:
        BT4G_ENABLED_PATH.write_text(datetime.now(APP_TZ).isoformat(timespec="seconds"), encoding="utf-8")
        try:
            os.chmod(BT4G_ENABLED_PATH, 0o664)
        except OSError:
            pass
    else:
        try:
            BT4G_ENABLED_PATH.unlink()
        except FileNotFoundError:
            pass
    return jsonify({"ok": True, **bt4g_state()})


# 화질·형식만 적힌 릴리즈 값(예: '1080p WEB-DL')은 릴 그룹이 아니므로 기본(MrHulk·Marco)으로 둔다
_QUALITY_ONLY_RE = re.compile(
    r"^(?:[\s._-]*(?:2160p|1080p|720p|4k|web-?dl|webrip|nf|x264|h\.?264|aac(?:2\.0)?))+[\s._-]*$", re.I)


def _kw_norm(s: str) -> str:
    """키워드 비교용 - tfreeca.py와 같게 띄어쓰기·기호를 무시하고, BT4G용 ' 720p'는 뗀다"""
    s = re.sub(r"\s*720p\s*$", "", s.partition("|")[0].strip(), flags=re.I)
    return re.sub(r"[\W_]+", "", s).lower()


def _rule_title(subfolder: str) -> str:
    return re.sub(r"\s*\(\d{4}\)\s*$", "", subfolder).strip().replace("|", "")


def _keyword_path(category: str) -> Path:
    return DRAMA_KEYWORDS_PATH if category == "드라마" else BT4G_KEYWORDS_PATH


def add_rule_keyword(category: str, subfolder: str, release: str = "") -> bool:
    """분류 규칙에 드라마·예능을 추가하면 키워드(티프리카 자동 다운로드)에도 넣는다.
    키워드는 폴더 이름에서 '(2026)'을 뗀 제목. 이미 그 제목을 잡는 키워드가 있으면 그대로 둔다
    ('나 혼자' 키워드가 있으면 '나 혼자 산다'는 안 넣음). 드라마만 릴리즈 값을 릴로 쓴다."""
    name = _rule_title(subfolder)
    key = _kw_norm(name)
    if not key:
        return False
    path = _keyword_path(category)
    lines = parse_bt4g_keywords(path)
    if any(_kw_norm(line) and _kw_norm(line) in key for line in lines):
        return False
    release = release.strip().replace("|", "")
    if category != "드라마" or _QUALITY_ONLY_RE.match(release):
        release = ""
    lines.append(f"{name} | {release}" if release else name)
    path.write_text(render_bt4g_keywords(lines), encoding="utf-8")
    return True


def add_drama_keyword(subfolder: str, release: str) -> bool:
    return add_rule_keyword("드라마", subfolder, release)


def remove_rule_keyword(category: str, subfolder: str, remaining_rules: list[dict]) -> list[str]:
    """분류 규칙을 지우면 그 제목을 잡던 키워드도 지운다. 같은 분류의 다른 규칙도 잡는
    키워드는 남긴다 ('미운 우리'는 '우리들의 발라드'에도 걸림). 지운 키워드 목록을 돌려준다."""
    key = _kw_norm(_rule_title(subfolder))
    if not key:
        return []
    others = [_kw_norm(_rule_title(r.get("subfolder", ""))) for r in remaining_rules
              if r.get("category") == category]
    path = _keyword_path(category)
    lines = parse_bt4g_keywords(path)
    keep, removed = [], []
    for line in lines:
        k = _kw_norm(line)
        if k and k in key and not any(k in o for o in others if o):
            removed.append(line)
        else:
            keep.append(line)
    if removed:
        path.write_text(render_bt4g_keywords(keep), encoding="utf-8")
    return removed


@app.route("/bt4g_keywords", methods=["GET", "POST"])
def bt4g_keywords():
    return _keyword_page("variety", BT4G_KEYWORDS_PATH, "bt4g_keywords.txt.bak", "/bt4g_keywords")


@app.route("/drama_keywords", methods=["GET", "POST"])
def drama_keywords():
    return _keyword_page("drama", DRAMA_KEYWORDS_PATH, "drama_keywords.txt.bak", "/drama_keywords")


def _keyword_page(kind: str, path: Path, backup_name: str, action: str):
    """예능·드라마 키워드 화면 (한 줄에 검색어 하나, tfreeca.py·bt4g.py가 읽는 형식)"""
    auth = authed(request)
    if auth is not True:
        return auth

    saved = False
    error = None

    if request.method == "POST":
        try:
            rows = [k.strip() for k in request.form.getlist("row_keyword")]
            if kind == "drama":
                # 드라마는 '키워드 | 릴' (릴을 비우면 tfreeca.py가 MrHulk·Marco로 받음)
                rels = [r.strip().replace("|", "") for r in request.form.getlist("row_release")]
                rels += [""] * (len(rows) - len(rels))
                rows = [f"{k} | {r}" if k and r else k for k, r in zip(rows, rels)]
            content = render_bt4g_keywords(rows)

            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                # 백업은 소스 파일 옆이 아니라 DATA_DIR(영구 볼륨)에 남긴다.
                # docker-compose가 키워드 파일 "하나만" 바인드 마운트하므로,
                # 옆에 .bak을 쓰면 컨테이너의 임시 레이어에만 생겼다가 재배포 시 사라진다.
                backup_path = DATA_DIR / backup_name
                backup_path.write_text(
                    path.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                )
            path.write_text(content, encoding="utf-8")
            saved = True
        except Exception as e:
            app.logger.exception("%s 저장 중 오류", path.name)
            error = str(e)

    try:
        keywords = [
            tuple(part.strip() for part in line.partition("|")[::2])
            for line in parse_bt4g_keywords(path)
        ]
        load_error = None
    except Exception as e:
        app.logger.exception("%s 로딩 중 오류", path.name)
        keywords = []
        load_error = str(e)

    return render_template(
        "bt4g_keywords.html",
        authed=True,
        kind=kind,
        form_action=action,
        keywords=keywords,
        bt4g=bt4g_state() if kind == "variety" else None,
        saved=saved,
        error=error or load_error,
        conf_path=str(path),
        conf_exists=path.exists(),
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

def _request_batch_run(trigger: str = "web-trigger") -> tuple[bool, str, int]:
    """run_all.sh를 호스트에서 실행 요청 (웹 버튼·텔레그램 공용). (성공, 메시지, HTTP 코드)"""
    if not Path(RUN_BATCH_SSH_KEY).exists():
        return False, "SSH 키가 설정되지 않았습니다 (secrets/run_batch_key 없음).", 500

    with _run_batch_state["lock"]:
        elapsed = time.time() - _run_batch_state["last_triggered"]
        if elapsed < 30:
            return False, f"방금 요청했습니다. {int(30 - elapsed)}초 후 다시 시도하세요.", 429
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
                # 읽어 로그에 어디서 수동 실행했는지(web/telegram-trigger) 남긴다.
                trigger,
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        app.logger.exception("배치 실행 요청 중 오류")
        return False, f"실행 요청 실패: {e}", 500

    return True, "배치 실행을 요청했습니다. 잠시 후 로그에서 진행 상황을 확인하세요.", 200


@app.route("/run_batch", methods=["POST"])
def run_batch():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    ok, message, code = _request_batch_run()
    return jsonify({"success": ok, "message": message}), code


def _request_wol() -> tuple[bool, str, int]:
    with _wol_state["lock"]:
        elapsed = time.time() - _wol_state["last_triggered"]
        if elapsed < 10:
            return False, f"방금 요청했습니다. {int(10 - elapsed)}초 후 다시 시도하세요.", 429
        _wol_state["last_triggered"] = time.time()

    try:
        _send_wol_packet(WOL_MAC, WOL_BROADCAST)
    except Exception as e:
        app.logger.exception("WOL 패킷 전송 중 오류")
        return False, f"전송 실패: {e}", 500

    return True, "KCK-GAMING으로 WOL 패킷을 보냈습니다. 부팅까지 1~2분 정도 걸려요.", 200


@app.route("/wake_pc", methods=["POST"])
def wake_pc():
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    ok, message, code = _request_wol()
    return jsonify({"success": ok, "message": message}), code


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

    # PC 부팅 + Drive 동기화 대기로 최대 30분 넘게 걸릴 수 있어 요청을 붙잡지 않고 뒤에서 돌린다.
    # 진행·결과는 텔레그램 인코딩 요청과 같은 문구로 알린다
    if not _is_pc_online(GAMING_PC_HOST) or _nas_encode_targets():
        threading.Thread(target=_tg_safe, args=(_run_encode_with_notify,), daemon=True).start()
        return jsonify({"success": True, "message": "PC를 켜고, 인코딩할 파일이 PC로 다 올라오면 시작할게요. 진행 상황은 텔레그램으로 알려 드려요."})
    ok, message = _ensure_pc_on_and_trigger_encode()
    return jsonify({"success": ok, "message": message}), (200 if ok else 500)


def _pc_ssh(command: str):
    return subprocess.run(
        [
            "ssh",
            "-i", GAMING_PC_SSH_KEY,
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8",
            f"{GAMING_PC_USER}@{GAMING_PC_HOST}",
            command,
        ],
        capture_output=True, timeout=20,
    )


def _request_cancel_shutdown() -> tuple[bool, str, int]:
    """KCK-GAMING에 예약된 종료(인코딩 후 자동 종료, PC 끄기의 1분 유예)를 취소한다."""
    if not Path(GAMING_PC_SSH_KEY).exists():
        return False, "SSH 키가 설정되지 않았습니다 (secrets/kck_gaming_key 없음).", 500

    with _cancel_shutdown_state["lock"]:
        elapsed = time.time() - _cancel_shutdown_state["last_triggered"]
        if elapsed < 10:
            return False, f"방금 요청했습니다. {int(10 - elapsed)}초 후 다시 시도하세요.", 429
        _cancel_shutdown_state["last_triggered"] = time.time()

    if not _is_pc_online(GAMING_PC_HOST):
        return False, "PC가 꺼져있는 것 같아요.", 409

    try:
        result = _pc_ssh("shutdown /a")
    except Exception as e:
        app.logger.exception("종료 취소 요청 중 오류")
        return False, f"실행 요청 실패: {e}", 500

    # shutdown /a는 예약된 종료가 없으면 종료코드 1089로 실패한다 - 이미 취소됐거나
    # 원래 예약이 없었던 정상적인 상황이므로 에러로 취급하지 않는다.
    if result.returncode not in (0, 1089):
        detail = (_pc_text(result.stderr) or _pc_text(result.stdout)).strip()[:300]
        return False, f"SSH 실행 실패(종료코드 {result.returncode}): {detail}", 500

    if result.returncode == 1089:
        return True, "예약된 종료가 없었어요(이미 취소됐거나 원래 없었음).", 200
    return True, "KCK-GAMING의 예약 종료를 취소했습니다.", 200


@app.route("/cancel_shutdown", methods=["POST"])
def cancel_shutdown():
    """encode_all.ps1이 끝나면 5분 후 자동으로 PC를 끄는데(shutdownAfter),
    인코딩이 끝났어도 PC를 계속 쓰고 싶을 때 그 예약 종료를 취소한다.
    자주 쓰는 기능이 아니라 헤더 버튼이 아니라 인코딩 관리 페이지 본문에만
    둔다."""
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    ok, message, code = _request_cancel_shutdown()
    return jsonify({"success": ok, "message": message}), code


def _request_pc_shutdown(force: bool) -> tuple[bool, str, int, bool]:
    """1분 유예로 PC 종료를 예약한다 (웹 "PC 끄기"·텔레그램 공용).
    (성공, 메시지, HTTP 코드, 인코딩 중이라 확인이 필요한지)"""
    if not Path(GAMING_PC_SSH_KEY).exists():
        return False, "SSH 키가 설정되지 않았습니다 (secrets/kck_gaming_key 없음).", 500, False

    if not _is_pc_online(GAMING_PC_HOST):
        return False, "PC가 이미 꺼져있는 것 같아요.", 409, False

    if not force and _is_encoding_in_progress():
        return False, "지금 인코딩이 진행 중이에요. 끄면 진행 중인 파일은 처음부터 다시 해야 해요.", 409, True

    # 인코딩 확인 뒤에 둬야 "그래도 끌까요?" 재요청이 쿨다운에 막히지 않는다
    with _shutdown_pc_state["lock"]:
        elapsed = time.time() - _shutdown_pc_state["last_triggered"]
        if elapsed < 10:
            return False, f"방금 요청했습니다. {int(10 - elapsed)}초 후 다시 시도하세요.", 429, False
        _shutdown_pc_state["last_triggered"] = time.time()

    try:
        result = _pc_ssh('shutdown /s /t 60 /c "Media Router에서 PC 끄기 요청"')
    except Exception as e:
        app.logger.exception("PC 끄기 요청 중 오류")
        return False, f"실행 요청 실패: {e}", 500, False

    # 1190: 이미 종료가 예약돼 있음(인코딩 후 자동 종료 등) - 어차피 꺼지므로 실패로 보지 않는다
    if result.returncode == 1190:
        return True, "이미 종료가 예약돼 있어요. 곧 꺼집니다.", 200, False
    if result.returncode != 0:
        detail = (_pc_text(result.stderr) or _pc_text(result.stdout)).strip()[:300]
        return False, f"SSH 실행 실패(종료코드 {result.returncode}): {detail}", 500, False

    return True, "KCK-GAMING을 1분 후 종료합니다. 취소하려면 'PC 종료 취소'를 누르세요.", 200, False


@app.route("/shutdown_pc", methods=["POST"])
def shutdown_pc():
    """헤더 "PC 끄기" 버튼. 바로 끄지 않고 1분 유예를 줘서, 잘못 눌렀으면
    인코딩 관리 페이지의 'PC 종료 취소'(shutdown /a)로 되돌릴 수 있게 한다.
    인코딩 중이면 409로 한 번 돌려보내고, 화면에서 다시 확인받으면 force로 끈다."""
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    force = bool((request.get_json(silent=True) or {}).get("force"))
    ok, message, code, encoding = _request_pc_shutdown(force)
    body = {"success": ok, "message": message}
    if encoding:
        body["encoding"] = True
    return jsonify(body), code


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
    return _qbit_call("GET", path, params=params).json()


def _qbit_post(path: str, data: dict) -> None:
    _qbit_call("POST", path, data=data)


def _qbit_call(method: str, path: str, params: dict | None = None, data: dict | None = None):
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
            r = sess.request(method, f"{QBIT_URL}{path}", params=params, data=data,
                             headers={"Referer": QBIT_URL}, timeout=8)
            if r.status_code == 403:
                _qbit_state["session"] = None
                continue
            r.raise_for_status()
            return r
    raise RuntimeError("qBittorrent 인증이 반복해서 거부됐습니다.")


@app.route("/downloads", methods=["GET"])
def downloads_page():
    auth = authed(request)
    if auth is not True:
        return auth
    return render_template("downloads.html", authed=True, qbit_public_url="https://bit.kckworld.com/")


@app.route("/api/downloads/cancel", methods=["POST"])
def api_downloads_cancel():
    """다운로드 탭의 취소 버튼: qBittorrent에서 지우고 받던 파일도 지운다"""
    auth = authed(request)
    if auth is not True:
        return jsonify({"success": False, "message": "로그인이 필요합니다."}), 401
    h = str((request.get_json(silent=True) or {}).get("hash") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", h):
        return jsonify({"success": False, "message": "잘못된 토렌트예요."}), 400
    try:
        _qbit_post("/api/v2/torrents/delete", {"hashes": h, "deleteFiles": "true"})
    except Exception as e:
        return jsonify({"success": False, "message": f"qBittorrent에서 지우지 못했어요: {e}"}), 502
    _movie_cancel_job(h)
    return jsonify({"success": True})


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


# 모든 함수·라우트 정의가 끝났으니 텔레그램 리스너가 업데이트를 처리해도 된다
_tg_ready.set()


if __name__=="__main__":
    app.run(host="0.0.0.0", port=5080)
