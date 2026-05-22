#!/bin/bash
set -euo pipefail
LOGDIR="/volume1/docker/media-router/logs"
mkdir -p "$LOGDIR"
exec >>"$LOGDIR/run_all.$(date +%F).log" 2>&1
LOCKFILE="/volume1/docker/media-router/run.lock"
exec 9>"$LOCKFILE"
flock -n 9 || { echo "[run_all $(date '+%H:%M:%S')] 이미 실행중입니다."; exit 0; }

ra_ts() { echo "[run_all $(date '+%H:%M:%S')]"; }

echo "$(ra_ts) RUN START"

# 1. 드라마 이름 변경
/volume1/web/video_auto/drama_name.sh || echo "$(ra_ts) drama_name.sh 실행 중 오류 발생 (계속 진행)"

# 2. happypack
/volume1/web/Python/happypack/happypack.sh || echo "$(ra_ts) happypack.sh 실행 중 오류 발생 (계속 진행)"

# 3. venv + media_router
cd /volume1/docker/media-router
. .venv/bin/activate
CONAN_DIR="/volume1/video/04.애니메이션/04.시리즈/명탐정 코난 (1996)"
ONEPIECE_DIR="/volume1/video/04.애니메이션/04.시리즈/원피스/One Piece Season 23"
conan_before=$(find "$CONAN_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)
onepiece_before=$(find "$ONEPIECE_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)
python -u media_router.py
conan_after=$(find "$CONAN_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)
onepiece_after=$(find "$ONEPIECE_DIR" -maxdepth 1 -type f 2>/dev/null | wc -l)
if [ "$conan_after" -gt "$conan_before" ] || [ "$onepiece_after" -gt "$onepiece_before" ]; then
    echo "$(ra_ts) 코난/원피스 파일 이동 감지 → aniname.sh 실행"
    /volume1/web/Python/aniname/aniname.sh || echo "$(ra_ts) aniname.sh 실행 중 오류 발생 (계속 진행)"
else
    echo "$(ra_ts) 코난/원피스 이동 없음 → aniname.sh 건너뜀"
fi

# 4. bt4g
/volume1/web/Python/bt4g/bt4g.sh || echo "$(ra_ts) bt4g.sh 실행 중 오류 발생 (계속 진행)"

# 5. photo_move
/volume1/web/video_auto/photo_move.sh || echo "$(ra_ts) photo_move.sh 실행 중 오류 발생 (계속 진행)"

# 6. speedtest
/volume1/web/Python/speedtest/speedtest.sh || echo "$(ra_ts) speedtest.sh 실행 중 오류 발생 (계속 진행)"