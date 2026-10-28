#!/bin/sh
# media-router 웹(5080) 응답 확인, 멈춰있으면 컨테이너 재시작.
# run-logged.sh 가 이 스크립트를 감싸서 로깅/Postgres/텔레그램 알림을 처리한다.
#
# 배경: 2026-10-11 밤 일시적 DNS 장애 때 텔레그램 리스너 스레드가 멈추면서
# gunicorn이 어떤 요청에도 응답을 안 하는 상태가 됐는데, 컨테이너 프로세스
# 자체는 살아있어서 `docker ps`만 봐서는 멈춘 걸 알 수 없었음. 그래서
# "컨테이너가 떠있는지"가 아니라 "실제로 HTTP 응답을 하는지"를 직접 확인.

CONTAINER="media_router"
URL="http://localhost:5080/"
DOCKER="/usr/local/bin/docker"
[ "$(id -u)" -eq 0 ] || DOCKER="sudo $DOCKER"

# 배포 중이면 건너뜀 (2026-10-27): deploy.sh가 컨테이너를 바꾸는 사이 응답이 없다고 옛 컨테이너를
# restart해서, compose가 "cannot remove a running container"로 실패하고 옛 이미지가 다시 뜬 적이
# 두 번 있었다. deploy.sh가 이 파일에 시작 시각을 적고 끝나면 지운다. 15분 넘게 남아 있으면
# 배포가 비정상 종료된 것으로 보고 무시한다(헬스체크가 영영 멈추지 않게).
DEPLOY_FLAG="$(dirname "$0")/data/.deploying"
DEPLOY_MAX_SEC=900
if [ -f "$DEPLOY_FLAG" ]; then
	started="$(cat "$DEPLOY_FLAG" 2>/dev/null)"
	age=$(( $(date +%s) - ${started:-0} ))
	if [ "$age" -ge 0 ] && [ "$age" -lt "$DEPLOY_MAX_SEC" ]; then
		echo "배포 중이라 건너뜀 (${age}초 전 시작)"
		exit 0
	fi
	echo "배포 표시가 ${age}초째 남아 있음 - 비정상 종료로 보고 무시하고 확인"
fi

is_ok() {
	# 로그인 전이라 302(로그인 리다이렉트)가 정상 응답.
	case "$1" in
		200|302) return 0 ;;
		*) return 1 ;;
	esac
}

code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL" 2>&1)"

if is_ok "$code"; then
	echo "정상 응답 (HTTP $code)"
	exit 0
fi

echo "응답 없음/이상(HTTP ${code:-없음}) - 컨테이너 재시작 시도"
$DOCKER restart "$CONTAINER" || exit $?

sleep 8
running="$($DOCKER inspect -f '{{.State.Running}}' "$CONTAINER" 2>&1)"
if [ "$running" != "true" ]; then
	echo "재시작 명령은 성공했으나 컨테이너가 running 상태가 아님: State.Running=$running"
	exit 90
fi

code2="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$URL" 2>&1)"
if is_ok "$code2"; then
	# 복구 성공은 실패가 아니다: exit 0 + RECOVERED 표시(로그·DB에만, 알림 없음)
	echo "RECOVERED: 재시작 후 정상 응답 확인 (HTTP $code2) - 헬스체크가 장애를 감지하고 복구함"
	exit 0
fi

echo "재시작했지만 여전히 응답 없음/이상 (HTTP ${code2:-없음}) - 직접 확인 필요"
exit 91
