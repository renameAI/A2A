"""구조화 로깅 — Cloud Logging이 파싱할 수 있는 JSON 한 줄.

배경(감사 확정 high): 애플리케이션 로그가 stdout으로 한 줄도 나가지 않아,
프로덕션에서 "검색이 이상해요" 신고가 오면 운영자가 볼 수 있는 것이 없었다.
job 내부 progress.log()는 폴링 응답에만 실려 사용자 화면에서 사라지면 함께
사라졌다.

Cloud Run은 stdout의 JSON을 구조화 로그로 인식한다. 규약대로 `severity`와
`message`를 쓰면 로그 탐색기에서 심각도 필터·필드 검색이 그대로 된다.
"""
import json
import logging
import os
import sys

# Cloud Logging severity 매핑 (Python 레벨명과 다르다)
_SEVERITY = {
    "DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARNING",
    "ERROR": "ERROR", "CRITICAL": "CRITICAL",
}

# LogRecord의 표준 속성 — extra로 들어온 사용자 필드만 골라내기 위한 제외 목록
_STD = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "severity": _SEVERITY.get(record.levelname, "DEFAULT"),
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            # 트레이스백은 message에 이어 붙인다 — Cloud Logging이 에러 리포팅으로
            # 묶으려면 stack_trace가 message 안에 있어야 한다.
            out["message"] += "\n" + self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                try:
                    json.dumps(v)
                    out[k] = v
                except (TypeError, ValueError):
                    out[k] = str(v)
        return json.dumps(out, ensure_ascii=False)


def setup() -> None:
    """루트 로거를 JSON 한 줄 출력으로 세운다. 프로세스당 한 번.

    LOG_FORMAT=text 면 사람이 읽는 형식으로 둔다(로컬 개발).
    """
    root = logging.getLogger()
    if getattr(root, "_a2a_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    if os.environ.get("LOG_FORMAT", "json").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s — %(message)s"))
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    root._a2a_configured = True   # type: ignore[attr-defined]
