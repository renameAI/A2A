# new-judge 시스템 사용 설명서
### 리서치 → 협상 판정(judge) → 설명·제안 메일 → 피드백 학습 — 전 과정 가이드 (v1.0)

> **대상 독자**: 이 시스템을 처음 실행하는 사용자.
> **시스템 한 줄 정의**: 회사 리서치를 축적한 풀(pool)에서 두 회사를 골라, 구매자·판매자
> 온톨로지를 판단 근거로 삼는 두 AI가 협상하고, 구조화된 결정·설명서·제안 메일을
> 산출하며, 사람의 피드백과 실제 회신으로 온톨로지가 조율되는(도메인 범위 인식)
> "학습 없는 학습" 시스템.

---

## 0. 전체 파이프라인 한 장

```
[1단계: 리서치]                       [2단계: 협상 판정 (judge)]
company_research.py                  negotiation_sim.py  (인자 없이 실행 = 풀 모드)
  · 단일/코스닥 일괄 리서치             · 풀에서 두 회사 선택(임의/지정/번호선택)
  · Google 검색 그라운딩               · 봉인(private) 자동 생성 [구성]
  · company_pool/ 에 저장     ──────►  · 조정 규칙 로드 + 범위(scope) 심사
                                       · Buyer AI ↔ Seller AI 라운드 협상
                                            (EXTRACT 판정 / SPEAK 발화 분리)
                                       · 결정 5종 + 스키마 검증
                                              │
                              ┌───────────────┼──────────────────┐
                              ▼               ▼                  ▼
                        _transcript.md   _explanation.md    _email_seller/buyer.md
                        (대화 로그)      (결론 설명서)       (협력 제안 메일,
                                                            recommend/conditional 시)
                                              │
[3단계: 피드백 학습]                          ▼
feedback_loop.py                        사람 검토 / 실제 메일 발송
  · 사람 피드백 or 실제 회신 입력  ◄──────────┘
  · 가설 채점(확증/반증)
  · 조정 규칙 제안(+적용 범위 scope) → 사람 승인
  · ontology_adjustments.json 반영 ──► 다음 협상부터 자동 적용(범위 심사 통과분만)
```

