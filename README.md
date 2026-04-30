# media-router

미디어 파일을 규칙 기반으로 자동 분류/이동하고, 웹 UI에서 규칙을 관리하는 Python 프로젝트입니다.

- 자동 분류 실행기: `media_router.py`
- 웹 관리자: `web_admin.py` (Flask)
- 운영 스크립트: `run_all.sh`, `media_cron.sh`, `restart_web_admin.sh`

## 주요 기능

- 다운로드 폴더를 재귀 탐색해 패턴(`fnmatch`)에 맞는 파일을 카테고리별 폴더로 이동
- 카테고리/패턴/서브폴더/요일 기반 규칙 관리
- 중복 파일명 자동 회피(`name_1.ext`)
- 소유권/권한(chown/chmod) 및 Synology ACL 상속(enforce-inherit) 적용
- Telegram 알림 전송(선택)
- 웹 UI에서 규칙 추가/수정/삭제, 체크 모드 관리
- 드라마/예능 에피소드 번호 추적 보조

## 요구 사항

- Python 3.8+
- Linux/NAS 환경(Synology 포함) 권장
- Bash 실행 환경(운영 스크립트 사용 시)

의존성은 `requirements.txt`:

- Flask==3.0.0
- PyYAML==6.0.2
- requests==2.32.3

## 설치

```bash
cd /volume1/web/media-router
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 설정

핵심 설정 파일은 `config.yaml` 입니다.

### 1) telegram

```yaml
telegram:
  bot_token: "<YOUR_BOT_TOKEN>"
  chat_id: "<YOUR_CHAT_ID>"
  enabled: true
```

### 2) paths.sources / cleanup

```yaml
paths:
  sources:
    - /volume2/torrent/download
    - /volume1/video/임시/download
  cleanup:
    remove_dirs:
      - "@eaDir"
    remove_files:
      - thumbs.db
      - Thumbs.db
```

### 3) base_paths

```yaml
base_paths:
  예능: /volume1/video/03.예능
  드라마: /volume1/video/01.드라마
  다큐: /volume1/video/07.다큐
  애니메이션: /volume1/video/04.애니메이션
```

### 4) rules

```yaml
rules:
  - category: 예능
    pattern: "*런닝맨*.*"
    subfolder: 런닝맨
    days: [일]
    updated: N
    updated_map:
      일: N
    last_episode: 785
```

필드 설명:

- `category`: `base_paths`의 키와 매칭
- `pattern`: 와일드카드 패턴(와일드카드가 없으면 내부에서 자동 정규화)
- `subfolder`: 최종 저장 하위 폴더
- `days`: 방송 요일
- `updated_map`: 요일별 처리 상태(`Y`/`N`)
- `last_episode`: 예능의 마지막 확인 회차
- `total_episodes`, `received_episodes`: 드라마 회차 관리

### 5) ownership

```yaml
ownership:
  apply: true
  user: plex
  group: users
  file_mode: 0o664
  dir_mode: 0o775
  setgid_dirs: true
  enforce_inherit: true
```

- `apply: true`일 때 파일 이동 후 권한/소유권을 맞춥니다.
- Synology에서 `synoacltool`이 있으면 ACL 상속 적용을 시도합니다.

## 실행 방법

### 단독 실행

```bash
cd /volume1/web/media-router
source .venv/bin/activate
python -u media_router.py
```

로그: `logs/media_router.log`

### 통합 실행(run_all)

```bash
cd /volume1/web/media-router
chmod +x run_all.sh
./run_all.sh
```

`run_all.sh` 동작:

1. `drama_name.sh` 실행
2. `happypack.sh` 실행
3. `media_router.py` 실행
4. 코난/원피스 이동 감지 시 `aniname.sh` 실행

특징:

- 락 파일(`/tmp/media-router.run.lock`)로 중복 실행 방지
- 실행 로그: `logs/run_all.YYYY-MM-DD.log`

### 웹 관리자 실행

```bash
cd /volume1/web/media-router
source .venv/bin/activate
python web_admin.py
```

- 기본 주소: `http://127.0.0.1:5080`
- 헬스체크: `GET /health`

## 웹 관리자 기능

- 체크 모드: 최근 요일 기준 미처리 항목 확인 및 체크
- 전체 관리 모드: 규칙 필터/정렬(요일, 카테고리, 패턴, 서브폴더)
- 규칙 추가/수정/삭제
- 드라마/예능 에피소드 스캔(`check_episodes`)
- 웹 관리자 재시작 API(`POST /restart`)

참고: 현재 코드에서 `authed()`는 항상 `True`를 반환하므로 실질 인증이 비활성화되어 있습니다.

## 크론/운영

### 크론 예시

```cron
*/30 * * * * /volume1/web/media-router/media_cron.sh
```

`media_cron.sh`는 내부에서 `media_router.py`를 호출합니다.

### 웹 관리자 재시작

```bash
cd /volume1/web/media-router
chmod +x restart_web_admin.sh
./restart_web_admin.sh
```

재시작 로그: `logs/restart_web_admin.YYYY-MM-DD.log`

추가 가이드는 `RESTART_GUIDE.md` 참고.

## 파일 구조

```text
media-router/
  media_router.py
  web_admin.py
  config.yaml
  requirements.txt
  run_all.sh
  media_cron.sh
  restart_web_admin.sh
  RESTART_GUIDE.md
  templates/
    index.html
    edit.html
  static/
  logs/
  state.yaml
  status.json
```

## 트러블슈팅

- `pip install -r requirements.txt 필요` 메시지 발생
  - 가상환경 활성화 후 `pip install -r requirements.txt` 재실행
- 권한 오류(chown/chmod 실패)
  - 실행 사용자 권한 확인, `ownership.apply`를 임시로 `false`로 설정 후 원인 파악
- 소스 경로/대상 경로가 없어서 이동이 안 됨
  - `config.yaml`의 `paths.sources`, `base_paths` 경로 존재 여부 확인
- 웹 UI 수정사항이 반영되지 않음
  - `restart_web_admin.sh`로 프로세스 재시작

## 보안 주의

- `config.yaml`의 Telegram 토큰/채팅 ID는 민감정보입니다.
- 저장소에 커밋하지 말고, 이미 노출된 토큰은 즉시 재발급(폐기 후 생성)하세요.
- 필요 시 `MEDIA_ADMIN_PASSWORD` 환경 변수를 사용해 인증을 강화하되, 현재 `authed()` 구현도 함께 수정해야 실제 보호가 됩니다.
