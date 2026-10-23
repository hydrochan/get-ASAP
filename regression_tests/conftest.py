"""regression_tests 공용 설정: 프로젝트 루트·dashboard를 import 경로에 올리고, 합성 데이터용 임시 DB 픽스처를 제공한다.

실제 data/asap.db, cache/, .env 토큰은 어떤 테스트도 건드리지 않는다(경로·환경변수를 tmp_path로 격리).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "dashboard"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    """초기화된 빈 정본 DB 경로. ASAP_DB_PATH가 이 경로를 가리키고 store.CACHE_DIR은 tmp 아래로 격리된다."""
    import store

    path = tmp_path / "data" / "asap.db"
    monkeypatch.setenv("ASAP_DB_PATH", str(path))
    monkeypatch.setattr(store, "CACHE_DIR", str(tmp_path / "cache"))
    store.init_db(str(path))
    return path


@pytest.fixture
def conn(db_file):
    import store

    c = store.connect(str(db_file))
    yield c
    c.close()
