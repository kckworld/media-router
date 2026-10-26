#!/bin/bash
# Media Router 설정 화면의 "Claude 로그인"이 SSH 강제 명령으로 호출하는 스크립트 (2026-10-24).
# 토큰이 만료돼 NAS의 백그라운드 claude 세션이 끊겼을 때 PC·SSH 없이 휴대폰 브라우저만으로
# 다시 로그인하게 해준다. authorized_keys에 이 키 전용으로 걸려 있어서 아래 명령만 받는다.
#
#   status        로그인 상태 + 진행 중인 로그인이 있으면 그 주소 (JSON)
#   start         claude auth login 을 띄우고 로그인 주소를 돌려준다 (JSON)
#   code <코드>   브라우저 로그인 후 받은 코드를 넣고 결과를 돌려준다 (JSON)
#   sessions      백그라운드 세션 목록, 마지막 대화 순 (JSON, 2026-10-26)
#   resume <ID>   데몬이 8시간 쉬어서 정리(retire)한 세션을 같은 대화로 다시 띄운다 (JSON)
#   new <이름>    새 백그라운드 세션을 띄운다. 이름은 공백 때문에 base64로 받는다 (JSON)
#
# claude auth login 은 TTY 없이도 "Paste code here" 에서 표준입력 한 줄을 기다린다.
# 표준입력을 FIFO(읽기쓰기로 열어 EOF가 안 나게)로 주고, code 명령이 그 FIFO에 써 넣는다.
set -uo pipefail

CLAUDE_BIN=/var/services/homes/kck9010/.local/bin/claude
WATCHDOG=/volume1/docker/claude-bg-watchdog.sh
STATE_DIR=${CLAUDE_RELOGIN_STATE:-/var/services/homes/kck9010/.claude-relogin}
FIFO=$STATE_DIR/stdin
OUT=$STATE_DIR/login.out
PIDFILE=$STATE_DIR/login.pid
LOGIN_TIMEOUT=900  # 브라우저 로그인 주소는 오래 못 쓰니 15분 지나면 버린다
PROJECTS_DIR=${CLAUDE_PROJECTS_DIR:-/var/services/homes/kck9010/.claude/projects}
# 세션이 일하던 폴더(worktree 등)가 지워졌을 때 대신 여는 곳. 신뢰 확인을 이미 받은 폴더여야 한다
DEFAULT_CWD=/volume1/docker
BG_OUT=$STATE_DIR/bg.out

mkdir -p "$STATE_DIR" && chmod 700 "$STATE_DIR"

login_pid() {
  local pid
  pid=$(cat "$PIDFILE" 2>/dev/null) || return 1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && echo "$pid"
}

login_url() { grep -o 'https://[^ ]*oauth/authorize[^ ]*' "$OUT" 2>/dev/null | head -1; }

auth_json() {
  local j
  # 로그아웃 상태면 auth status가 빈 출력/비JSON을 내기도 해서 결과가 비면 기본값을 준다
  j=$(env -u CLAUDE_CODE_OAUTH_TOKEN "$CLAUDE_BIN" auth status --json 2>/dev/null \
    | jq -c '{loggedIn: (.loggedIn // false), authMethod, subscriptionType}' 2>/dev/null)
  if [ -n "$j" ]; then echo "$j"; else echo '{"loggedIn":false}'; fi
}

cmd_status() {
  local url="" pending=false
  if login_pid >/dev/null; then pending=true; url=$(login_url); fi
  jq -cn --argjson auth "$(auth_json)" --argjson pending "$pending" --arg url "$url" \
    '{ok: true, auth: $auth, pending: $pending, url: $url}'
}

stop_login() {
  local pid
  if pid=$(login_pid); then kill "$pid" 2>/dev/null; sleep 1; kill -9 "$pid" 2>/dev/null; fi
  rm -f "$PIDFILE" "$FIFO"
}

