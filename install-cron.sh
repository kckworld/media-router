#!/bin/sh
# media-router 헬스체크 cron 엔트리를 /etc/crontab 에 등록한다 (5분마다).
# 사용법:  sudo sh /volume1/docker/media-router/install-cron.sh
# (root 권한 필요 - /etc/crontab 수정 및 crond 재시작)

set -e

CRONTAB="/etc/crontab"
MARKER="healthcheck-cron.sh"
# 필드: 분 시 일 월 요일 사용자 명령  (반드시 TAB 구분)
ENTRY="$(printf '*/5\t*\t*\t*\t*\troot\t/volume1/docker/media-router/healthcheck-cron.sh')"

if [ "$(id -u)" -ne 0 ]; then
	echo "root 권한이 필요합니다:  sudo sh $0" >&2
	exit 1
fi

if grep -q "$MARKER" "$CRONTAB"; then
	echo "이미 등록되어 있습니다. 현재 엔트리:"
	grep "$MARKER" "$CRONTAB"
	exit 0
fi

cp -a "$CRONTAB" "$CRONTAB.bak.$(date +%Y%m%d%H%M%S)"
printf '%s\n' "$ENTRY" >> "$CRONTAB"
echo "추가됨:"
grep "$MARKER" "$CRONTAB"

systemctl restart crond
echo "crond 재시작 완료."
