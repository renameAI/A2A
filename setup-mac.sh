#!/usr/bin/env bash
# A2A 브라우저 + 로컬 백엔드 — 맥 설치(한 번). docs/LOCAL_MAC.md
#
#   ./setup-mac.sh            설치·갱신(다시 돌려도 안전 — 있는 건 건너뛴다)
#   ./setup-mac.sh --check    무엇을 할지 보여 주기만(아무것도 바꾸지 않음)
#
# 하는 일:
#   1) Homebrew 패키지: postgresql@17, redis, node@22, uv  → brew services로 상시 실행
#   2) 로컬 Postgres DB 3개: a2a_control, a2a_cell_a, a2a_cell_b (팀 채팅 컨트롤 플레인 + 셀)
#   3) Python 가상환경(.venv, uv) + requirements.txt
#   4) .env — 없는 키만 채운다(있는 값은 절대 덮어쓰지 않음). ANTHROPIC_API_KEY는 화면에 안 보이게 입력
#   5) 팀 채팅 시드(내 이름으로 워크스페이스 TEST) · 브라우저 npm 설치·빌드
# 모든 데이터는 이 맥 안에만 쌓인다(Postgres·Redis는 127.0.0.1, 브라우저 데이터는 ~/Library/Application Support/a2a-browser).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
CHECK=0; [[ "${1:-}" == "--check" ]] && CHECK=1
# 입력은 터미널에서 직접(파이프로 넘겨도 되게 — brew 등이 표준입력을 먹지 않게)
ask_in() { if [[ -t 0 ]]; then read -r "$@"; elif [[ -r /dev/tty ]] && { : < /dev/tty; } 2>/dev/null; then read -r "$@" < /dev/tty; else read -r "$@" || true; fi; }
say()  { printf '\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
plan() { printf '  · %s\n' "$*"; }
die()  { printf '\033[31m✕ %s\033[0m\n' "$*" >&2; exit 1; }
run()  { if (( CHECK )); then plan "$*"; else "$@"; fi; }

# ── 0. 기본 도구 ──
say "기본 도구"
[[ "$(uname)" == "Darwin" ]] || die "맥 전용 스크립트입니다"
xcode-select -p >/dev/null 2>&1 || die "Xcode 명령줄 도구가 없어요 — 터미널에서: xcode-select --install"
if ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "git이 막혀 있어요(Xcode 라이선스) — 터미널에서: sudo xcodebuild -license accept"
fi
command -v brew >/dev/null || die "Homebrew가 없어요 — https://brew.sh 의 설치 한 줄을 실행한 뒤 다시 돌려 주세요"
ok "Xcode 도구 · git · Homebrew"

# ── 1. 패키지 ──
say "패키지 (Homebrew)"
for f in postgresql@17 redis node@22 uv; do
  if brew list --versions "$f" >/dev/null 2>&1; then ok "$f"; else run brew install "$f" </dev/null; fi
done
PG_BIN="$(brew --prefix postgresql@17)/bin"
export PATH="$PG_BIN:$(brew --prefix node@22)/bin:$PATH"
for s in postgresql@17 redis; do
  if brew services list | awk -v s="$s" '$1==s && $2=="started"{f=1} END{exit !f}'; then ok "$s 실행 중"; else run brew services start "$s"; fi
done
if (( ! CHECK )); then
  for i in $(seq 1 30); do "$PG_BIN/pg_isready" -q -h 127.0.0.1 && break; sleep 1; done
  "$PG_BIN/pg_isready" -q -h 127.0.0.1 || die "Postgres가 뜨지 않았어요 — brew services list 로 확인해 주세요"
  redis-cli -h 127.0.0.1 ping >/dev/null || die "Redis가 뜨지 않았어요 — brew services list 로 확인해 주세요"
fi

# ── 2. DB ──
say "로컬 DB"
PGU="$(whoami)"
DBP="${A2A_DB_PREFIX:-a2a}"       # 시험용으로 다른 이름을 쓸 때만 바꾼다
for db in "${DBP}_control" "${DBP}_cell_a" "${DBP}_cell_b"; do
  if (( CHECK )); then plan "createdb $db (없으면)"; continue; fi
  if "$PG_BIN/psql" -h 127.0.0.1 -U "$PGU" -d postgres -tAc "select 1 from pg_database where datname='$db'" | grep -q 1; then ok "$db"; else "$PG_BIN/createdb" -h 127.0.0.1 -U "$PGU" "$db"; ok "$db 만듦"; fi
done

# ── 3. Python ──
say "Python 가상환경 (.venv)"
if [[ -x .venv/bin/python ]]; then ok ".venv 있음"; else run uv venv .venv --python 3.13; fi
run uv pip install --python .venv/bin/python -q -r requirements.txt
ok "requirements.txt"

# ── 4. .env — 없는 키만 ──
say ".env (없는 키만 채움 — 있는 값은 그대로)"
ENV="$ROOT/.env"
[[ -f "$ENV" ]] || { (( CHECK )) && plan ".env 새로 만듦" || { umask 077; : > "$ENV"; }; }
has()  { [[ -f "$ENV" ]] && grep -qE "^$1=" "$ENV"; }
put()  { if has "$1"; then ok "$1 (있음)"; elif (( CHECK )); then plan "$1=$2"; else printf '%s=%s\n' "$1" "$2" >> "$ENV"; ok "$1"; fi; }
ME_DEFAULT="$(whoami | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_')"
if has A2A_USER; then ME="$(grep -E '^A2A_USER=' "$ENV" | head -1 | cut -d= -f2-)"; else
  if (( CHECK )); then ME="$ME_DEFAULT"; else ask_in -p "  이 앱에서 쓸 내 이름(영문 소문자, 엔터=$ME_DEFAULT): " ME || ME=""; ME="${ME:-$ME_DEFAULT}"; fi
fi
[[ "$ME" =~ ^[a-z0-9_]{2,32}$ ]] || die "이름은 영문 소문자·숫자·_ 2~32자로 해 주세요"
put A2A_USER "$ME"
put A2A_TENANT "test"
put SAAS_AUTH "dev"
put SAAS_STORE "local"
put SAAS_DB_PATH "$ROOT/data/saas.db"
put SAAS_ALLOWED_USERS "$ME,alice,bob,carol,vendor,ops,acme_ceo"
put CHAT_CONTROL_DSN "postgresql://$PGU@127.0.0.1:5432/${DBP}_control"
put DEV_CELL_A "postgresql://$PGU@127.0.0.1:5432/${DBP}_cell_a"
put DEV_CELL_B "postgresql://$PGU@127.0.0.1:5432/${DBP}_cell_b"
put CHAT_FANOUT "redis"
put CHAT_REDIS_URL "redis://127.0.0.1:6379/0"
put CHAT_ARCHIVE_DIR "$ROOT/data/archive"
put CHAT_MOVE_INLINE "0"
if has ANTHROPIC_API_KEY && [[ -n "$(grep -E '^ANTHROPIC_API_KEY=' "$ENV" | cut -d= -f2-)" ]]; then ok "ANTHROPIC_API_KEY (있음)"; elif (( CHECK )); then plan "ANTHROPIC_API_KEY 입력 받음"; else
  K=""; ask_in -s -p "  ANTHROPIC_API_KEY (입력이 화면에 안 보여요, 엔터=나중에): " K || K=""; echo
  sed -i '' '/^ANTHROPIC_API_KEY=/d' "$ENV"; printf 'ANTHROPIC_API_KEY=%s\n' "$K" >> "$ENV"
  [[ -n "$K" ]] && ok "ANTHROPIC_API_KEY" || plan "ANTHROPIC_API_KEY 비어 있음 — 나중에 .env에 넣으면 AI 기능이 켜져요"
fi
(( CHECK )) || chmod 600 "$ENV"
run mkdir -p data/archive uploads pages

# ── 5. 팀 채팅 시드 · 브라우저 ──
say "팀 채팅 시드 (워크스페이스 TEST, 관리자 $ME)"
if (( CHECK )); then plan ".venv/bin/python scripts/chat_dev_seed.py --admin $ME"; else
  ( set -a; . "$ENV"; set +a; .venv/bin/python scripts/chat_dev_seed.py --admin "$ME" ) && ok "시드"
fi
say "브라우저 (browser/)"
run bash -c "cd browser && npm ci --no-audit --no-fund && npx vite build"
ok "npm 설치·빌드"

say "끝 — 실행: ./run-mac.sh"
