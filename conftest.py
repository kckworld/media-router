# -*- coding: utf-8 -*-
"""pytest 공통 픽스처.

media_router.py/db.py는 모듈 최상단에서 DB 경로(DATA_DIR)를 계산해두고 쓰기
때문에, 테스트가 실제 운영 DB(data/media_router.db)를 건드리지 않도록 db
모듈의 DATA_DIR/DB_PATH/커넥션 캐시를 테스트 전용 임시 디렉터리로 바꿔치기하는
fresh_db 픽스처를 제공한다.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
import db as db_module


@pytest.fixture
def fresh_db(monkeypatch):
    """규칙/설정을 저장할 SQLite DB를 매 테스트마다 임시 디렉터리에 새로 만든다.

    pytest 기본 tmp_path는 이 NAS에서 TMPDIR이 홈 디렉터리(느린 btrfs 볼륨)로
    잡혀있어서, db.py에 이미 적혀있는 그 "WAL 커넥션을 닫을 때 체크포인트가
    fsync로 1~10초씩 걸린다"는 문제를 테스트에서도 그대로 겪는다(DB 관련
    테스트 하나당 4~8초, 전체 스위트가 90초 이상). 테스트에는 내구성이 필요
    없으므로 항상 /tmp(tmpfs)를 직접 지정해 그 비용을 피한다."""
    with tempfile.TemporaryDirectory(dir="/tmp") as tmp_dir:
        tmp_path = Path(tmp_dir)
        monkeypatch.setattr(db_module, "DATA_DIR", tmp_path)
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "media_router.db")
        db_module._connection = None
        db_module._initialized = False
        yield db_module
        if db_module._connection is not None:
            db_module._connection.close()
        db_module._connection = None
        db_module._initialized = False
