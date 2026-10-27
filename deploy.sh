#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ts() { echo "[deploy $(date '+%H:%M:%S')]"; }

# 인터랙티브 쉘의 `alias docker='sudo docker'`는 스크립트 파일 실행 시
# 적용되지 않으므로, 여기서는 sudo를 명시적으로 붙인다.
DC="sudo docker compose"
DOCKER="sudo docker"
# 10-27 한 번은 빌드 뒤 조용히 멈춰 원인을 못 찾았다 → 실패하면 어디서 멈췄는지 남긴다
trap 'echo "$(ts) ❌ 실패: ${BASH_SOURCE[0]}:${LINENO} — ${BASH_COMMAND}" >&2' ERR
CONTAINER="media_router"

# 배포하는 동안 healthcheck.sh(5분마다)가 컨테이너를 restart하지 않게 표시를 남긴다 (2026-10-27).
# 끝나면(실패해도) 지운다. 15분 넘게 남으면 healthcheck가 무시한다.
DEPLOY_FLAG="$SCRIPT_DIR/data/.deploying"
date +%s > "$DEPLOY_FLAG"
chmod 666 "$DEPLOY_FLAG" 2>/dev/null || true
trap 'rm -f "$DEPLOY_FLAG"' EXIT

echo "$(ts) media-router 로컬 빌드 시작 (수 분 걸릴 수 있음)"
$DC build media-router
new_image="$($DOCKER image inspect -f '{{.Id}}' kck9010/media-router:latest)"

# 지난 배포가 꼬여 남은 '<id>_media_router' 임시 컨테이너(Created)가 있으면 compose가 이름 충돌로 실패한다
for c in $($DOCKER ps -a --filter "name=_${CONTAINER}" --filter status=created --format '{{.Names}}'); do
    echo "$(ts) 지난 배포에서 남은 임시 컨테이너 삭제: $c"
    $DOCKER rm "$c" >/dev/null
done

echo "$(ts) 컨테이너 재시작"
$DC up -d media-router

running_image="$($DOCKER inspect -f '{{.Image}}' "$CONTAINER")"
if [ "$running_image" != "$new_image" ]; then
    echo "$(ts) ❌ 실행 중인 컨테이너가 새 이미지가 아닙니다 (실행 ${running_image:7:12}, 새 이미지 ${new_image:7:12})" >&2
    exit 1
fi
for i in $(seq 1 15); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:5080/ || true)"
    case "$code" in 200|302) break ;; esac
    sleep 2
done
echo "$(ts) 배포 완료 (이미지 ${new_image:7:12}, 응답 HTTP ${code:-없음})"
$DC ps media-router

if [ -n "$(git status --short 2>/dev/null)" ]; then
    echo "$(ts) 참고: 커밋 안 된 변경사항이 있습니다 (git은 형상관리 용도이니 잊지 말고 커밋하세요)"
    git status --short
fi
