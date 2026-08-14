#!/usr/bin/env bash
# 배포 오케스트레이션 — Supabase(DB·Auth) + Cloud Run(엔진) + Vercel(웹).
#
#   scripts/deploy.sh check      # 사전 점검만 (파괴적 동작 없음)
#   scripts/deploy.sh db         # Supabase 마이그레이션 적용
#   scripts/deploy.sh engine     # Cloud Run 빌드·배포
#   scripts/deploy.sh web        # Vercel 프로덕션 배포
#   scripts/deploy.sh all        # db → engine → web
#
# 로그인(supabase login / vercel login / gcloud auth login)은 이 스크립트가
# 하지 않는다 — 브라우저 대화형이고 자격증명은 사람이 다뤄야 한다.
# check가 무엇이 빠졌는지 정확히 알려준다.
set -euo pipefail

: "${GCP_PROJECT:=}"          # Cloud Run 프로젝트
: "${GCP_REGION:=asia-northeast3}"
: "${SERVICE:=a2a-engine}"
: "${SUPABASE_PROJECT_REF:=}"  # Supabase 프로젝트 ref (대시보드 URL의 해시)

ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }

check() {
  local fail=0
  echo "── CLI ──"
  for c in supabase gcloud vercel; do
    command -v "$c" >/dev/null && ok "$c 설치됨" || { bad "$c 없음"; fail=1; }
  done

  echo "── 인증 ──"
  supabase projects list >/dev/null 2>&1 \
    && ok "supabase 로그인됨" \
    || { bad "supabase 미로그인 → supabase login"; fail=1; }
  gcloud auth print-access-token >/dev/null 2>&1 \
    && ok "gcloud 로그인됨 ($(gcloud config get-value account 2>/dev/null))" \
    || { bad "gcloud 미로그인 → gcloud auth login"; fail=1; }
  vercel whoami >/dev/null 2>&1 \
    && ok "vercel 로그인됨 ($(vercel whoami 2>/dev/null))" \
    || { bad "vercel 미로그인 → vercel login"; fail=1; }

  echo "── 환경변수 ──"
  [ -n "$GCP_PROJECT" ] && ok "GCP_PROJECT=$GCP_PROJECT" \
    || { bad "GCP_PROJECT 미설정"; fail=1; }
  [ -n "$SUPABASE_PROJECT_REF" ] && ok "SUPABASE_PROJECT_REF=$SUPABASE_PROJECT_REF" \
    || { bad "SUPABASE_PROJECT_REF 미설정"; fail=1; }
  for v in OPENAI_API_KEY TAVILY_API_KEY SAAS_ALLOWED_USERS; do
    [ -n "${!v:-}" ] && ok "$v 있음" || warn "$v 없음 (engine 배포 전 필요)"
  done
  # 허용 목록이 비면 아무도 못 들어온다 — 배포 후 403의 가장 흔한 원인
  [ -z "${SAAS_ALLOWED_USERS:-}" ] && \
    warn "SAAS_ALLOWED_USERS가 비면 전원 거부됩니다(fail-closed 설계)"

  echo "── 로컬 게이트 ──"
  # 라우터·스토어 관통을 포함한다. 이전 게이트는 순수 함수 21건만 돌려
  # /search·/refine·인증·비용이 한 줄도 안 지났다(감사 확정 발견).
  # 실패 출력을 버리지 않는다 — 무엇이 깨졌는지 모르면 게이트가 아니다.
  if .venv/bin/python -m pytest -q \
       tests/test_saas_layer.py tests/test_supabase_store.py \
       tests/test_attack_surface.py tests/test_discover_identity.py \
       tests/test_router_guards.py tests/test_failure_honesty.py tests/test_deletion.py tests/test_core_modules.py \
       tests/test_clarify.py tests/test_outcome_loop.py \
       tests/test_ontology_bench.py 2>&1 | tail -3; then
    ok "결정적 테스트 통과"
  else
    bad "테스트 실패 — 배포 중단"; fail=1
  fi

  [ "$fail" = 0 ] && echo "→ 준비 완료" || echo "→ 위 ✗ 를 먼저 해결하세요"
  return "$fail"
}

db() {
  [ -n "$SUPABASE_PROJECT_REF" ] || { bad "SUPABASE_PROJECT_REF 필요"; exit 1; }
  echo "→ Supabase 링크·마이그레이션 ($SUPABASE_PROJECT_REF)"
  supabase link --project-ref "$SUPABASE_PROJECT_REF"
  # push는 미적용 마이그레이션만 올린다. 기존 데이터를 지우지 않는다.
  supabase db push
  ok "마이그레이션 적용 완료"
}

