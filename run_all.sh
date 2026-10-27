#!/bin/bash
set -euo pipefail
LOGDIR="/volume1/docker/media-router/logs"
mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/run_all.$(date +%F).log"
# 이 스크립트는 서로 다른 계정으로 실행된다: DSM 예약작업은 root로, 웹 관리자의
# "배치 실행" 버튼은 SSH 강제 명령을 통해 kck9010으로 실행한다. 로그 파일은 그날
# 처음 실행한 쪽의 계정 소유로 생성되는데, 기본 644 권한이면 소유자가 아닌 쪽은
# append(exec >>)할 때 Permission denied로 죽어서 로그 한 줄도 안 남고 종료된다.
# root가 만들면 그룹도 root가 되어서 kck9010은 664로도(그룹에 못 속해서) 못 쓴다
# — 그래서 그룹이 아니라 "기타(other)" 쓰기 비트까지 열어주는 666이 필요하다.
# 매 실행마다 이 권한을 보장해서 어느 쪽이 먼저 만들었든 둘 다 이어서 쓸 수 있게
# 한다(chmod는 파일 소유자 또는 root만 가능하므로, 소유자가 아닌 쪽이 실행되는
# 시점엔 이 chmod 자체도 실패하지만, 그다음 소유자 쪽 실행에서 다시 666으로
# 고쳐지면서 그 이후로는 정상화된다).
touch "$LOGFILE" 2>/dev/null || true
chmod 666 "$LOGFILE" 2>/dev/null || true
exec >>"$LOGFILE" 2>&1
ra_ts() { echo "[run_all $(date '+%H:%M:%S')]"; }

# 웹·텔레그램의 재인코딩 [비우기] (2026-10-27): Drive 파일을 지우는 일이라 컨테이너가 아니라 여기(호스트)서.
# 같은 SSH 키로 'reencode-clear'를 보내면 배치는 돌리지 않고 비우기만 한다. 배치 잠금보다 앞이라
# 배치가 도는 중에도 바로 처리된다 (reencode.py가 자기 잠금으로 run과 겹치지 않게 함).
# 'reencode-trash-emptied'(Drive 휴지통 비웠음 기록, 볼륨 여유 공간을 재야 해서 호스트)도 같은 방식.
case "${SSH_ORIGINAL_COMMAND:-}" in reencode-clear|reencode-trash-emptied)
    re_cmd="${SSH_ORIGINAL_COMMAND#reencode-}"
    echo "$(ra_ts) 재인코딩 $re_cmd 요청 (웹·텔레그램)"
    # reencode.py run이 도는 중이면 clear는 바로 끝나 버리므로(잠금) 최대 10분 다시 시도
    for i in $(seq 1 20); do
        out="$(python3 /volume1/docker/reencode/reencode.py "$re_cmd" 2>&1)" || echo "$(ra_ts) reencode.py $re_cmd 실패"
        echo "$out"
        case "$out" in *"다른 실행이 진행 중"*) sleep 30 ;; *) break ;; esac
    done
    exit 0 ;;
esac

LOCKFILE="/volume1/docker/media-router/run.lock"
exec 9>"$LOCKFILE"
flock -n 9 || { echo "$(ra_ts) 이미 실행중입니다."; exit 0; }

# 30일 지난 로그 자동 정리 (run_all/generate_schedule_image/hwasung_alert 등
# logs/ 아래 쌓이는 모든 *.log 대상). 삭제 없이는 무기한 누적되던 문제 방지.
find "$LOGDIR" -maxdepth 1 -name "*.log" -mtime +30 -delete 2>/dev/null || true

# 트리거 소스 구분: 웹 관리자의 "배치 실행" 버튼은 SSH 강제 명령으로
# run_all.sh를 실행시키면서 클라이언트 커맨드로 "web-trigger"를 보낸다.
# authorized_keys의 command=가 실제 실행 명령은 대체하지만 sshd가 클라이언트가
# 원래 요청한 명령을 SSH_ORIGINAL_COMMAND 환경변수로 넘겨주므로, 이 값으로
# 로그에서 웹 수동 실행과 DSM 예약 실행(cron)을 구분할 수 있다.
TRIGGER_SOURCE="${SSH_ORIGINAL_COMMAND:-cron}"
RUN_START_EPOCH=$(date +%s)


# 실행 종료 시(정상/오류 모두) 소요 시간과 종료 상태를 반드시 한 줄 남긴다.
# 이게 없으면 로그만 보고는 "지금 실행 중인지 / 중간에 죽었는지"를 알 수 없었다.
finish() {
    local status=$?
    local elapsed=$(( $(date +%s) - RUN_START_EPOCH ))
    if [ "$status" -eq 0 ]; then
        echo "$(ra_ts) RUN END (소요 ${elapsed}s, 트리거: ${TRIGGER_SOURCE})"
    else
        echo "$(ra_ts) RUN END - 오류 종료 (exit=${status}, 소요 ${elapsed}s, 트리거: ${TRIGGER_SOURCE})"
    fi
}
trap finish EXIT

echo "$(ra_ts) RUN START (트리거: ${TRIGGER_SOURCE})"

