# media-router

미디어 파일을 규칙 기반으로 자동 분류/이동하고, 웹 UI에서 규칙을 관리하는 Python 프로젝트입니다.

## 주요 기능

- 다운로드 폴더의 파일을 패턴 규칙에 따라 카테고리별 폴더로 자동 이동
- 웹 UI에서 규칙 추가/수정/삭제
- 드라마/예능 에피소드 번호 추적
- Telegram 알림 전송 (선택)
- 소유권/권한 자동 적용 (Synology ACL 포함)

---

## 설치

### 1. 설정 파일 준비

\`\`\`bash
mkdir -p /volume1/docker/media-router/logs
cd /volume1/docker/media-router
cp config.example.yaml config.yaml
\`\`\`

\`config.yaml\`을 열어 본인 환경에 맞게 수정합니다.

### 2. docker-compose.yml 작성

\`\`\`yaml
services:
  media-router:
    image: kck9010/media-router:latest
    container_name: media_router
    restart: unless-stopped
    ports:
      - "5080:5080"
    volumes:
      - ./config.yaml:/app/config.yaml
      - ./state.yaml:/app/state.yaml
      - ./status.json:/app/status.json
      - ./logs:/app/logs
\`\`\`

### 3. 실행

\`\`\`bash
docker compose up -d
\`\`\`

웹 관리자: \`http://NAS_IP:5080\`

---

## 자동 분류 설정

\`media_router.py\`는 호스트에서 직접 실행합니다. DSM 작업 스케줄러에 등록하세요.

\`\`\`bash
# venv 초기 설정 (최초 1회)
cd /volume1/docker/media-router
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
\`\`\`

작업 스케줄러 스크립트:

\`\`\`bash
cd /volume1/docker/media-router
source .venv/bin/activate
python -u media_router.py
\`\`\`

---

## 설정 (config.yaml)

\`config.example.yaml\`을 참고하세요. 주요 항목:

| 항목 | 설명 |
|------|------|
| \`telegram\` | 봇 토큰, 채팅 ID |
| \`paths.sources\` | 다운로드 폴더 경로 |
| \`base_paths\` | 카테고리별 대상 폴더 |
| \`rules\` | 분류 규칙 목록 |
| \`ownership\` | 파일 소유권/권한 설정 |

> ⚠️ \`config.yaml\`은 민감정보를 포함하므로 git에 올리지 마세요.

> ⚠️ \`ownership.enforce_inherit\`은 Synology 전용 기능입니다. 일반 Linux에서는 \`false\`로 설정하세요.
