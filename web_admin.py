# -*- coding: utf-8 -*-
from pathlib import Path
import os, hashlib, hmac, re, secrets, subprocess, time, threading
from flask import Flask, request, redirect, render_template, abort, make_response, jsonify
from datetime import datetime
from zoneinfo import ZoneInfo
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import requests
from db import load_cfg, save_cfg, DATA_DIR

BASE = Path(__file__).resolve().parent
PASSWORD = os.getenv("MEDIA_ADMIN_PASSWORD", "")

app = Flask(__name__, template_folder=str(BASE / "templates"))

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
CATEGORIES = ["드라마", "예능", "애니메이션", "다큐"]
DAY_ORDER = {d: i for i, d in enumerate(WEEKDAYS)}
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Seoul"))

RENAME_RULES_PATH = Path(os.getenv("RENAME_RULES_PATH", "/volume1/web/video_auto/rename_rules.conf"))

# 전체 배치(run_all.sh) 수동 실행 - 호스트로 SSH 접속해 강제 명령(run_all.sh)만 실행
RUN_BATCH_SSH_KEY = os.getenv("RUN_BATCH_SSH_KEY", "/app/secrets/run_batch_key")
RUN_BATCH_SSH_HOST = os.getenv("RUN_BATCH_SSH_HOST", "127.0.0.1")
RUN_BATCH_SSH_PORT = os.getenv("RUN_BATCH_SSH_PORT", "202")
RUN_BATCH_SSH_USER = os.getenv("RUN_BATCH_SSH_USER", "kck9010")
_run_batch_state = {"lock": threading.Lock(), "last_triggered": 0}


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
        # 정렬 표시용: 요일 문자열로 비교
        return (" ".join(r.get("days") or []), (r.get("category") or "").lower())

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

            new_rule = {
                "category": request.form.get("category","").strip(),
                "pattern":  pattern,
                "subfolder": request.form.get("subfolder","").strip(),
                "days": [d for d in WEEKDAYS if d in sel_days],
                "updated": "N",
                "updated_map": {d: "N" for d in sel_days if d in WEEKDAYS},
            }
            
            if pattern_or:
                new_rule["pattern_or"] = pattern_or
            if pattern2:
                new_rule["pattern2"] = pattern2
            if pattern2_or:
                new_rule["pattern2_or"] = pattern2_or
            if exclude_pattern:
                new_rule["exclude_pattern"] = exclude_pattern
            
            # 릴리즈 정보 추가 (선택사항)
            release = request.form.get("release", "").strip()
            if release:
                new_rule["release"] = release
            else:
                # 기존 릴리즈 정보 유지 (편집 시)
                if "release" in old_rule:
                    new_rule["release"] = old_rule["release"]
            
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
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        app.logger.exception("배치 실행 요청 중 오류")
        return jsonify({"success": False, "message": f"실행 요청 실패: {e}"}), 500

    return jsonify({"success": True, "message": "배치 실행을 요청했습니다. 잠시 후 로그에서 진행 상황을 확인하세요."})

@app.route("/search_tmdb", methods=["POST"])
def search_tmdb():
    auth = authed(request)
    if auth is not True:
        return auth
    
    query = request.form.get("q", "").strip()
    result = tmdb_search_info(query)
    return jsonify(result)

if __name__=="__main__":
    app.run(host="0.0.0.0", port=5080)
