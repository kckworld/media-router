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
      - /volume1/video:/volume1/video:ro
    environment:
      - MEDIA_ADMIN_PASSWORD=${MEDIA_ADMIN_PASSWORD:-}
      - TMDB_API_KEY=${TMDB_API_KEY:-}
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

- 날짜가 바뀐 뒤 첫 실행에서 해당 요일의 `updated_map`을 자동으로 `N`으로 리셋합니다(캐치업). 재부팅/스케줄러 장애 등으로 자정 시간대를 놓쳐도, 그날 배치가 처음 도는 시점에 지연 리셋되며 같은 날 중복 리셋되지 않습니다.
- 파일 이동 성공 시 해당 요일의 `updated_map`을 `Y`로 마킹합니다.
- 에피소드 점검 기능을 사용하려면 컨테이너에서 대상 경로를 읽을 수 있어야 하므로 `/volume1/video` 마운트가 필요합니다.
- 드라마 추가 시 전체 에피소드 수를 비워두면 `TMDB_API_KEY`가 설정된 경우 TMDB에서 자동 조회해 채웁니다.

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

---

## 테스트

`find_matches`(패턴 AND/OR/exclude 조합), `extract_episode_number`(에피소드 번호 추출),
`reset_updated_for_today`(캐치업 리셋), `db.update_rule_fields`(레이스 컨디션 방지)에 대한
pytest 회귀 테스트가 있습니다. 실제 운영 DB는 건드리지 않고 임시 SQLite DB에서 동작합니다.

```bash
pip install -r requirements-dev.txt
pytest -q
```

> ⚠️ `ownership.enforce_inherit`은 Synology 전용 기능입니다. 일반 Linux에서는 `false`로 설정하세요.