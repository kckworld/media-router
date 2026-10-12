#!/bin/sh
# media-router 헬스체크 + 자동재시작 (DSM 작업 스케줄러, 5분마다, 사용자=root)
# 실제 로직은 healthcheck.sh, 로깅/텔레그램/Postgres 는 run-logged.sh 가 담당.
exec /volume1/docker/run-logged.sh "media-router-healthcheck" /volume1/docker/media-router/healthcheck.sh
