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
  .venv/bin/python -m pytest tests/test_clarify.py tests/test_outcome_loop.py \
      tests/test_ontology_bench.py -q >/dev/null 2>&1 \
    && ok "결정적 테스트 통과" || { bad "테스트 실패 — 배포 중단 권장"; fail=1; }

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
  [ -n "$GCP_PROJECT" ] || { bad "GCP_PROJECT 필요"; exit 1; }
  echo "→ Cloud Run 배포 ($SERVICE / $GCP_REGION)"
  # max-instances=1: job 폴링이 인스턴스 로컬 메모리를 쓰므로 스케일아웃하면
  # 폴링이 다른 인스턴스에 붙어 '결과 없음'이 된다 (스펙 Architecture).
  gcloud run deploy "$SERVICE" \
    --project "$GCP_PROJECT" --region "$GCP_REGION" \
    --source . --allow-unauthenticated \
    --max-instances 1 --memory 1Gi --timeout 900 \
    --set-env-vars "LLM_PROVIDER=openai,SAAS_AUTH=supabase,SAAS_STORE=supabase" \
    --set-env-vars "SUPABASE_URL=${SUPABASE_URL:?SUPABASE_URL 필요}" \
    --set-env-vars "SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY:?SUPABASE_ANON_KEY 필요}" \
    --set-env-vars "SUPABASE_SERVICE_KEY=${SUPABASE_SERVICE_KEY:?SUPABASE_SERVICE_KEY 필요}" \
    --set-env-vars "OPENAI_API_KEY=${OPENAI_API_KEY:?OPENAI_API_KEY 필요}" \
    --set-env-vars "TAVILY_API_KEY=${TAVILY_API_KEY:?TAVILY_API_KEY 필요}" \
    --set-env-vars "SAAS_ALLOWED_USERS=${SAAS_ALLOWED_USERS:?SAAS_ALLOWED_USERS 필요}"
  local url
  url=$(gcloud run services describe "$SERVICE" --project "$GCP_PROJECT" \
        --region "$GCP_REGION" --format 'value(status.url)')
  ok "엔진 URL: $url"
  echo "  스모크: curl -s -o /dev/null -w '%{http_code}\\n' $url/saas/settings/llm  # 401이 정상(무토큰)"
  echo "$url" > .engine_url
}

web() {
  local engine_url
  engine_url=$(cat .engine_url 2>/dev/null || echo "${ENGINE_URL:-}")
  [ -n "$engine_url" ] || { bad "ENGINE_URL 미상 — engine 먼저 배포하거나 ENGINE_URL 설정"; exit 1; }
  echo "→ Vercel 배포 (ENGINE_URL=$engine_url)"
  cd web
  # env는 Vercel 프로젝트에 저장한다 — 빌드마다 넘기면 누락 시 조용히 dev 모드가 된다
  for kv in "ENGINE_URL=$engine_url" \
            "NEXT_PUBLIC_SUPABASE_URL=${SUPABASE_URL:?}" \
            "NEXT_PUBLIC_SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY:?}"; do
    printf '%s' "${kv#*=}" | vercel env add "${kv%%=*}" production --force >/dev/null 2>&1 || true
  done
  vercel deploy --prod --yes
  cd ..
  ok "웹 배포 완료 — Supabase Auth의 Redirect URLs에 이 도메인을 추가하세요"
}

case "${1:-check}" in
  check)  check ;;
  db)     db ;;
  engine) engine ;;
  web)    web ;;
  all)    check && db && engine && web ;;
  *) echo "사용: $0 {check|db|engine|web|all}"; exit 1 ;;
esac