cmd_start() {
  stop_login
  mkfifo -m 600 "$FIFO"
  : > "$OUT"; chmod 600 "$OUT"
  # sshd가 기다리지 않도록 입출력을 전부 떼어서 새 세션으로 띄운다
  setsid env -u CLAUDE_CODE_OAUTH_TOKEN timeout "$LOGIN_TIMEOUT" \
    "$CLAUDE_BIN" auth login --claudeai <>"$FIFO" >"$OUT" 2>&1 &
  echo $! > "$PIDFILE"
  local url=""
  for _ in $(seq 1 30); do
    url=$(login_url) && [ -n "$url" ] && break
    sleep 0.5
  done
  if [ -z "$url" ]; then
    jq -cn --arg out "$(head -c 300 "$OUT")" '{ok: false, message: ("로그인 주소를 받지 못했어요: " + $out)}'
    stop_login
    return 1
  fi
  jq -cn --arg url "$url" '{ok: true, url: $url}'
}

cmd_code() {
  local code=$1
  # 코드는 "영숫자_-#" 조합이다. 그 외 문자는 FIFO로 넘기지 않는다
  if ! [[ "$code" =~ ^[A-Za-z0-9_#-]{10,512}$ ]]; then
    echo '{"ok":false,"message":"코드 형식이 이상해요. 브라우저에 나온 코드를 그대로 붙여 넣어 주세요."}'
    return 1
  fi
  local pid
  if ! pid=$(login_pid); then
    echo '{"ok":false,"message":"진행 중인 로그인이 없어요(15분이 지났을 수 있어요). 로그인 시작부터 다시 해 주세요."}'
    return 1
  fi
  printf '%s\n' "$code" > "$FIFO"
  for _ in $(seq 1 60); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  local tail_out
  tail_out=$(sed 's/.*Paste code here[^>]*> *//; s/https:[^ ]*//g' "$OUT" | grep -v '^Opening browser\|^If the browser' | tail -c 300)
  stop_login
  if jq -e '.loggedIn == true and .authMethod == "claude.ai"' <<<"$(auth_json)" >/dev/null \
     && ! grep -qi 'failed\|error' <<<"$tail_out"; then
    # 꺼진 백그라운드 세션을 10분 기다리지 않고 바로 다시 띄운다 (결과는 watchdog 로그에)
    setsid "$WATCHDOG" </dev/null >>/volume1/docker/_logs/claude-bg-watchdog.log 2>&1 &
    jq -cn --argjson auth "$(auth_json)" '{ok: true, auth: $auth, message: "로그인됐어요. 백그라운드 세션도 바로 다시 확인할게요."}'
  else
    jq -cn --arg out "$tail_out" '{ok: false, message: ("로그인 실패: " + $out)}'
    return 1
  fi
}

cmd_sessions() {
  local list mt='{}' sid f
  list=$("$CLAUDE_BIN" agents --json --all 2>/dev/null) || list='[]'
  # 마지막 대화 시각은 대화 기록 파일의 수정 시각으로 본다. 기록이 없는 세션은 다시 열 수 없으니 뺀다
  for sid in $(jq -r '.[] | select(.kind == "background") | .sessionId' <<<"$list" 2>/dev/null); do
    [[ "$sid" =~ ^[0-9a-f-]{36}$ ]] || continue
    f=$(ls "$PROJECTS_DIR"/*/"$sid".jsonl 2>/dev/null | head -1)
    [ -n "$f" ] && mt=$(jq -c --arg s "$sid" --argjson t "$(stat -c %Y "$f")" '. + {($s): $t}' <<<"$mt")
  done
  jq -c --argjson mt "$mt" '{ok: true, sessions: ([.[] | select(.kind == "background" and $mt[.sessionId] != null)
      | {sessionId, name, state, running: (.pid != null), cwd, startedAt, lastActive: ($mt[.sessionId] * 1000)}]
      | sort_by(-.lastActive) | .[:20])}' <<<"$list"
}

# --bg는 바로 돌아오지만, 데몬이 새로 뜨면 그 출력을 물려받을 수 있다.
# 파이프로 받으면 sshd가 데몬이 끝날 때까지 기다리므로 결과는 파일로 받는다
start_bg() {
  local dir=$1; shift
  (cd "$dir" && env -u CLAUDE_CODE_OAUTH_TOKEN timeout 60 "$CLAUDE_BIN" --bg "$@" </dev/null >"$BG_OUT" 2>&1)
  grep -q '^backgrounded' "$BG_OUT"
}

cmd_resume() {
  local sid=$1 row dir name
  if ! [[ "$sid" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
    echo '{"ok":false,"message":"세션 ID 형식이 이상해요."}'
    return 1
  fi
  row=$("$CLAUDE_BIN" agents --json --all 2>/dev/null \
    | jq -c --arg s "$sid" 'first(.[] | select(.kind == "background" and .sessionId == $s)) // empty')
  if [ -z "$row" ]; then
    echo '{"ok":false,"message":"그 세션을 찾지 못했어요. 목록을 새로 고쳐 주세요."}'
    return 1
  fi
  name=$(jq -r '.name // "세션"' <<<"$row")
  # 실행 중인 세션에 --resume을 하면 사본이 하나 더 생긴다
  if jq -e '.pid != null' <<<"$row" >/dev/null; then
    jq -cn --arg n "$name" '{ok: false, message: ("\"" + $n + "\" 세션은 이미 실행 중이에요. 앱에서 바로 열면 돼요.")}'
    return 1
  fi
  dir=$(jq -r '.cwd // empty' <<<"$row")
  [ -n "$dir" ] && [ -d "$dir" ] || dir=$DEFAULT_CWD
  if start_bg "$dir" --resume "$sid"; then
    jq -cn --arg n "$name" '{ok: true, message: ("다시 열었어요. 앱 세션 목록에서 \"" + $n + "\"을 눌러 이어서 대화하세요.")}'
  else
    jq -cn --arg out "$(head -c 300 "$BG_OUT")" '{ok: false, message: ("다시 열지 못했어요: " + $out)}'
    return 1
  fi
}

cmd_new() {
  local enc=$1 name=""
  [[ "$enc" =~ ^[A-Za-z0-9+/=]{1,200}$ ]] && name=$(printf '%s' "$enc" | base64 -d 2>/dev/null)
  # 이름은 인자로만 넘겨서 셸로 해석되지 않지만, 줄바꿈 같은 제어 문자는 받지 않는다
  if [ -z "$name" ] || [ "$(printf '%s' "$name" | wc -c)" -gt 120 ] || [[ "$name" =~ [[:cntrl:]] ]]; then
    echo '{"ok":false,"message":"세션 이름이 이상해요 (40자 이내로 적어 주세요)."}'
    return 1
  fi
  if start_bg "$DEFAULT_CWD" --name "$name"; then
    jq -cn --arg n "$name" '{ok: true, message: ("새 세션을 띄웠어요. 앱 세션 목록에서 \"" + $n + "\"을 눌러 대화하세요.")}'
  else
    jq -cn --arg out "$(head -c 300 "$BG_OUT")" '{ok: false, message: ("새 세션을 띄우지 못했어요: " + $out)}'
    return 1
  fi
}

# 강제 명령이라 실제 인자는 SSH_ORIGINAL_COMMAND로 들어온다 (로컬 테스트 때는 $@)
read -r -a ARGS <<<"${SSH_ORIGINAL_COMMAND:-$*}"
case "${ARGS[0]:-status}" in
  status) cmd_status ;;
  start)  cmd_start ;;
  code)   cmd_code "${ARGS[1]:-}" ;;
  sessions) cmd_sessions ;;
  resume) cmd_resume "${ARGS[1]:-}" ;;
  new)    cmd_new "${ARGS[1]:-}" ;;
  *)      echo '{"ok":false,"message":"모르는 명령이에요."}'; exit 2 ;;
esac
