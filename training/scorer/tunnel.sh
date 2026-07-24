#!/bin/bash
# E9 스코어러 서빙 SSH 터널 — autossh로 자동 재연결 (방화벽이 22만 허용).
#
# 문제: 수동 `ssh -f -N -L` 터널은 원격 서버가 멀쩡해도 조용히 끊긴다(idle/네트워크
# 요동). retrieve가 조용히 폴백돼 "왜 학습 점수가 안 나오지"로 이어진다.
# 해법: autossh가 터널 상태를 모니터링해 끊기면 자동 재연결.
#
# 사용: bash training/scorer/tunnel.sh   (이미 떠 있으면 아무것도 안 함)
set -e
KEY=~/.ssh/ai_champion_gpu
HOST=tta@123.41.23.113
PORT=8500

if nc -z localhost "$PORT" 2>/dev/null; then
  echo "[터널] 포트 $PORT 이미 열림 — 건너뜀"
  exit 0
fi

pkill -f "autossh.*-L $PORT:localhost:$PORT" 2>/dev/null || true

export AUTOSSH_GATETIME=0        # 초기 연결 실패도 재시도(기본은 포기함)
export AUTOSSH_POLL=30           # 30초마다 생존 확인

nohup autossh -M 0 -f -N \
  -L "$PORT:localhost:$PORT" \
  -o "ServerAliveInterval=15" \
  -o "ServerAliveCountMax=3" \
  -o "ExitOnForwardFailure=yes" \
  -i "$KEY" "$HOST" \
  > /tmp/e9_tunnel.log 2>&1

sleep 2
if nc -z localhost "$PORT" 2>/dev/null; then
  echo "[터널] 포트 $PORT autossh로 열림 (자동 재연결)"
else
  echo "[터널] 실패 — /tmp/e9_tunnel.log 확인"
  exit 1
fi
