#!/bin/bash
set -euo pipefail

BASE="/volume1/web/media-router"
cd "$BASE"

LOGDIR="$BASE/logs"
mkdir -p "$LOGDIR"

# 로그 파일로 출력
exec >>"$LOGDIR/restart_web_admin.$(date +%F).log" 2>&1

echo "=== RESTART web_admin.py $(date '+%F %T') ==="

# web_admin.py 프로세스 찾기 및 종료
PID=$(ps aux | grep "[p]ython.*web_admin.py" | awk '{print $2}')

if [ -n "$PID" ]; then
    echo "기존 프로세스 종료 중... (PID: $PID)"
    kill $PID || true
    sleep 2
    
    # 강제 종료가 필요한 경우 확인
    if ps -p $PID > /dev/null 2>&1; then
        echo "강제 종료 중..."
        kill -9 $PID || true
        sleep 1
    fi
    echo "기존 프로세스 종료 완료"
else
    echo "실행 중인 web_admin.py 프로세스가 없습니다."
fi

# 가상환경 활성화
if [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "가상환경 활성화: $(which python)"
else
    echo "가상환경 없음, 시스템 Python 사용"
fi

# Python 버전 확인
python -V

# web_admin.py 재시작
echo "web_admin.py 재시작 중..."
nohup python -u web_admin.py > "$LOGDIR/web_admin.$(date +%F).log" 2>&1 &

NEW_PID=$!
sleep 1

# 프로세스가 정상적으로 실행 중인지 확인
if ps -p $NEW_PID > /dev/null 2>&1; then
    echo "재시작 완료! (PID: $NEW_PID)"
    echo "로그 확인: tail -f $LOGDIR/web_admin.$(date +%F).log"
else
    echo "재시작 실패! 로그를 확인하세요."
    exit 1
fi
