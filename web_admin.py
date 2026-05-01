# -*- coding: utf-8 -*-
from pathlib import Path
import os, hashlib, re, subprocess
from flask import Flask, request, redirect, render_template, abort, make_response, jsonify
from datetime import datetime
from zoneinfo import ZoneInfo
from db import load_cfg, save_cfg

BASE = Path(__file__).resolve().parent
PASSWORD = os.getenv("MEDIA_ADMIN_PASSWORD", "")

app = Flask(__name__, template_folder=str(BASE / "templates"))

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
CATEGORIES = ["예능", "다큐", "드라마", "애니메이션"]
DAY_ORDER = {d: i for i, d in enumerate(WEEKDAYS)}
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Seoul"))


def current_weekday() -> int:
    return datetime.now(APP_TZ).weekday()

def authed(req):
    return True  # 비밀번호 인증 비활성화

def normalize_pattern(p: str) -> str:
    if not p: return p
    p = p.strip().strip('"').strip("'")
    has_wild = ('*' in p) or ('?' in p)
    has_ext_dot = '.' in p
    if not has_wild:
        return f"*{p}*.*"
    if not has_ext_dot and not p.endswith(".*"):
        return p + ".*"
    return p

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
                    item["_idx"] = i  # 원본 규칙의 인덱스 (삭제 기능용)
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
            authed=authed(request), cfg=cfg, weekdays=WEEKDAYS,
            selected_day=selected_ui, selected_cat=selected_cat,
            rules=entries, mode="check",
            today_name=today_name, yest_name=yest_name, two_days_ago_name=two_days_ago_name, categories=CATEGORIES,
            sort_key=sort_key, sort_dir=sort_dir)

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
        authed=authed(request), cfg=cfg, weekdays=WEEKDAYS,
        selected_day=selected_day, selected_cat=selected_cat, hide_no_days=hide_no_days,
        rules=filtered, mode="",
        today_name=today_name, yest_name=yest_name, two_days_ago_name=two_days_ago_name, categories=CATEGORIES,
        sort_key=sort_key, sort_dir=sort_dir)

@app.route("/check", methods=["POST"])
def check_action():
    if not authed(request): 
        return jsonify({"error": "인증 실패"}), 403
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

@app.route("/check_episodes", methods=["POST"])
def check_episodes():
    try:
        if not authed(request): 
            return jsonify({"success": False, "message": "인증 실패"}), 403
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

@app.route("/login", methods=["POST"])
def login():
    pw=request.form.get("pw","")
    if PASSWORD and pw==PASSWORD:
        resp=make_response(redirect("/"))
        resp.set_cookie("media_admin","ok:"+PASSWORD,max_age=12*3600,httponly=True,samesite="Lax",secure=True)
        return resp
    return abort(403)

@app.route("/add", methods=["POST"])
def add():
    if not authed(request): return abort(403)
    cfg = load_cfg()
    pat_raw = request.form.get("pattern", "").strip()
    pattern = normalize_pattern(pat_raw)
    sel_days = request.form.getlist("days")

    rule = {
        "category": request.form.get("category", "").strip(),
        "pattern":  pattern,
        "subfolder": request.form.get("subfolder", "").strip(),
        "days": [d for d in WEEKDAYS if d in sel_days],
        "updated": "N",
        "updated_map": {d: "N" for d in sel_days if d in WEEKDAYS},
    }
    
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
    if mode: params.append(f"mode={mode}")
    if day: params.append(f"day={day}")
    if cat: params.append(f"cat={cat}")
    if sort != "default": params.append(f"sort={sort}")
    if sort_dir != "asc": params.append(f"dir={sort_dir}")
    if hide_no_days == "1": params.append(f"hide_no_days={hide_no_days}")
    
    redirect_url = "/" + ("?" + "&".join(params) if params else "")
    return redirect(redirect_url)

@app.route("/delete", methods=["POST"])
def delete():
    if not authed(request): return abort(403)
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
    if mode: params.append(f"mode={mode}")
    if day: params.append(f"day={day}")
    if cat: params.append(f"cat={cat}")
    if sort != "default": params.append(f"sort={sort}")
    if sort_dir != "asc": params.append(f"dir={sort_dir}")
    if hide_no_days == "1": params.append(f"hide_no_days={hide_no_days}")
    
    redirect_url = "/" + ("?" + "&".join(params) if params else "")
    return redirect(redirect_url)

@app.route("/edit", methods=["GET","POST"])
def edit():
    if not authed(request): return abort(403)
    cfg = load_cfg()
    if request.method == "GET":
        ...
    else:
        idx = int(request.form.get("idx","-1"))
        old_rule = next((r for r in cfg.get("rules", []) if r.get("id") == idx), None)
        if old_rule is not None:
            pat_raw = request.form.get("pattern", "").strip()
            pattern = normalize_pattern(pat_raw)
            sel_days = request.form.getlist("days")

            new_rule = {
                "category": request.form.get("category","").strip(),
                "pattern":  pattern,
                "subfolder": request.form.get("subfolder","").strip(),
                "days": [d for d in WEEKDAYS if d in sel_days],
                "updated": "N",
                "updated_map": {d: "N" for d in sel_days if d in WEEKDAYS},
            }
            
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
        
        params = []
        # mode는 항상 추가 (빈 문자열이어도 전체 관리 모드로 감)
        params.append(f"mode={mode if mode else ''}")
        if day: params.append(f"day={day}")
        if cat: params.append(f"cat={cat}")
        if sort != "default": params.append(f"sort={sort}")
        if sort_dir != "asc": params.append(f"dir={sort_dir}")
        if hide_no_days == "1": params.append(f"hide_no_days={hide_no_days}")
        
        redirect_url = "/" + ("?" + "&".join(params) if params else "")
        return redirect(redirect_url)

@app.route("/restart", methods=["POST"])
def restart():
    if not authed(request):
        return jsonify({"success": False, "message": "인증 실패"}), 403
    try:
        script_path = BASE / "restart_web_admin.sh"
        if not script_path.exists():
            return jsonify({"success": False, "message": "재시작 스크립트를 찾을 수 없습니다."}), 404
        
        # 백그라운드로 스크립트 실행
        subprocess.Popen(
            ["bash", str(script_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(BASE)
        )
        return jsonify({"success": True, "message": "재시작 중입니다. 잠시 후 페이지를 새로고침하세요."})
    except Exception as e:
        return jsonify({"success": False, "message": f"재시작 실패: {str(e)}"}), 500

if __name__=="__main__":
    app.run(host="0.0.0.0", port=5080)
