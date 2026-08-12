# A2A 엔진 — Cloud Run 서빙 (이슈 #6, 이슈 A)
#
# 계약: 엔진은 무상태 계산기. 컨테이너 내 SQLite(A2A_DB_PATH)는 휘발 캐시이며
# 보존 책임은 API 계층의 Firestore에 있다. MVP는 max-instances=1로 배포해
# job 폴링 일관성을 확보한다 (스펙 Architecture 절).
FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY docs/LEAD_DISCOVERY_SAAS_PLAN.md docs/

# Cloud Run이 PORT를 주입한다 (기본 8080). SQLite는 /tmp — 인스턴스 소멸과 함께
# 사라지는 것이 계약이다 (조용히 영속인 척하지 않는다).
ENV A2A_DB_PATH=/tmp/a2a.db
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
