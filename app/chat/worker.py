"""배경 작업자 — 셀 이동 대기열 처리 + 작업자를 잃은 이동 복구.

  python -m app.chat.worker            # 계속 돈다(5초 간격)
  python -m app.chat.worker --once     # 한 번만(크론·시험)

서버리스(Vercel 등)는 응답 뒤의 배경 실행을 보장하지 않고 함수 시간 제한(수 분)이 있어
큰 테넌트 이동을 끝까지 못 돈다. 그래서 이동은 이 상주 작업자(Cloud Run Job·compose
서비스)가 맡는다. 여러 개를 띄워도 claim이 SKIP LOCKED라 하나의 이동은 하나만 잡는다.
"""
import argparse
import logging
import os
import socket
import time

from . import move, provision

log = logging.getLogger("chat.worker")


def tick(worker: str) -> dict:
    recovered = move.recover_stale()
    # 스키마 이전 — 대상이 없으면 질의 한 번. 배포가 새 tenant_vN.sql을 실으면 여기서 올라간다
    migrated = provision.migrate_all()
    ran = move.run_pending(worker, limit=1)
    return {"recovered": recovered, "migrated": migrated, "ran": ran}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=float(os.environ.get("CHAT_WORKER_INTERVAL", "5")))
    a = ap.parse_args()
    worker = f"{socket.gethostname()}:{os.getpid()}"
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            r = tick(worker)
            if r["recovered"] or r["migrated"] or r["ran"]:
                log.info("tick %s", r)
        except Exception as e:  # noqa: BLE001 — 작업자는 죽지 않고 다음 주기에 다시 한다
            log.warning("tick failed: %s", e)
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
