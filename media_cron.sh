#!/bin/bash
# /volume1/web/media-router/media_cron.sh
# venv 사용 시 활성화 후 실행(미사용이면 파이썬 경로만 맞추세요)

BASE="/volume1/web/media-router"
cd "$BASE"

# 가볍게 잠시 대기(동시 실행 방지용 락 파일도 가능)
sleep 1

/usr/bin/python3 "$BASE/media_router.py"
