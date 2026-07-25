# 기준선 감사 결과

## 작업 경계

- 신규 작업 루트: `interview_agent(test)`
- 기존 파일은 읽기 전용 기준선으로 사용한다.
- `.env` 내용은 감사·테스트에서 읽거나 출력하지 않는다.
- 실제 외부 API를 사용하는 자동 테스트는 금지한다.

## 기준선

| 파일 | SHA-256 |
|---|---|
| `../interview_agent(v0)/interview_agent_target_first_standalone.py` | `33823C0CBF244A7B664E7734C8079ED7659F8BDCCD4BC8F5F2E535B20E8573C0` |
| `../질문온톨로지/10_interview_ontology.schema.json` | `C6638D52CCA05203F8328011078B661BC86327CB7590DFC7F53B2B7615F48081` |
| `../독립_전략인터뷰_프로그램_구축_계획.md` | `C46AB83F2154329A949B6E8966B09A8A78E66676D7893185F7A11B2F29D5812F` |

## 그대로 재사용할 기능

| 영역 | 기준 함수 | 처리 |
|---|---|---|
| 환경설정 | `load_dotenv`, API 환경변수 설정 | 새 파일 기준 상대 `.env` 경로로 이식 |
| Friendli 호출 | `make_client`, `chat` | 호출 계약과 thinking 설정 유지 |
| Google Grounding | `google_research`, `phase_research`의 검색 부분 | 이번 범위에서는 동작 변경 없이 이식 |
| 콘솔 입력 | `read_human_answer`, 종료 명령 판정 | 여러 줄 입력과 `/end` 유지 |
| 안전 파싱 | `_parse_json_typed`, `extract_json` | JSON 계약 검증 기반으로 재사용 |
| 텍스트 유틸 | `_has_value`, `_as_list`, `_as_text`, `_compare_text` | 새 상태 모델에 맞게 일반화 |
| 답변 기록 | `add_answer_record`의 불변 이력 원칙 | 새 경로·상태 메타데이터를 추가해 재구현 |
| API 우선 정책 | 필드별 LLM 추출 및 기술 실패 시 로컬 fallback | 새 전략 필드용 공통 추출기로 재구현 |
| 질문 안전장치 | 내부 키 차단, 장문·다중 질문 제한 | 새 질문 후보 구조에 맞게 강화 |

## 교체할 기능

| 영역 | 기존 함수/구조 | 교체 이유 |
|---|---|---|
| 상태 초기화 | `init_state`의 평면 B1~B9 | `offer`, `transaction_strategy`, `strategy_tracks` 중심 구조 필요 |
| 완료 판정 | `apply_completeness`, `all_confirmed` | 인터뷰 종료·핵심 앵커·실행 준비도를 분리해야 함 |
| 질문 선택 | `_mandatory_target`, `_select_question`, `INTERVIEW_ORDER` | 다발 순회가 재질문과 실행 위험 질문을 막음 |
| 질문 생성 | `_build_human_question` | 필드 하나 중심이 아니라 질문 후보와 트랙 문맥 중심으로 변경 |
| 답변 반영 | `_apply_answer`, `_apply_final_review` | 트랙 경로, 복수 필드, stale 전파가 필요 |
| 요약 | `_summary_text` | 평면 9개 항목 대신 공통 제안과 트랙별 요약 필요 |
| 인터뷰 루프 | Target-first `phase_interview` | 중요도 기반 9문항과 최종 확인 1문항으로 재구축 |
| 출력 | Target-first `phase_output` | 새 전략 JSON·요약·완료 상태·호환 projection 필요 |
| 스키마 | `10_interview_ontology.schema.json` | 트랙별 관계를 보존하는 새 스키마 필요 |

## 독립성 통과 조건

1. 새 실행 파일 AST에 `interview_agent` 또는 `interview_agent_target_first` import가 없어야 한다.
2. 새 폴더에 실행 파일과 스키마만 복사한 격리 폴더에서도 import되어야 한다.
3. import 시 외부 API를 호출하지 않아야 한다.
4. 모든 런타임 경로는 `Path(__file__).resolve().parent` 기준이어야 한다.
5. 테스트는 mock 클라이언트만 사용해야 한다.

## 기준선에서 확인한 핵심 결함

1. 내용 질문 9개와 핵심 다발 9개가 일대일이어서 일반 실행에서는 의미 재질문 공간이 없다.
2. 기존 스키마의 성공 기준, Key Benefit, Proof 공개 범위, 전환 흐름 등이 질문 라우터에서 활성화되지 않는다.
3. 시장·고객·채널·CTA를 배열로 저장해도 트랙별 관계가 보존되지 않는다.
4. 최종 확인의 상위 값 수정이 하위 전략을 stale 처리하지 않는다.
5. 스키마 유효성과 전략 실행 준비도가 별도 사용자 메시지로 구분되지 않는다.

## 1단계 종료 조건

- [x] 신규 폴더가 생성됨
- [x] 기준 파일과 스키마 해시가 기록됨
- [x] 재사용/교체 함수가 분류됨
- [x] 독립성 조건이 명시됨
- [x] 기존 파일을 수정하지 않음
