# web_admin.py 재시작 가이드

## 방법 1: SSH 접속 후 직접 재시작 (권장)

### 1단계: NAS에 SSH 접속
```bash
ssh 사용자명@NAS_IP주소
# 예: ssh admin@192.168.1.100
```

### 2단계: 프로세스 확인 및 종료
```bash
cd /volume1/web/media-router

# 실행 중인 web_admin.py 프로세스 확인
ps aux | grep web_admin.py

# 프로세스 ID(PID) 확인 후 종료
kill PID번호
# 또는 강제 종료
kill -9 PID번호
```

### 3단계: 재시작
```bash
# 가상환경이 있다면 활성화
source .venv/bin/activate

# 백그라운드로 재시작
nohup python -u web_admin.py > logs/web_admin.log 2>&1 &

# 또는 포그라운드로 실행 (종료하려면 Ctrl+C)
python web_admin.py
```

## 방법 2: 스크립트 사용

1. `restart_web_admin.sh` 파일을 NAS에 업로드
2. 실행 권한 부여: `chmod +x restart_web_admin.sh`
3. 실행: `./restart_web_admin.sh`

## 방법 3: systemd 서비스로 실행 중인 경우

서비스 파일이 있다면:
```bash
sudo systemctl restart web_admin.service
# 또는
sudo systemctl restart media-router-web.service
```

서비스 상태 확인:
```bash
sudo systemctl status web_admin.service
```

## 확인 방법

재시작 후 브라우저에서 접속 테스트:
- http://NAS_IP:5080
- 또는 http://localhost:5080 (NAS에서 직접 접속 시)

## 주의사항

- 코드 변경 사항을 적용하려면 반드시 재시작이 필요합니다
- 실행 중인 사용자가 있다면 재시작 전에 안내하는 것이 좋습니다
- 로그 파일은 `logs/web_admin.log` 또는 `logs/` 디렉토리에서 확인할 수 있습니다





