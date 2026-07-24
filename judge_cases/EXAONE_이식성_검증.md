# new-judge의 K-EXAONE 이식성 검증

> 목적: 박사님 judge가 Gemini 없이 **소버린 모델(K-EXAONE)** 에서 작동하는지 실측.
> 측정 2026-07 · 프리셋 6종 각 1회 · 박사님 코드는 **수정하지 않음**.

## 1. 방법

`negotiation_sim.chat()`이 모든 LLM 호출의 단일 관문이므로 이 함수만 런타임
monkeypatch로 K-EXAONE(Friendli dedicated)에 연결했다. 원본 파일은 변경하지 않았고,
`OUT_DIR`을 스크래치로 돌려 기존 Gemini 산출물을 보존했다.

- 판정·발화 모델: `gemini-3.1-pro-preview` / `flash-lite` → **K-EXAONE-236B**
- 봉인(private)은 프리셋 고정값을 사용 — 생성 변동을 통제해 **모델 차이만** 비교
- 검증: 박사님 `negotiation_ontology.schema.json` (jsonschema)

## 2. 결과

| 시나리오 | Gemini 결정 | **K-EXAONE 결정** | 스키마 | Gemini outcome | K-EXAONE outcome |
|---|---|---|---|---|---|
| baseline | conditional | **conditional** ✅ | PASS | hold | deal_structured |
| structural | terminate_structural | **hold** ❌ | PASS | walk_away_structural | no_agreement |
| values | terminate_structural | **conditional** ❌ | PASS | walk_away_values | no_agreement |
| hold_reverse | hold | **conditional** ❌ | PASS | hold | poc_agreed |
| recommend_clean | recommend | **conditional** ❌ | PASS | meeting_agreed | poc_agreed |
| recommend_inbound | conditional | **conditional** ✅ | PASS | poc_agreed | poc_agreed |

| 지표 | 결과 |
|---|---|
| 완주 | **6/6** (LLM 호출 220회, **실패 0**) |
| 스키마 통과 | **6/6** |
| BB·SB 축 채움 | **6/6** (각 10/10, 누락 0) |
| **결정 정확 일치** | **2/6** |
| 거래 불성립 여부(이진) | 6/6 (단 §4 참조 — 성긴 지표) |
| 결정 다양성 | K-EXAONE **2종** vs Gemini **4종** |
| 소요 | 305~443초 (Gemini 2~10분과 동급) |

## 3. 해석

**공학적 이식은 성공했다.** 스키마 6/6, 호출 실패 0, 20축 전부 채움, 라운드 정상 종료.
생성 메일도 `BB6`(실행 게이트)·`BB9`(결정 구조)를 근거로 인용하는 등 온톨로지를 실제로
사용했다. 즉 **온톨로지·프롬프트·스키마·안전망은 모델 독립적**임이 확인됐다.

**그러나 판단 라벨은 재현되지 않았다.** K-EXAONE의 결정이 6건 중 5건 `conditional`로
쏠렸다. Gemini가 시나리오 설계대로 4종(recommend·conditional·hold·terminate_structural)을
발현한 것과 대비된다. 박사님 보고서 §2.2의 "결정 커버리지 5/5종 발현"이 K-EXAONE에서는
**2종으로 축소**된다.

**다만 방향은 감지한다.** 철수하도록 설계된 두 시나리오(structural·values)에서 판매자
outcome이 모두 `no_agreement`로 갔다. 즉 "이 거래는 성립하지 않는다"는 도달하나, 그것을
**구조적 철수 / 가치 철수로 분류하지 못한다.** values 시나리오의 착취 감지(SB9 — 시안
선요청 + 로열티 거부)가 발화되지 않은 것이 대표적이다.

요약하면 **파이프라인은 이식되고 판단의 해상도는 이식되지 않는다.**

## 4. 한계

1. **n=6, 각 1회.** 박사님 보고서 §2.3은 Gemini도 세부 outcome·라운드가 반복마다
   변동함을 보고했다(핵심 결정은 3/3 안정). K-EXAONE의 반복 안정성은 미측정이다.
2. **"거래 불성립 여부 6/6"은 성긴 지표다.** 6건 중 4건이 비철수 대역이라 상수 응답으로도
   4/6이 나온다. 유의미한 부분은 철수 설계 2건이 모두 불성립에 도달했다는 점뿐이다.
3. **정답 앵커 부재는 그대로다.** 여기서 "Gemini 결정"은 정답이 아니라 **기준선**이다.
   어느 쪽이 옳은지는 실제 성사/거절 데이터 없이는 판정할 수 없다.
4. 봉인을 프리셋으로 고정해 모델 차이만 봤다. 풀 모드(봉인 생성)에서는 결과가 다를 수 있다.

## 5. 세 시스템 대조 (같은 6쌍)

| 시나리오 | Gemini judge | K-EXAONE judge | **스코어러 1.2B(E9)** |
|---|---|---|---|
| baseline | conditional | conditional | 3.76 |
| structural | terminate_structural | hold | 3.65 |
| values | terminate_structural | conditional | 4.47 |
| hold_reverse | hold | conditional | 3.50 |
| recommend_clean | recommend | conditional | 4.28 |
| recommend_inbound | conditional | conditional | 3.83 |
| **단위비용** | 2~10분 · $0.2~0.4 | 5~7분 · Friendli | **36 ms · $0** |

E9 점수는 3.50~4.47의 좁은 대역에 있어 judge 결정과 정렬되지 않는다. 예상된 결과다 —
E9는 공개 정보 기반 **보완성 랭킹**용이고, 철수 사유(카피 의도·캐파 제약)는 봉인에만
있어 원리적으로 볼 수 없다. 세 시스템 중 **판단 해상도는 Gemini judge가 유일하게 확보**하고
있으며, E9는 그 앞단에서 후보를 20,100쌍 → top-k로 줄이는 역할이다.

## 6. 결론과 권고

1. **"Gemini 없으면 무쓸모"는 아니다.** 온톨로지·스키마·안전망·피드백 루프·기존 201사
   리서치는 모델과 무관한 자산이고, 판정부는 K-EXAONE에서 오류 없이 구동된다.
2. **다만 지금 상태로 모델을 교체하면 판단 품질이 떨어진다.** 결정이 conditional로
   쏠려 철수·추천을 구분하지 못한다. 교체 전에 다음이 필요하다:
   - 결정 라벨 판정을 프롬프트에서 **결정론적 게이트로 이전**(우리 `app/engine/judge.py`가
     쓰는 방식 — 축 판정 → 코드가 결정 산출). 모델 의존도를 낮추는 표준 수법이다.
   - 또는 판정 단계만 few-shot 앵커를 추가해 라벨 분포를 교정.
3. **리서치는 여전히 Gemini가 필요하다.** 검색 그라운딩은 EXAONE에 없고, 로컬 EXAONE
   생성은 환각이 실측됐다(EXPERIMENTS §5.4). 기존 201사는 저장돼 있어 재생성 불요.
4. 따라서 현실적 배치는 **"리서치=Gemini, 판정=Gemini(현행 유지) 또는 게이트 보강 후
   K-EXAONE"**. 소버린 트랙 요건이 강제되면 2번 보강이 선결 과제다.

---

## 부록. 재현

```bash
# 박사님 코드 무수정 — chat()만 K-EXAONE으로 교체, 출력은 스크래치로 격리
FRIENDLI_TOKEN=... FRIENDLI_ENDPOINT_ID=... \
SCN=baseline SCRATCH_OUT=/tmp/out \
python judge_cases/run_judge_exaone.py
```
