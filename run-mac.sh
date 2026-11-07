#!/usr/bin/env bash
# A2A 브라우저 + 로컬 백엔드 실행. 먼저 ./setup-mac.sh 를 한 번 돌려 두세요. docs/LOCAL_MAC.md
#
#   ./run-mac.sh             엔진(8423)·워커를 띄우고 브라우저를 연다. 브라우저를 닫으면 엔진·워커도 끈다
#   ./run-mac.sh --backend   백엔드만(브라우저는 따로)
# 로그: data/logs/engine.log, data/logs/worker.log
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
[[ -f .env && -x .venv/bin/python && -d browser/node_modules ]] || { echo "먼저 ./setup-mac.sh 를 실행해 주세요"; exit 1; }
export PATH="$(brew --prefix postgresql@17)/bin:$(brew --prefix node@22)/bin:$PATH"
envget() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
PORT="${A2A_ENGINE_PORT:-8423}"

# Postgres·Redis(brew services) — 꺼져 있으면 켠다
for s in postgresql@17 redis; do
  brew services list | awk -v s="$s" '$1==s && $2=="started"{f=1} END{exit !f}' || brew services start "$s" >/dev/null
done
for i in $(seq 1 30); do pg_isready -q -h 127.0.0.1 && break; sleep 1; done

mkdir -p data/logs
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "▸ 엔진이 이미 :$PORT 에서 돌고 있어요 — 그대로 씁니다"
else
  echo "▸ 엔진 :$PORT (로그 data/logs/engine.log)"
  ( set -a; . ./.env; set +a; exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" ) >> data/logs/engine.log 2>&1 &
  PIDS+=($!)
  echo "▸ 워커 (로그 data/logs/worker.log)"
  ( set -a; . ./.env; set +a; exec .venv/bin/python -m app.chat.worker ) >> data/logs/worker.log 2>&1 &
  PIDS+=($!)
  for i in $(seq 1 40); do curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 || { echo "✕ 엔진이 뜨지 않았어요 — data/logs/engine.log 를 봐 주세요"; tail -20 data/logs/engine.log; exit 1; }
fi

if [[ "${1:-}" == "--backend" ]]; then
  echo "▸ 백엔드만 실행 중 — Ctrl+C로 끕니다"
  wait
else
  echo "▸ 브라우저 ($(envget A2A_USER) @ $(envget A2A_TENANT))"
  cd browser
  A2A_USER="$(envget A2A_USER)" A2A_TENANT="$(envget A2A_TENANT)" A2A_ENGINE_URL="http://127.0.0.1:$PORT" npx electron .
fi
