---
name: lead-discovery
description: 기업 자료(IR PDF 등)를 읽고 프로필을 만든 뒤, 보완성 기준으로 리드 후보를 찾고 아웃리치 메일 초안까지 만드는 내부 운영용 워크플로우. "리드 찾아줘", "이 회사 아웃리치", "lead discovery" 요청에 사용.
---

# Lead Discovery — Claude가 모델, 엔진 코드가 심판

A2A 매칭엔진의 LLM 자리(K-EXAONE)를 이 세션의 Claude가 대신한다.
점수·임계·경쟁사 강등·의도 티어 같은 **결정은 전부 기존 엔진 코드**가 내린다
(축 판정=모델, 결정=코드 — 엔진 철학 그대로).

용도 경계: MYSC 내부·대행 업무 전용. 고객이 직접 쓰는 SaaS 서빙 경로에는
이 스킬을 넣지 않는다(구독 계정은 상용 백엔드가 아니다).

## 인터랙션 원칙 — unknown-unknown을 양쪽에서 줄인다

이 스킬의 대화 상대는 자기 회사를 제일 잘 알지만 **무엇을 말해야 하는지 모르고**,
AI는 무엇이 필요한지 알지만 **회사 내부 사정을 모른다**. 그래서:

- **웹 먼저, 질문은 나중**: 사용자에게 묻기 전에 WebSearch·WebFetch로 회사
  홈페이지·뉴스·채용공고를 먼저 찾아 채운다. 웹으로 알 수 있는 것을 사람에게
  묻는 것은 실례다. 웹에서 찾은 사실은 출처 URL과 함께 "웹에서 이렇게 확인했는데
  맞나요?" 형태로 **확인만** 받는다.
