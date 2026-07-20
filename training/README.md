# EXAONE 관련도 스코어러 (training/scorer)

두 기업의 리서치 결과(또는 represent 온톨로지)를 입력하면 **관련도 0–10점**을 내는
경량 파인튜닝 모델. EXAONE 4.0에 **특수 토큰 + 선택적 unfreeze + FFN LoRA**로 학습한다.

## 이게 무엇을 대체하나 (정직한 프레이밍)

Judge를 대체하지 **않는다.** 기획서·`judge_cases` README가 "점수 폐기"를 명시한다 —
Judge의 산출은 숫자가 아니라 근거·리스크가 달린 구조화 판단이다. 이 스코어러는 그 앞단의
**랭킹 프리필터**다: 현재 `app/engine/retrieve.py`의 `_score()`가 bigram overlap 휴리스틱으로
후보를 거르는데, 그 자리를 학습된 스코어러로 바꿔 수천 쌍을 싸게 정렬하고 top-k만 Judge로
넘긴다. 대칭 점수(관련도)는 "1차 랭킹"엔 정당하고, 비대칭 보완성 판단은 그대로 Judge 몫이다.

## 데이터 형식

`RelatednessPair` JSONL 한 줄:
```json
{"a_id":"co-1","a_text":"A 리서치 결과…","b_id":"co-2","b_text":"B 리서치 결과…","score":7,"mode":"research","source":"claude-opus-4.8"}
```
`mode`: `research`(리서치 결과) | `ontology`(represent 온톨로지). 같은 파이프라인으로 둘 다 학습.

## 실행 순서

```bash
# 0) (로컬) GPU 없이 데이터·설정·규모 검증 — 불균형·누수·스텝수 확인
python -m training.scorer.dry_run --pairs data/pairs.jsonl

# 1) (서버) 의존성 — 서빙 레포와 분리
pip install -r training/requirements-train.txt

# 2) (서버) 재사용할 미학습 토큰 17개 고르기 (아래 '서버 확인사항' 참조)
#    → {"score":[11개 문자열],"struct":[6개 문자열]} 를 tokens.json 으로 저장

# 3) (서버) 학습
python -m training.scorer.train \
  --pairs data/pairs.jsonl \
  --model-id /path/to/EXAONE-4.0-32B \
  --tokens-json tokens.json \
  --output-dir training/runs/scorer

# 4) (서버) 추론 — 기댓값 readout으로 연속 점수
python -c "from training.scorer.infer import RelatednessScorer; \
  s=RelatednessScorer('/path/to/EXAONE-4.0-32B','training/runs/scorer'); \
  print(s.score('A 리서치…','B 리서치…'))"
```

## 설계 근거 (전문가 리뷰 반영)

- **untied unfreeze** — EXAONE 4.0-32B는 `tie_word_embeddings=False`. 점수 토큰은
  *예측*되므로 lm_head(출력) 행이, 구조 토큰은 *읽히므로* 입력 행이 학습돼야 한다.
  안전하게 17개 특수 토큰의 입력·출력 행을 모두 unfreeze한다(1.2B은 tied라 자동으로 한 행렬).
  `requires_grad`는 텐서 단위라 행 일부만 못 푸므로, **backward 훅으로 특수 토큰 외 행의
  grad를 0으로** 눌러 17행만 갱신한다.
- **기댓값 readout** — 11개 점수 토큰을 독립 분류로 두면 CE가 "6 대신 7"과 "0 대신 7"을
  똑같이 벌준다(순서성 소실). 추론 때 점수 토큰 로짓만 softmax → 기댓값 Σp·k로 연속·보정 점수.
- **계층 샘플링 + 회사 분할** — 랜덤이면 99%가 0–2점이라 모델이 "무조건 낮게" 찍고도
  정확해 보인다. 점수 버킷 상한으로 균형을 맞춘다. held-out은 **회사 단위 분리**(교차 쌍 폐기,
  누수 0)라 회사명 암기가 아닌 판단 구조 전이를 측정한다.
- **FFN LoRA(기본) + 어텐션 ablation** — "두 기업 비교"는 본질적으로 어텐션 연산이지만,
  사전학습 어텐션이 이미 in-context 비교를 하므로 FFN-LoRA로 읽어내는 베팅이 합리적이다.
  단 가정이므로 `--include-attention`으로 ablation을 돌려 실측하라.
- **미학습 토큰 평균 초기화** — 노이즈 대신 기존 임베딩 평균에서 출발(수렴 가속).

## 서버 확인사항 (torch 파트는 로컬에서 검증 불가 — 반드시 서버에서)

1. **미학습 토큰 선택** — `model_setup.find_unused_token_candidates(tokenizer)`로 예약/미학습
   토큰 후보를 뽑아 사람이 확인 후 17개 채택. voca 102,400 중 실제 미사용 슬롯을 써야
   기존 능력을 안 건드린다.
2. **LoRA 타겟 이름** — 기본 `gate_proj/up_proj/down_proj`(SwiGLU 확인). EXAONE4 구현의
   실제 서브모듈명과 다르면 PEFT가 "타겟 없음"으로 명확히 에러 내니 `model.named_modules()`로
   확인 후 조정.
3. **분산 학습(ZeRO-3)** — 행 마스킹 grad 훅은 DDP엔 그대로 되지만, DeepSpeed ZeRO-3는
   파라미터를 분할하므로 훅 동작을 검증하고 필요시 조정. 대안: `LoraConfig(modules_to_save=
   ["embed_tokens","lm_head"])`로 임베딩 전체를 저장(단 17행만이 아니라 전체가 trainable).
4. **저장/복원** — 어댑터(LoRA) + `special_token_weights.pt`(학습된 17행)를 함께 저장하고
   `infer.py`가 둘을 복원한다.

## 7일 GPU 버스트 매핑

| 일차 | 작업 | GPU |
|---|---|---|
| 1 | 서버 붙기·의존성·EXAONE 4.0 서빙 확인·미학습 토큰 선정 | 1노드 |
| 2–3 | **데이터 생성**(리서치 4000사 + 페어 스코어링) — 진짜 병목. **AXR팀 협의 후** | Claude API |
| 4 | dry-run 확정 → 7.8B/32B 스코어러 학습(각 ~1 GPU시간, 여러 config ablation) | 8×H100 |
| 5 | 기댓값 readout 평가 + held-out(OOD 회사) 랭킹 정확도 | 1노드 |
| 6 | `retrieve._score` 교체 실측 A/B(휴리스틱 대비) | 1노드 |
| 7 | 온톨로지 mode 학습 + 리포트 | 8×H100 |

컴퓨트는 남는다(스코어러 학습 자체는 ~1시간). 예산의 승부처는 **데이터 생성 품질** —
특히 하드 포지티브(고관련 쌍) 마이닝. dry-run 히스토그램이 쏠림을 그대로 보여준다.

## ⚠️ 정직성 경고

- **에코 챔버** — Claude 페어 점수로 학습 = Claude 증류. 상한이 Claude 제로샷 품질이고
  실제 성사 outcome 앵커가 0이다(`judge_cases` README와 같은 위험). 절대점수보다 **순위**로
  쓰고, 가능해지면 실제 성사 라벨로 교체한다.
- **API 대량 호출** — 4000사 리서치 + 페어 스코어링은 큰 비용. 데이터 생성 전 **AXR팀 협의**.