engine() {
  # 엔진도 Vercel에 올린다 (Cloud Run 아님).
  #
  # 근거: 실측상 GPT API 기준 job 최대 129초로 Vercel Fluid(300초) 안에
  # 들어간다. 이전에 "22.7분이라 불가"로 판단했던 것은 로컬 EXAONE 수치를
  # 섞은 오류였다(모델별 분리: API 최대 129s / 로컬 최대 1365s).
  #
  # 서버리스 조건은 시간이 아니라 **상태**였고, job 원장을 SaasStore로
  # 옮겨(V1) 해결했다 — 폴링이 어느 인스턴스에 붙어도 같은 원장을 본다.
  echo "→ Vercel 엔진 배포"
  local envf
  envf=$(mktemp -t a2a-env); chmod 600 "$envf"
  trap 'rm -f "$envf"' RETURN INT TERM

  # env는 Vercel 프로젝트에 저장하고 실패하면 중단한다 — 키가 빠진 채
  # 뜨면 readyz가 503으로 알려주긴 하지만, 배포 자체를 막는 편이 낫다.
  for kv in "LLM_PROVIDER=openai" "SAAS_AUTH=supabase" "SAAS_STORE=supabase" \
            "LOG_FORMAT=json" \
            "SUPABASE_URL=${SUPABASE_URL:?SUPABASE_URL 필요}" \
            "SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY:?SUPABASE_ANON_KEY 필요}" \
            "SUPABASE_SERVICE_KEY=${SUPABASE_SERVICE_KEY:?SUPABASE_SERVICE_KEY 필요}" \
            "OPENAI_API_KEY=${OPENAI_API_KEY:?OPENAI_API_KEY 필요}" \
            "TAVILY_API_KEY=${TAVILY_API_KEY:?TAVILY_API_KEY 필요}" \
            "SAAS_ALLOWED_USERS=${SAAS_ALLOWED_USERS:?SAAS_ALLOWED_USERS 필요}" \
            "COST_CAP_REQUEST_USD=${COST_CAP_REQUEST_USD:-5}" \
            "COST_CAP_MONTH_USD=${COST_CAP_MONTH_USD:-100}" \
            "COST_CAP_GLOBAL_MONTH_USD=${COST_CAP_GLOBAL_MONTH_USD:-50}"; do
    local name="${kv%%=*}" value="${kv#*=}"
    printf '%s' "$value" | vercel env add "$name" production --force --yes \
      >/dev/null 2>&1 && ok "env $name" \
      || { bad "env $name 설정 실패 — 중단"; exit 1; }
  done

  vercel deploy --prod --yes
  local url
  url=$(vercel inspect --json 2>/dev/null | python3 -c \
    'import json,sys;print(json.load(sys.stdin).get("url",""))' 2>/dev/null || true)
  [ -n "$url" ] && { echo "https://$url" > .engine_url; ok "엔진 URL: https://$url"; } \
    || warn "엔진 URL 자동 추출 실패 — vercel ls 로 확인해 .engine_url에 적으세요"
  echo "  스모크: curl -s <URL>/healthz  ·  curl -s <URL>/readyz"
}

web() {
  local engine_url
  engine_url=$(cat .engine_url 2>/dev/null || echo "${ENGINE_URL:-}")
  [ -n "$engine_url" ] || { bad "ENGINE_URL 미상 — engine 먼저 배포하거나 ENGINE_URL 설정"; exit 1; }
  echo "→ Vercel 배포 (ENGINE_URL=$engine_url)"
  cd web

  # env는 Vercel 프로젝트에 저장하고, **실패하면 배포를 중단한다.**
  # 조용히 넘기면 안 되는 이유: NEXT_PUBLIC_SUPABASE_* 가 없으면 프론트의
  # isConfigured가 false가 되어 로그인 게이트가 통째로 사라지고 X-Dev-User
  # 폴백으로 뜬다. 배포 실패보다 '인증 없이 뜬 프로덕션'이 훨씬 나쁘다.
  for kv in "ENGINE_URL=$engine_url" \
            "NEXT_PUBLIC_SUPABASE_URL=${SUPABASE_URL:?SUPABASE_URL 필요}" \
            "NEXT_PUBLIC_SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY:?SUPABASE_ANON_KEY 필요}"; do
    local name="${kv%%=*}" value="${kv#*=}"
    if printf '%s' "$value" | vercel env add "$name" production --force --yes \
         >/dev/null 2>&1; then
      ok "env $name 설정"
    else
      bad "env $name 설정 실패 — 배포 중단 (인증 없이 뜨는 것을 막는다)"
      cd ..; exit 1
    fi
  done

  # 빌드 전에 실제로 저장됐는지 되읽어 확인한다 — add가 0으로 끝나도
  # 스코프가 달랐거나 프로젝트 링크가 다른 곳이면 값이 없을 수 있다.
  local have
  have=$(vercel env ls production 2>/dev/null | grep -c "NEXT_PUBLIC_SUPABASE" || true)
  [ "${have:-0}" -ge 2 ] || { bad "NEXT_PUBLIC_SUPABASE_* 확인 실패 — 중단"; cd ..; exit 1; }

  vercel deploy --prod --yes
  cd ..
  ok "웹 배포 완료"
  warn "Supabase Auth → URL Configuration → Redirect URLs 에 이 도메인을 추가하세요"
  warn "추가 전에는 매직링크가 localhost로 돌아와 로그인이 안 됩니다"
}

case "${1:-check}" in
  check)  check ;;
  db)     db ;;
  engine) engine ;;
  web)    web ;;
  all)    check && db && engine && web ;;
  *) echo "사용: $0 {check|db|engine|web|all}"; exit 1 ;;
esac
