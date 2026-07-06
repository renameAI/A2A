# 개발 가이드

## 로컬 개발 환경 설정

```bash
# 1. 의존성 설치
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 환경변수 설정 (필요시)
cp .env.example .env
# FRIENDLI_TOKEN, FRIENDLI_ENDPOINT_ID 등을 .env에 입력

# 3. 서버 시작 (Mock 모드 기본)
.venv/bin/uvicorn app.main:app --port 8423

# 또는 온라인 LLM 모드
LLM_PROVIDER=friendli .venv/bin/uvicorn app.main:app --port 8423
```

## 테스트

```bash
# 모든 테스트 실행 (항상 Mock 모드, 비용 0)
.venv/bin/python -m pytest tests/ -q

# 특정 테스트만 실행
.venv/bin/python -m pytest tests/test_consultant.py -v
```

## 코드 스타일

- 파이썬: 기본 PEP 8 따름
- 프롬프트: **모두 `app/engine/prompts.py`에 중앙화** — 다른 파일에서 프롬프트 문자열 정의 금지
- 커밋 메시지: 한국어 + 변경 내용 명시 (예: "Fix: 보강 질문 무한루프", "Feat: 4지선다 추가")

## 핵심 설계 원칙

1. **프롬프트 중앙화**: 모든 LLM 시스템 프롬프트·스키마는 `app/engine/prompts.py`에만 존재
2. **엔진은 stateless**: `app/engine/*.py`는 상태를 갖지 않음 — 상태는 `app/product/store.py`에서만 관리
3. **계약 방어**: 모든 LLM 입출력은 JSON 스키마로 검증; 예외 불가
4. **더블 디펜스**: 약한 모델 대비 프롬프트 레벨(`HARD_RULES`) + 코드 레벨(`sanitize()`) 방어

## PR 체크리스트

- [ ] 테스트 65개 전부 통과
- [ ] 새 프롬프트는 `app/engine/prompts.py`에만 추가
- [ ] `.env` 파일이 `.gitignore`에 있음 (절대 커밋 금지)
- [ ] 커밋 메시지에 변경 내용 명시
- [ ] README 업데이트 필요시 동일 PR에 포함
