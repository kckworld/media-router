#!/bin/bash
set -euo pipefail

LOGDIR="/volume1/web/media-router/logs"
mkdir -p "$LOGDIR"

# 모든 출력 로그 파일로 수집
exec >>"$LOGDIR/run_all.$(date +%F).log" 2>&1

# 동시 실행 방지
LOCKFILE="/tmp/media-router.run.lock"
exec 9>"$LOCKFILE"
flock -n 9 || { echo "이미 실행중입니다."; exit 0; }

echo "=== RUN $(date '+%F %T') ==="

# 1. 드라마 이름 변경
/volume1/web/video_auto/drama_name.sh

# 2. happypack (venv 환경에서 실행)
/volume1/web/Python/happypack/happypack.sh || echo "happypack.sh 실행 중 오류 발생 (계속 진행)"

# 3. venv + media_router
cd /volume1/web/media-router
. .venv/bin/activate
python -V

CONAN_DIR="/volume1/video/04.애니메이션/04.시리즈/명탐정 코난 (1996)"
ONEPIECE_DIR="/volume1/video/04.애니메이션/04.시리즈/원피스/One Piece Season 23"
conan_before=$(find "$CONAN_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)
onepiece_before=$(find "$ONEPIECE_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)

python -u media_router.py   # -u: 버퍼링 없이 로그 플러시

conan_after=$(find "$CONAN_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)
onepiece_after=$(find "$ONEPIECE_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)

if [ "$conan_after" -gt "$conan_before" ] || [ "$onepiece_after" -gt "$onepiece_before" ]; then
    echo "코난/원피스 파일 이동 감지 → aniname.sh 실행"
    /volume1/web/Python/aniname/aniname.sh || echo "aniname.sh 실행 중 오류 발생 (계속 진행)"
else
    echo "코난/원피스 이동 없음 → aniname.sh 건너뜀"
fi

