# judge_cases_exaone — 판단(JUDGE)부 K-EXAONE 적용판

원본 `judge_cases`(Gemini 전용)와 **온톨로지·스키마·회사 풀·실행 방법이 전부 동일**하며,
LLM 호출 라우팅만 다음과 같이 바뀐 버전이다.

| 역할 | 원본 (judge_cases) | 이 폴더 (judge_cases_exaone) |
|---|---|---|
| **판단** — EXTRACT 상태 갱신, 최종 판정(구매자·판매자), 결론 설명서, 피드백 조율(TUNER) | gemini-3.1-pro-preview | **K-EXAONE-236B** (Friendli 전용 엔드포인트, OpenAI 호환) |
| 발화(SPEAK)·제안 메일·조정규칙 scope 심사 | gemini-3.1-flash-lite | **K-EXAONE-236B** |
| 리서치 — 검색 그라운딩 (company_research.py 및 시뮬레이터 내 회사 조사) | Gemini | Gemini (유일한 예외 — 검색 그라운딩은 Gemini 전용 기능) |

## 구현 방식
- `negotiation_sim.py`의 `JUDGE_MODEL`·`SPEAK_MODEL` 을 모두 K-EXAONE 라벨로 지정하고,
  `chat()`/`chat_json()` 이 이 라벨을 보면 Friendli(OpenAI 호환) 클라이언트로 라우팅한다.
  검색(`use_search=True`) 호출만 `RESEARCH_MODEL`(gemini-3.1-flash-lite)로 자동 전환된다.
- `feedback_loop.py` 는 `negotiation_sim` 의 `JUDGE_MODEL`/`chat_json` 을 그대로 가져다
  쓰므로 **수정 없이 자동으로** 판단부가 K-EXAONE 으로 전환된다.
- JSON 판단 호출은 `response_format=json_object` 로 디코딩 단계에서 JSON 문법을 강제하고
  (`top_p=0.9` 로 저확률 깨진 토큰 억제), 실패 시 일반 모드로 다운그레이드 후
  기존 `extract_json` + 정규화 안전망이 받아낸다.
- `enable_thinking=False` 기본(빠름). 판단 심층화가 필요하면 CONFIG 의
  `ENABLE_THINKING=True` 로 바꾸면 된다.

## 실행 준비
```
pip install google-genai openai jsonschema pyyaml
```
`secrets_local.py`(이 폴더, .gitignore 대상)에 세 값이 필요하다:
`GOOGLE_API_KEY`, `FRIENDLI_TOKEN`, `EXAONE_ENDPOINT_ID`
(또는 동명의 환경변수).

**중요**: K-EXAONE 은 Friendli **전용 엔드포인트**라서 (1) 엔드포인트가 켜져(Running)
있어야 호출이 성공하고, (2) 과금이 토큰당이 아니라 **가동시간 기준**이다.
비용 리포트의 달러 금액은 Gemini 부분만 반영된다.

## 실행 방법 (원본과 동일)
```
python negotiation_sim.py                 # 풀에서 무작위 두 회사
python negotiation_sim.py --choose        # 풀에서 직접 선택
python negotiation_sim.py --scenario baseline
python feedback_loop.py --type market_reply --text "..."
python company_research.py --company "회사명"
```