**파일 지도** (`judge_cases\` 폴더):

| 파일 | 역할 |
|---|---|
| `company_research.py` | 1단계: 회사 리서치 → 풀 저장 |
| `negotiation_sim.py` | 2단계: 협상 판정(judge) 본체 |
| `feedback_loop.py` | 3단계: 피드백 입력·온톨로지 조율 |
| `buyer_ontology.yaml` / `seller_ontology.yaml` | 판단 온톨로지 정규 정의(불변) |
| `negotiation_ontology.schema.json` | 세션 검증 스키마 |
| `ontology_adjustments.json` | 피드백으로 축적된 조정 계층(가변) |
| `hypothesis_library.yaml` | 가설 시드 라이브러리 |
| `company_pool\*.json` | 리서치된 회사 풀 |
| `feedback_ledger.jsonl` | 피드백 원장 |

---

## 1. 사전 준비 (1회)

- Python 환경: miniconda `wgpaeng` (필요 패키지 `google-genai`, `jsonschema`, `requests` 설치됨)
- API 키: `negotiation_sim.py` 상단 `GOOGLE_API_KEY` (Gemini — 현재 입력됨)
- 실행 위치:
```powershell
$py = "C:\Users\wgpae\.conda\envs\wgpaeng\python.exe"
cd c:\Users\wgpae\리네임\judge_cases
```
> conda 환경을 활성화했다면 `& $py` 대신 `python` 으로 실행해도 됩니다.

---

## 2. 1단계 — 회사 리서치 (`company_research.py`)

### 2.1 단일 회사
```powershell
& $py company_research.py --company "회사명" --hint "무엇을 하는 회사인지 한 줄"
```
→ Google 검색 그라운딩으로 6~10줄 B2B 프로필 생성, `company_pool\회사명.json` 저장.
검색 자료가 없으면 `provenance=insufficient`로 표시(가짜 프로필을 만들지 않음).

### 2.2 코스닥 일괄
```powershell
& $py company_research.py --kosdaq 200 --workers 4
```
- KRX 상장법인목록에서 실명단을 내려받아(스팩 제외) 무작위 N곳 표본 → 병렬 리서치
- **이어받기 가능**: 재실행 시 이미 저장된 회사는 건너뜀 (`--force` 로 재수집)
- 실측: 200곳 ≈ 7분, 비용 ≈ $0.03

### 2.3 풀 확인
```powershell
& $py company_research.py --list
```

---

## 3. 2단계 — 협상 판정 (`negotiation_sim.py`)

### 3.1 기본 실행 (풀 모드)
```powershell
& $py negotiation_sim.py                       # 풀에서 임의 두 회사
& $py negotiation_sim.py --pool-seller 펄어비스 --pool-buyer 넥스틴   # 지정(부분일치)
& $py negotiation_sim.py --choose              # 번호 목록에서 직접 선택
& $py negotiation_sim.py --list-pool           # 풀 목록만 출력
```

실행 흐름:
1. 두 회사의 저장된 리서치를 공개 프로필로 로드
2. 각 역할(판매/구매)에 맞는 **봉인(private state)** 을 온톨로지 축에 맞춰 자동 생성(`[구성]` — 실제 속내 아님, §7 한계 참조)
3. 조정 규칙을 로드하고 **범위(scope) 심사** — 이 협상 도메인에 맞는 규칙만 적용
   (`[조정 규칙] buyer 적용 N·범위외 제외 M ...` 로 표시)
4. 최대 7라운드 협상: 매 턴 각 측이 [상대 발화→내 온톨로지 상태 갱신(EXTRACT)] +
   [갭/무브 기반 발화(SPEAK)] 수행
5. 종료 → 최종 구조화 판정 → 스키마 검증

### 3.2 결정의 의미 (구매자 5종 / 판매자 outcome)
| 구매자 결정 | 의미 |
|---|---|
| `recommend` | 진행 — 다운사이드 낮고 핵심 충족 |
| `conditional` | 조건부 진행 — 조건 + **검증 방법(check_method)** 명시 |
| `hold` | 보류(실패 아님) — 관계 유지하며 확인 리스크 채움 |
| `terminate_structural` | 구조적 불가(캐파·인증) — 관계 보존, 조건 변화 시 재접촉 |
| `terminate_values` | 가치충돌·착취 감지 — 관계·정보 차단 |

판매자 outcome: `meeting_agreed / poc_agreed / deal_structured / hold /
walk_away_structural / walk_away_values / no_agreement`

### 3.3 산출물 (실행마다 자동 생성)
| 파일 | 내용 |
|---|---|
| `negotiation_{태그}_transcript.md` | 라운드별 대화 전문 |
| `negotiation_{태그}_session.json` | 온톨로지 상태·결정·조건·리스크·**가설 카드**·감사추적(audit) — 스키마 검증됨 |
| `negotiation_{태그}_explanation.md` | **결론 설명서**: 어떤 발화가 어떤 판단을 움직였는지 인과로 서술 |
| `negotiation_{태그}_email_seller.md` / `_email_buyer.md` | 협력 제안/회신 메일 (recommend·conditional 시) |

### 3.4 구모드 (필요 시)
```powershell
& $py negotiation_sim.py --scenario recommend_clean      # 프리셋 6종 (all=전부)
& $py negotiation_sim.py --seller "회사A" --seller-hint "..." --buyer "회사B" --buyer-hint "..."  # 즉석 리서치
& $py negotiation_sim.py --profiles my.json              # 수동 프로필 파일
```

---

## 4. 3단계 — 피드백 학습 (`feedback_loop.py`)

### 4.1 언제 쓰나
- 결론 설명서·제안 메일을 읽고 **의견**이 있을 때 (`human_feedback`)
- 제안 메일을 실제 발송했고 **상대의 실제 회신**이 왔을 때 (`market_reply` — 가장 강한 학습 신호)

### 4.2 사용
```powershell
& $py feedback_loop.py                                          # 대화형(유형 선택→내용 입력)
& $py feedback_loop.py --type market_reply --text "실제 회신…"    # 비대화형
& $py feedback_loop.py --type human_feedback --file 의견.txt
& $py feedback_loop.py --session negotiation_xxx_session.json   # 특정 세션 지정(기본=최신)
```

### 4.3 무슨 일이 일어나나
1. 피드백이 `feedback_ledger.jsonl` 에 기록(market_reply 는 provenance=observed)
2. 조율자가 세션의 **가설 카드를 채점**(확증/반증 — evidence_needed 와 대조)하고
   **조정 규칙**을 제안 — 각 규칙에 **적용 범위(scope)** 포함
   (도메인 특수 관행이면 그 도메인 한정, 보편 원칙만 전역)
3. **사람 승인**: 번호 선택 / `a`(전체) / `n`(취소). (`--auto-apply` 는 테스트 전용)
4. 승인분이 `ontology_adjustments.json` 에 origin 피드백 id 와 함께 기록
5. **다음 협상부터 자동 적용** — 단, 협상 시작 시 범위 심사를 통과한 규칙만
   (예: 아트 도메인에서 배운 "룩북 선제 제공" 규칙은 반도체 협상에는 미적용)

### 4.4 조정 규칙 관리
- 규칙 끄기: `ontology_adjustments.json` 에서 해당 규칙 `"status": "retired"`
- 정규 온톨로지(YAML)는 불변 — 조정은 전부 이 계층에서 이뤄져 추적·롤백 가능

---

## 5. 전형적 사용 시나리오

**시나리오 A — 탐색적 매칭 발굴**
```powershell
& $py negotiation_sim.py            # 임의 쌍 반복 실행 → recommend/conditional 쌍 발굴
```
**시나리오 B — 특정 회사의 파트너 검토**
```powershell
& $py negotiation_sim.py --pool-seller "우리회사" --pool-buyer "후보사"
# → 설명서로 판단 근거 검토 → 제안 메일 초안 활용
```
**시나리오 C — 실전 아웃리치 + 학습 루프**
```
협상 실행 → 제안 메일 검토·수정·발송 → 회신 수신
→ feedback_loop.py 로 회신 입력 → 조율 승인
→ 같은/유사 상대로 재협상 (조정 반영 확인)
```

---

## 6. 비용·시간 (실측 기준)

| 작업 | 시간 | 비용(Gemini 종량) |
|---|---|---|
| 회사 리서치 1건 | ~10초 | ~$0.0001 |
| 코스닥 200곳 일괄 | ~7분 | ~$0.03 |
| 협상 1회(전 산출물 포함) | 2~5분 | ~$0.2 (판정=Pro 가 대부분) |
| 피드백 조율 1회 | ~1분 | ~$0.05 |
| 범위 심사(협상당) | 수 초 | ~$0.001 |

모델: 판정·설명·조율 = `gemini-3.1-pro-preview` / 발화·메일·리서치·심사 = `gemini-3.1-flash-lite`.
실행 종료마다 비용 리포트가 자동 출력됩니다.

---

## 7. 알아야 할 한계 (정직 고지)

1. **봉인은 [구성]**: 협상에서 구매자·판매자의 "속내"는 AI가 온톨로지 축에 맞춰
   개연적으로 생성한 것 — 실제 회사의 진짜 전략이 아니다. 결론은 "이 조합이 유망한가"의
   **가설**이지 사실 판정이 아니다. (실제 속내 주입 경로: `--profiles` 수동 작성, 또는
   QA_agent 인터뷰 파이프라인으로 실측 후 변환)
2. **정답 앵커 없음**: 세션 provenance 는 전부 `simulated / outcome_anchor=false`.
   실제 성사/거절 결과로 검증된 판단이 아직 없다 — market_reply 축적이 그 시작이다.
3. **리서치 최신성**: 풀의 프로필은 수집 시점 기준. `--force` 로 갱신 가능.
4. **provenance=insufficient 회사**: 협상에 쓸 수는 있으나 프로필이 빈약하다는 경고가 뜬다.

## 8. 문제 해결

| 증상 | 조치 |
|---|---|
| `[오류] 회사 풀에 회사가 부족` | `company_research.py --kosdaq N` 으로 풀 채우기 |
| KRX 명단 다운로드 실패 | 재시도 자동(4회). 계속 실패 시 잠시 후 재실행 |
| 한글 깨짐 | `$env:PYTHONIOENCODING="utf-8"` 후 실행 |
| 스키마 위반 메시지 | 정규화 안전망이 대부분 보정. 지속 시 세션 json 의 해당 필드 확인 |
| 조정 규칙이 엉뚱한 도메인에 적용 | 규칙의 `scope.domain` 확인·수정 또는 `status: retired` |
| 429/일시 오류 | 호출별 3회 자동 재시도. 지속 시 잠시 후 재실행 |
