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
- 드라마에 **하루 편수**(`episodes_per_day`)를 2 이상으로 지정하면, 한 방영일에 그 편수만큼 실제로 받아야 그 요일이 `Y`로 체크됩니다(요일별 카운터 `day_progress`). 신병처럼 수/목 각 2편씩 올라오는 드라마가 수요일 2편만 받았을 때 목요일까지 체크로 넘어가던 문제를 막습니다. 카운터는 주간 리셋 때 해당 요일만 0으로 돌아갑니다.
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

## 핫딜 (생활 > 핫딜, 2026-11-13)

구독 중인 텔레그램 핫딜 채널 메시지를 **내 텔레그램 계정**(Telethon)으로 읽어 한 양식(상품명·가격·쇼핑몰·바로가기·원문)으로 모으고 검색한다. 텔레그램에는 **읽기만** 한다(쓰기·반응 없음). 소유자만 볼 수 있다.

- **바로가기(홈 화면 앱)**: 핫딜 화면의 [📱 핫딜 앱 바로가기] → 열린 화면에서 "홈 화면에 추가". 주소 `/hotdeal?app=1`, `static/hotdeal.webmanifest` (메모장과 같은 방식)
- **수집**: MR 안 주기 작업이 5분마다 켜 둔 채널의 새 메시지만 가져온다(채널별 `last_msg_id` 이후, 처음은 최근 7일). FloodWait이 나면 그 채널만 다음으로 미룬다. 마지막 수집 시각·새 딜 수·채널별 오류는 핫딜 > 채널 관리에 보인다. 끄려면 컨테이너 환경변수 `HOTDEAL_LOOP=0`
- **파싱**: 정규식·휴리스틱만(첫 줄=제목, `[\d,]+원` 중 가장 싼 값=가격, 메시지 링크·숨은 텍스트 링크·버튼 링크 중 첫 상품 링크, 도메인으로 쇼핑몰). 잘 안 맞는 채널은 `hotdeal.py`의 `CHANNEL_PARSERS`에 전용 함수를 추가. 파싱이 안 돼도 원문은 저장(원문 검색 가능)
- **저장**: `data/hotdeal/hotdeal.db`(MR 본 DB와 분리) — `hotdeal_channels`, `hotdeals`, `hotdeals_fts`(FTS5 trigram, 3글자 이상. 2글자 이하는 LIKE). 같은 상품(트래킹 파라미터 뺀 URL, 쿠팡 productId·네이버 products 번호)이 48시간 안에 또 오면 중복 표시. 보관 기간(기본 90일)이 지나면 자동 삭제
- **알림(기본 꺼짐)**: 핫딜 > 채널 관리 > 알림·보관에서 "키워드 알림"(키워드가 들어간 새 딜만)·"새 딜 전체 피드"를 켠다. 보내는 곳은 설정 > 알림의 `핫딜 키워드 알림`·`핫딜 전체 피드`

### 처음 설정 / 다시 로그인
1. my.telegram.org → API development tools에서 API ID·HASH 발급
2. `.env`에 `TG_API_ID=…`, `TG_API_HASH=…`를 넣고 `./deploy.sh` (또는 핫딜 > 채널 관리 화면에서 직접 입력 → `data/hotdeal/api.json`, 600)
3. 핫딜 > 채널 관리 > 텔레그램 로그인: 전화번호(+82…) → 텔레그램 앱으로 온 인증코드 → (2단계 인증이면) 비밀번호
   - 예비(터미널): `sudo /usr/local/bin/docker exec -it media_router python hotdeal_login.py`
4. 로그아웃·다른 계정으로 바꾸기: 같은 화면 [로그아웃] 후 3번 다시

### 채널 추가
핫딜 > 채널 관리 > [내 구독 채널에서 추가] → 원하는 채널 [추가]. 채널마다 켜기/끄기·표시 이름 바꾸기·빼기(모은 딜도 지움)가 된다.
터미널로 목록만 보기: `sudo /usr/local/bin/docker exec -it media_router python hotdeal_login.py --list`

> ⚠️ `data/hotdeal/tg.session`에는 텔레그램 계정 전체 권한이 들어 있다. 권한 600, `data/`는 git·이미지에서 빠져 있고, 웹·로그에 내보내지 않는다. 의심되면 텔레그램 앱 설정 > 기기에서 "MR hotdeal" 세션을 끊는다.

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