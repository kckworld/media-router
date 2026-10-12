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
	echo "재시작 후 정상 응답 확인 (HTTP $code2) - 헬스체크가 장애를 감지하고 복구함"
	exit 1
fi

echo "재시작했지만 여전히 응답 없음/이상 (HTTP ${code2:-없음}) - 직접 확인 필요"
exit 91