# 자동 정리 일시정지 (2026-10-19): 웹 '배치' 화면 스위치가 이 파일을 만든다/지운다.
# 켜져 있으면 이름 변환(drama_name)·이동(media_router)·애니 이름 정리(aniname)를 건너뛰어
# 받은 파일이 Drive/다운로드 폴더에 원래 이름 그대로 남는다 (여러 릴 인코딩 비교용).
PAUSE_FLAG="/volume1/docker/media-router/data/processing_paused.json"
PROCESSING_PAUSED=0
if [ -f "$PAUSE_FLAG" ] && grep -q '"paused": *true' "$PAUSE_FLAG" 2>/dev/null; then
    PROCESSING_PAUSED=1
    echo "$(ra_ts) 자동 정리 일시정지 중 → 이름 변환(drama_name)·이동(media_router)·aniname 건너뜀"
fi

# 0. qBittorrent DHT 감시 (2026-10-27): VPN 재연결 뒤 UDP가 막혀 DHT 0이면 qbittorrent만 재시작.
#    평소엔 조용히 끝나고, 재시작하면 run-logged가 텔레그램 알림 + 기록을 남긴다.
/volume1/docker/run-logged.sh qbit-dht-watchdog sh /volume1/docker/qbit-dht-watchdog.sh || true

# 1. 드라마 이름 변경
if [ "$PROCESSING_PAUSED" -eq 0 ]; then
    /volume1/web/video_auto/drama_name.sh || echo "$(ra_ts) drama_name.sh 실행 중 오류 발생 (계속 진행)"
fi

# 1.5 예능 일괄 재인코딩 (2026-10-27): 인코딩 끝난 것 회수·교체 + Drive/인코딩에 다음 묶음 넣기
#     (/volume1/docker/reencode/reencode.py, 미디어 정리 세션). drama_name은 [재인코딩] 파일을 건너뜀
/volume1/docker/run-logged.sh reencode python3 /volume1/docker/reencode/reencode.py run || true

# 2. happypack
/volume1/web/Python/happypack/happypack.sh || echo "$(ra_ts) happypack.sh 실행 중 오류 발생 (계속 진행)"

# 3. venv + media_router
cd /volume1/docker/media-router
. .venv/bin/activate
# 3-0. 텔레그램 "영화 베스트"로 고른 영화: 다 받아졌으면 "제목 (연도)."를 붙여 영화 라이브러리로 (2026-10-26)
python -u movie_mover.py || echo "$(ra_ts) movie_mover.py 실행 중 오류 발생 (계속 진행)"
CONAN_DIR="/volume1/video/04.애니메이션/04.시리즈/명탐정 코난 (1996)"
ONEPIECE_DIR="/volume1/video/04.애니메이션/04.시리즈/원피스/One Piece Season 23"
if [ "$PROCESSING_PAUSED" -eq 0 ]; then
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
fi
# 3-1. 코난/원피스 자동 한국어 자막: 새 회차에 한국어 자막이 없으면 영상 속 영어 자막을 DeepL로 번역해
#      "<영상이름>.ko.ass"로 둔다. 팬 자막이 들어오면 자동 자막을 지우고 교체 (2026-10-19, auto_subtitles.py)
#      aniname 다음에 돌려야 최종 파일명 기준으로 만든다.
#      먼작귀(ToonsHub, 영어 SRT → .ko.srt)도 같이 (2026-10-26)
CHIIKAWA_DIR="/volume1/video/04.애니메이션/04.시리즈/먼작귀 (2022)/Season 1"
python -u auto_subtitles.py "$CONAN_DIR" "$ONEPIECE_DIR" "$CHIIKAWA_DIR" || echo "$(ra_ts) auto_subtitles.py 실행 중 오류 발생 (계속 진행)"

# 4. bt4g
/volume1/web/Python/bt4g/bt4g.sh || echo "$(ra_ts) bt4g.sh 실행 중 오류 발생 (계속 진행)"

# 5. photo_move
photo_move_output="$(/volume1/web/video_auto/photo_move.sh 2>&1)" || echo "$(ra_ts) photo_move.sh 실행 중 오류 발생 (계속 진행)"
[ -n "$photo_move_output" ] && echo "$photo_move_output"

# 5b. photo_move가 실제로 새 파일을 복사했으면(copied=0이 아니면) immich 라이브러리
# 스캔을 트리거한다. immich는 기본 6시간 주기 스캔이라, 트리거 안 하면 새 사진이
# 최대 6시간 늦게 보인다.
if echo "$photo_move_output" | grep -q "copied=[1-9]"; then
    IMMICH_API_KEY=""
    IMMICH_LIBRARY_ID=""
    IMMICH_BASE_URL=""
    # shellcheck disable=SC1091
    [ -f /volume1/docker/media-router/immich.conf ] && . /volume1/docker/media-router/immich.conf
    if [ -n "$IMMICH_API_KEY" ] && [ -n "$IMMICH_LIBRARY_ID" ]; then
        scan_http_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
            "${IMMICH_BASE_URL}/api/libraries/${IMMICH_LIBRARY_ID}/scan" \
            -H "x-api-key: ${IMMICH_API_KEY}" -H "Content-Type: application/json" -d '{}' || echo "curl_failed")
        echo "$(ra_ts) immich 라이브러리 스캔 트리거 http=${scan_http_code}"
    else
        echo "$(ra_ts) WARN: immich.conf 없음/키 없음 - 스캔 트리거 생략"
    fi
fi

# 6. speedtest
/volume1/web/Python/speedtest/speedtest.sh || echo "$(ra_ts) speedtest.sh 실행 중 오류 발생 (계속 진행)"