- **질문은 AskUserQuestion으로, 한 번에 하나**: 자료·웹 모두로 알 수 없는 것만
  묻는다. 질문 5공리(원자성·판정가능성·비중복·정보가치 내림차순·예산 5개)를
  지키고, 선택지를 만들 수 있으면 4지선다로 제시한다 — 대표가 언어화하지 못한
  사실을 끌어내는 컨설턴트의 질문("타겟이 누구예요?"가 아니라 "지금까지 돈을 낸
  고객 중 가장 만족한 곳은 어디였고, 무엇 때문에 냈나요?").
- **사용자의 unknown-unknown 드러내기**: 검색 조건을 확정하기 전에, 사용자가
  고려하지 않았을 축을 선택지로 보여준다 — 제외 조건(경쟁사·규모 하한),
  구매자 유형(브랜드/CSR/공공/유통은 사는 이유가 다르다), 아웃리치 언어.
  "이 중 뭘 빼야 할까요?"가 "뭘 찾을까요?"보다 좋은 질문일 때가 많다.
- **AI의 unknown-unknown 드러내기**: 웹 리서치 중 판단이 갈리는 경계 사례
  (경쟁사인지 구매자인지, 제외 조건에 걸치는지)는 AI가 정하지 말고 근거와 함께
  사용자에게 넘긴다. 채점 결과의 "확인 필요" 표시가 그 자리다.

## 1단계 — Represent (프로필 5층 독해)

사용자가 준 자료(PDF는 Read로 직접 읽는다)를 다섯 겹으로 독해한다:
1층 표면(자료의 문장 그대로) → 2층 기능(누구의 어떤 결핍을 없애며 고객이 돈을
내는 이유 — 주어는 항상 고객) → 3층 경제(누가 언제 무엇에 어떤 구조로 지불) →
4층 전략(트랙션·레퍼런스 분포에서 지금 절실한 것 역추론) → 5층 양면(이 회사가
구매자로서 필요로 할 것).

규율 — 엔진의 HARD_RULES와 동일:
- 자료에 글자 그대로 있는 것만 사실. 없는 수치·연도·고유명사를 만들지 않는다.
- 모르는 것은 "미상". 추론은 추론이라 표시하고 확신도(0~1)를 붙인다.
- 순수 한국어 서술, 고유명사만 원어 허용.

산출: 아래 JSON을 `/tmp/lead_profile.json`에 Write. 스키마는 엔진 Profile과 동일
(basic{name,country,city,founded_year,industry}, description,
problem_solved/solution/target_customer{value,provenance,confidence,evidence_chunk_ids},
references[], traction, sell_value_props[], purchase_value_props[],
willingness_sell, willingness_purchase, portrait{identity,business_model,edge,
stage_narrative,assets,gaps,risk_signals}).
value_props enum: revenue_growth·cost_reduction·impact·problem_solving.
provenance enum: stated·inferred·ask.

핵심 필드(문제·솔루션·타겟) 중 자료로 알 수 없는 것은 ① 먼저 회사 홈페이지·뉴스를
WebSearch로 찾아 보강하고(출처 명시, provenance=inferred), ② 그래도 빈 것만
AskUserQuestion으로 묻는다 — 한 번에 하나, 답이 특정 필드를 확정하는 것만.
confidence 0.6 미만의 추론 필드도 진행 전에 같은 방식으로 확인받는다.

## 2단계 — 상대상 합성 + 검색 조건 확정

프로필의 gaps·stage_narrative를 반영해 "이상적 상대의 상" 한 문단을 쓴다.
나와 비슷한 회사가 아니라 **내 솔루션이 푸는 문제를 지금 겪는 상대**를 그린다
(상황·관찰 가능한 고통 신호·규모와 단계·지역). `/tmp/lead_synth.txt`에 Write.

합성문을 사용자에게 보여주고 검색 시작 전에 AskUserQuestion으로 확정한다.
이때 사용자가 놓쳤을 축을 함께 묻는다 — 제외 조건(경쟁사·최소 규모·계약 형태),
구매자 유형(브랜드/CSR/공공/유통), 아웃리치 언어. 상이 틀렸으면 여기서 고치는 게
후보 100건을 버리는 것보다 싸다.

## 3단계 — 후보 수집·채점 (결정은 코드)

후보는 두 모드 중 하나로 확보한다.

**웹 모드(기본 — 시장 PoC)**: WebSearch로 지역·업종·수요 신호를 검색해 후보를
모으고 `/tmp/lead_cands.json`에 Write한다. 형식:
`[{"name","country","industry","description","pain_signal","url"}]`.
pain_signal에는 **검색 결과 원문에서 실제로 관측한 문장의 요지만** 쓴다 —
관측하지 못했으면 빈 문자열로 두고, 지어내지 않는다. url은 반드시 실재 출처.
유망해 보이는 후보는 WebFetch로 원문 페이지를 열어 pain_signal을 검증·보강한다.

수집 중 판단이 갈리는 경계 사례(경쟁사인지 구매자인지, 제외 조건에 걸치는지,
같은 그룹 계열사인지)는 임의로 넣거나 빼지 말고 근거 요약과 함께
AskUserQuestion으로 사용자가 정하게 한다.

**풀 모드**: 엔진 저장소의 내부 후보 풀을 그대로 쓴다(`--candidates` 생략).

```bash
cd /Users/boram/a2a-matching-engine && .venv/bin/python \
  .claude/skills/lead-discovery/scripts/score_candidates.py \
  --profile /tmp/lead_profile.json --synth /tmp/lead_synth.txt \
  --region <지역|생략> --vps <value_props 쉼표구분> --k 5 \
  --candidates /tmp/lead_cands.json   # 웹 모드일 때만
```

출력 JSON의 순위를 그대로 쓴다 — Claude가 순위를 재조정하지 않는다
(의도 티어와 임계는 코드의 권한). `passes_threshold: false` 후보는
"임계 미만"임을 사용자에게 그대로 표시한다.

## 4단계 — 후보 인사이트 + 메일 초안

사용자가 고른 후보에 대해:
- 관측된 수요 신호(풀 레코드의 pain_signal 등 실재 데이터만)와
  요청 기업 솔루션의 연결점을 만든다. 근거 없는 수치·의도 단정 금지.
- 확인되지 않은 내용은 본문에서 빼고, 뺐다는 사실을 표시한다.
- 메일 제목·본문 2안(A/B). 발송하지 않는다 — 초안까지만, 발송은 사람.

## 보고

각 단계 산출물(프로필 요약·상대상·순위표·메일 초안)을 사용자에게 보여주고,
추론(inferred) 필드와 임계 미만 후보는 반드시 구분해 표시한다.
