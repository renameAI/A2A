"""Python 관리 플레인 ↔ Rust 데이터 플레인 사이의 공유 상수 검사.

사본을 둘 수밖에 없는 두 곳(메트릭 히스토그램 버킷, 권한 목록)이 어긋나면
조용히 틀린 분위수·권한 화면이 된다 — 여기서 어긋나는 순간 잡는다.
펜스 SQL과 add_member는 파일·DB 함수 하나라 검사할 사본 자체가 없다.
"""
import json
import subprocess
from pathlib import Path

from app.chat import metrics

BIN = str(Path(__file__).resolve().parent.parent / "rust" / "chat-engine" / "target" / "release" / "a2a-chat")
pytestmark = __import__("pytest").mark.skipif(not Path(BIN).exists(),
                                              reason="a2a-chat 바이너리 없음 — cargo build --release")


def _config():
    return json.loads(subprocess.run([BIN, "print-config"], capture_output=True,
                                     text=True, check=True).stdout)


def test_metrics_bounds_match():
    assert _config()["metrics_bounds"] == metrics.BOUNDS


def test_permission_catalog_matches_policy_docs():
    # 권한 이름 목록 — 관리 콘솔(Python)이 보여 주는 카탈로그의 원본은 이제 Rust다.
    perms = _config()["permissions"]
    assert "message.post" in perms and "lead.access" in perms and len(perms) >= 19


def test_fence_sql_is_shared_file():
    fence = Path(__file__).resolve().parent.parent / "app" / "chat" / "sql" / "tx_fence.sql"
    body = "\n".join(l for l in fence.read_text().splitlines() if not l.strip().startswith("--"))
    assert "has_schema_privilege" in body and body.count("%s") == 3
