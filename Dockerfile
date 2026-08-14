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

# Cloud Run이 PORT를 주입한다 (기본 8080). SQLite와 업로드는 /tmp —
# 인스턴스 소멸과 함께 사라지는 것이 계약이다 (조용히 영속인 척하지 않는다).
# UPLOAD_DIR을 명시하는 이유: 기본값 "uploads"는 상대 경로라 /srv/uploads가
# 되는데, Cloud Run의 컨테이너 파일시스템은 인메모리라 그쪽에 쌓으면 어디서
# 메모리가 새는지 알기 어렵다. 휘발 경로를 한곳(/tmp)으로 모은다.
# 스니펫 로그는 켜지 않는다 — 어차피 재시작마다 증발하는 쓰기다.
ENV A2A_DB_PATH=/tmp/a2a.db \
    UPLOAD_DIR=/tmp/uploads \
    LOG_FORMAT=json
EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
