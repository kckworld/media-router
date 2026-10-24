#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ts() { echo "[deploy $(date '+%H:%M:%S')]"; }

# 인터랙티브 쉘의 `alias docker='sudo docker'`는 스크립트 파일 실행 시
# 적용되지 않으므로, 여기서는 sudo를 명시적으로 붙인다.
DC="sudo docker compose"

echo "$(ts) media-router 로컬 빌드 시작 (수 분 걸릴 수 있음)"
$DC build media-router

echo "$(ts) 컨테이너 재시작"
$DC up -d media-router

echo "$(ts) 배포 완료"
$DC ps media-router

if [ -n "$(git status --short 2>/dev/null)" ]; then
    echo "$(ts) 참고: 커밋 안 된 변경사항이 있습니다 (git은 형상관리 용도이니 잊지 말고 커밋하세요)"
    git status --short
fi
