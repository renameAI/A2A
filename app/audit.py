"""감사 로그 (SYS-04) — 판단 출력을 JSONL로 저장 (HITL 검토·재학습용).

날짜별 파일에 append. 감사 기록 실패가 본 판단을 막지 않는다(best-effort).
"""
import json
import os
import time
from pathlib import Path


def _audit_dir() -> Path:
    override = os.environ.get("A2A_AUDIT_DIR")
    return Path(override) if override else \
        Path(__file__).resolve().parent.parent / "audit"


def record(kind: str, payload: dict) -> None:
    try:
        directory = _audit_dir()
        directory.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **payload}
        path = directory / f"{time.strftime('%Y%m%d')}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
