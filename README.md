# media-router

미디어 파일을 규칙 기반으로 자동 분류/이동하고, 웹 UI에서 규칙을 관리하는 Python 프로젝트입니다.

## 주요 기능

- 다운로드 폴더의 파일을 패턴 규칙에 따라 카테고리별 폴더로 자동 이동
- 웹 UI에서 규칙 추가/수정/삭제 (체크모드 / 전체관리 모드)
- 드라마/예능 에피소드 번호 추적
- Telegram 알림 전송 (선택)
- 소유권/권한 자동 적용 (Synology ACL 포함)
- GitHub Actions → Docker Hub → NAS 자동 배포

---

## 설치 (Synology NAS)

### 1. 데이터 디렉토리 생성

```bash
mkdir -p /volume1/docker/media-router/data
```

### 2. docker-compose.yml 작성

```yaml
services:
  media-router:
    image: kck9010/media-router:latest
    container_name: media_router
    restart: unless-stopped
    ports:
      - "5080:5080"
    volumes:
      - ./data:/app/data
    environment:
      - MEDIA_ADMIN_PASSWORD=${MEDIA_ADMIN_PASSWORD:-}
```

### 3. 실행

```bash
cd /volume1/docker/media-router
docker compose up -d
```

웹 관리자: `http://NAS_IP:5080`

---

## 자동 분류 설정

`media_router.py`는 컨테이너 내부에서 직접 실행합니다. DSM 작업 스케줄러에 등록하세요.

작업 스케줄러 스크립트:

```bash
sudo /usr/local/bin/docker exec media_router python media_router.py
```

- 매일 자정~1시 사이 실행 시 해당 요일의 `updated_map`을 자동으로 `N`으로 리셋합니다.
- 파일 이동 성공 시 해당 요일의 `updated_map`을 `Y`로 마킹합니다.

---

## 데이터 저장

모든 설정과 규칙은 SQLite DB(`data/media_router.db`)에 저장됩니다.

| 경로 | 설명 |
|------|------|
| `data/media_router.db` | 규칙 및 설정 DB |
| `data/logs/` | 실행 로그 |

설정 항목(`config.example.yaml` 참고):

| 항목 | 설명 |
|------|------|
| `telegram` | 봇 토큰, 채팅 ID |
| `paths.sources` | 다운로드 폴더 경로 |
| `base_paths` | 카테고리별 대상 폴더 |
| `ownership` | 파일 소유권/권한 설정 |

> ⚠️ `ownership.enforce_inherit`은 Synology 전용 기능입니다. 일반 Linux에서는 `false`로 설정하세요.