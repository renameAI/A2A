# 프롬프트 파이프라인의 수학적 형식화

> A2A 매칭엔진의 LLM 프롬프트 파이프라인을 어텐션·RAG처럼 수식으로 형식화한다.
> 목적: **"입력할 때마다 출력이 예상 안 됨"의 정확한 이름을 붙이고, 그것을 줄이는 연산을 명세한다.**
>
> 방법론: 4개 수학 렌즈(확률/신경망/연산자/최적화)로 형식화한 뒤, 각 수식을 3인(엄밀성·정직성·실행가능성)이
> **적대적으로 검증**해 은유를 엄밀함으로 위장한 지점을 색출했다. 아래 수식의 라벨 **[엄밀]/[부분]/[은유]** 는
> 그 검증을 통과한 등급이며, 검증에서 잡힌 오류는 본문에 **⚠교정**으로 명시했다. 라벨을 신뢰하되 은유를 엄밀함으로
> 읽지 말 것 — 이 문서의 가치는 그 구분에 있다.

---

## 0. 한 문장 결론

> 프롬프트→출력의 사상 자체는 수식화 **불가능**(그건 frozen transformer $f_\theta$의 forward pass 전체다).
> 그러나 **$f_\theta$를 감싸는 파이프라인 구조**는 정확히 수식화되며, 사용자가 겪는 "예측 불가"는
> **조건부 출력 분산 $\mathrm{Var}[Y\mid X{=}x]$의 이분산성(heteroscedasticity)**이다.
> 따라서 "프롬프트 수식화"의 목적함수는 **$\mathrm{Var}[Y\mid x]$의 최소화**이고, 우리 코드의 계약 검증기·게이트·스키마는
> 전부 이 분산을 결정적으로 깎는 **연산자**로 형식화된다. 신경망 유비는 절반만 맞다(§3).

---

## 1. 진단 — 왜 매번 다른가

### 1.1 기본 확률모형 **[엄밀]**

각 LLM 호출은 결정론적 사상이 아니라 **1회 표집**이다:

$$ y \sim p_\theta(\,\cdot \mid x, \pi), \qquad x=\text{입력(청크·프로필)},\ \pi=\text{system 프롬프트},\ \theta=\text{frozen 가중치} $$

- 코드 대응: `llm.py:AnthropicExtractor.extract_json`(`messages.create`), `_OpenAICompatExtractor._chat`, `vision.py:GeminiBBoxExtractor.locate_batch`.
- 온도 $T>0$인 한 같은 $x$에도 $\mathrm{Var}[Y\mid x]>0$. 이게 예측불가의 1차 원천.

### 1.2 잠재 해석에 대한 전체 확률모형 **[부분]**

프롬프트는 "출력"이 아니라 **입력의 해석 $z$**를 좁힌다:

$$ p(y\mid x,\pi) = \sum_z p(y\mid x,z,\pi)\, p(z\mid x,\pi) $$

- $z$ = "이 회사명이 주체인가 고객사인가", "이 수치가 매출인가 목표인가" 같은 잠재 해석.
- 코드 대응: `prompts.py:HARD_RULES`(사실 고정), `EXTRACT_SYSTEM`의 '주체 고정' 블록, 5층 다층독해.
- **⚠교정**: 흔한 오기 $p(y\mid x,\pi)=\sum_z p(y\mid x,z)p(z\mid x,\pi)$ 는 $y\perp\pi\mid z$ 를 **무선언 가정**한다.
  정확히는 $p(y\mid x,z,\pi)$ 로 $\pi$ 를 유지해야 한다. 프롬프트는 해석 선택 $p(z\mid\cdot)$ 뿐 아니라 해석 조건부
  생성 $p(y\mid\cdot,z,\cdot)$ 에도 개입하기 때문.

### 1.3 프롬프트에 의한 해석 엔트로피 감소 **[부분]** — 가장 흔한 오용 지점

직관: 좋은 $\pi$ 는 해석 불확실성 $H(z)$ 를 줄인다. **그러나 부등식의 형태에 주의:**

$$ \mathbb{E}_{\Pi}\big[H(Z\mid X,\Pi)\big] \le H(Z\mid X) \qquad\text{(조건화는 \emph{기댓값}에서만 엔트로피를 줄인다)} $$

- **⚠교정**: 점별 부등식 $H(z\mid x,\pi) < H(z\mid x)$ 는 **일반적으로 거짓**이다. 특정 $\pi$ 는 특정 값의
  엔트로피를 **키울 수도** 있다(예: 잘못 좁힌 규칙이 오히려 모호성 유발). "좋은 프롬프트가 평균적으로 해석을 좁힌다"는
  참이지만, 그건 $\Pi$ 에 대한 기댓값 명제다. ML 이론에서 이 구분은 필수 — 설계 희망을 정리로 위장하면 안 된다.

### 1.4 이분산성 — 사용자 불만의 정확한 형식 **[부분]**

출력 분산은 상수가 아니라 **입력 $x$ 에 의존**한다:

$$ \sigma^2(x) := \mathrm{Var}[Y\mid X{=}x], \qquad \sigma^2(x)\ \text{는}\ x\ \text{마다 다르다 (이분산)} $$

- provenance$(x)$ 로 $\sigma^2(x)$ 를 층화할 수 있다: `stated`(자료에 명시) → 낮음, `inferred`·`ask`(4~5층 추론) → 높음.
- 코드 대응: `prompts.py:_FIELD.provenance` enum(`stated/inferred/ask`), `open_questions`의 `conf<0.6` 판정.
- **이것이 "입력할 때마다 예상 안 됨"의 정확한 이름이다**: 전역 baseline 분산 + $x$-의존 이분산 성분. 특히 후자가
  "어려운 입력(추론이 깊게 필요한 자료)"에서 커진다.

### 1.5 ⚠ 정직한 메타-경고: run-to-run 분산은 **현재 계측되지 않는다**

- 위 진단의 모든 "$\sigma^2$ 가 크다 / 이 항이 지배적이다"는 주장은 **현재 코드에서 측정된 바 없다**.
  `vision.py`는 토큰(`tokens_used`)·거부율(`rejected`)은 실측하지만, **동일 입력 재실행 간 출력 분산은 어디서도 재지 않는다.**
- 적대적 검증의 핵심 지적: **"단일표본 VLM 확률성이 예측불가의 지배항"이라는 인과 순위는 미계측 상태의 단정**이다.
  → 따라서 **첫 번째 실행 레버(§5 L0)는 분산 계측 하네스**다. 재지 않는 것은 줄일 수 없다.

---

## 2. 파이프라인의 두 얼굴 — 확률적 생성 vs 결정적 사영

### 2.1 함수 합성? — Markov 커널 합성 **[부분]**

$$ f = f_{\text{negotiate}} \circ f_{\text{compose}} \circ f_{\text{judge}} \circ f_{\text{retrieve}} \circ f_{\text{represent}} $$

- 코드 대응: `router.py` onboard→represent → match→retrieve → judge → compose → negotiate.
- **⚠교정**: 각 $f_i$ 는 **결정론적 함수가 아니라 확률적 사상(Markov kernel)**이다. $\circ$ 기호로 합성하되
  "$Y_i = \text{LLM}(\pi_i;X_i)$ 는 표집"이라고 해놓고 함수처럼 취급하면 자기모순. 정확히는 커널 합성
  $f = f_5 \ast f_4 \ast \cdots \ast f_1$ 이며, 선형 사슬을 'DAG'로 부풀리지 말 것(실제로는 체인).

### 2.2 가장 엄밀한 부분 — 질문핀 파이프라인의 연산자 대수 **[엄밀]**

여기가 이 문서에서 **가장 단단한 수학**이다. 결정적 코드라 완전히 형식화된다.
`_run_question_pinning`(router.py)은 VLM 후보 멀티셋을 사영 연산자들의 합성으로 거른다:

$$ \pi_{\text{pins}} = \mathrm{TopK} \circ \mathrm{Dedup} \circ \mathrm{Filter}_r \circ \mathrm{Filter}_g \circ \Pi_{\text{geom}} \circ \Pi_{\text{page}} \circ \Pi_{\text{index}} \;(C_{\text{raw}}) $$

| 연산자 | 정의 | 코드 |
|---|---|---|
| $C_{\text{raw}}$ | $\biguplus_{b}\biguplus_{(n,\text{img})\in b} \mathrm{VLM}(\text{img},Q)$ (멀티셋 합 $\uplus$) | `for batch…for item in vision.locate_batch` |
| $\Pi_{\text{index}}$ | $\{x: x.\text{qi}\in\mathbb{Z},\ 0\le x.\text{qi}<n\}$ | `router.py` 인덱스 폐기 |
| $\Pi_{\text{page}}$ | $\{x: x.\text{page}\in \text{batch\_page\_nos}\}$ | 배치 밖 페이지 폐기 |
| $\Pi_{\text{geom}}$ | $\{x: \text{validate\_box}(x.\text{box})\}$ (순서·범위·변$\ge4$·면적$\le5{\times}10^5$) | `vision.validate_box` |
| $\mathrm{Filter}_g$ | $\{x: g=\text{None}\ \lor\ g\ge0.6\}$, $g=\dfrac{|T_3(q)\cap T_3(p)|}{|T_3(q)|}$ | `grounding_score`, `GROUND_THRESHOLD` |
| $\mathrm{Filter}_r$ | $\{x: r\ge0.5\}$ | `REL_THRESHOLD` |
| $\mathrm{Dedup}$ | 키 $(q,\text{asset},\text{page})$ 별 $\arg\max_x s(x)$ | `best` dict |
| $\mathrm{TopK}$ | 질문별 상위 $K{=}2$ ($s$ 내림차순) | `_PINS_PER_QUESTION` |

- **결합 점수** $s(x) = r(x)\cdot g(x)$, $g:=0.75$ if `grounding is None` (검증불가 보정). `vision.pin_score`.
- **기수 단조 축소 사슬 [엄밀]**: $|C_{\text{raw}}| \ge |\Pi_{\text{index}}| \ge \cdots \ge |\pi_{\text{pins}}| \le n_Q\cdot K$.
  → `router.py`의 정직 집계 로그(`n_rej/n_dedup/n_capped`)가 이 사슬의 각 단차를 실제로 찍는다.
- **멱등성 [엄밀]**: $\Pi_{\text{geom}}\circ\Pi_{\text{geom}}=\Pi_{\text{geom}}$, $\mathrm{Dedup}^2=\mathrm{Dedup}$ 등. 고정 술어 재적용이라 성립.

**⚠교정 2건** (적대적 검증이 잡음):
1. **재현성 조건**: "$C_{\text{raw}}$ 고정 ⇒ 핀 완전 재현"은 **두 단서** 하에서만 참이다.
   (a) `Dedup`/`TopK`의 $\arg\max$·top-$K$ 가 **동점(tie)에서 후보 순서에 의존** → tie-break 순서가 고정돼야 함.
   (b) `evidence_id=uuid4()`·`thread ts=now()` 는 매 실행 새로 찍힌다 → **비의미(non-semantic) 필드는 재현 안 됨**.
   정확한 명제: *의미 내용(question·box·page·quote)은 $C_{\text{raw}}$+tie-break 고정 시 재현되나, id·timestamp는 아니다.*
2. **집합의 분산은 미정의**: 아래 §2.3의 총분산 논의에서 $\mathrm{Var}[\text{pins}]$ 를 집합값 객체에 문자 그대로 쓰면
   타입 오류다. 실제 척도는 지시벡터화하거나 Jaccard/대칭차 $|Y_1\triangle Y_2|$ 로 정의해야 한다.

### 2.3 총분산은 "분해"지 "감소"가 아니다 **[부분]**

$$ \mathrm{Var}[Y] = \underbrace{\mathbb{E}_A\big[\mathrm{Var}(Y\mid A)\big]}_{\text{조건부 잔차}} + \underbrace{\mathrm{Var}_A\big(\mathbb{E}[Y\mid A]\big)}_{\text{생성단계가 \emph{추가}하는 항}} $$

- **⚠교정**: 이건 **항등식(분해)**이지 감소 정리가 아니다. 확률적 스테이지 $A$ 를 추가하면 둘째 항이 **오히려 늘어난다.**
  "정규화층이 없어서 5단 합성으로 분산이 뒤로 갈수록 증폭되고 말단이 최대"라는 주장은 **비정리(거짓)**다:
  - 합성은 분산을 키울 수도 **줄일 수도** 있다. `f_judge`의 4-범주 enum 이산화는 분산을 유계 집합으로 **붕괴**시킨다.
  - LayerNorm/BatchNorm 부재를 원인으로 지목하는 건 **범주 오류**(정규화는 표본내 피처 스케일링이지 run-to-run
    표집분산 상계 장치가 아니다).
- **올바른 그림**: 파이프라인은 **분산 주입 단(확률적 LLM 표집) ↔ 분산 붕괴 단(하드 게이트·enum·사영)의 교대**다.
  예측불가는 "구조가 신경망이라서"가 아니라 **"판단이 결정적 코드 게이트가 아니라 확률적 LLM 게이트에서 일어나고,
  그 표집분산을 붕괴시킬 결정적 사영이 그 지점에 없기 때문"**이다. → 소재지가 명확히 국소화된다(§5).

---

## 3. 신경망 유비의 정직한 대차대조표

"프롬프트를 신경망 구조로"라는 아이디어의 **최종 판정**: 구조적 유비로는 유용하나, 대부분 **은유**이고
엄밀 대응은 **결정적 게이트 층**에 국한된다.

| 유비 | 판정 | 근거 / ⚠교정 |
|---|---|---|
| 파이프라인 = 커널 합성 $f=\ast f_i$ | **[부분]** | 합성은 실재. 단 $f_i$ 는 함수 아닌 Markov 커널, 체인이지 DAG 아님 |
| LLM 호출 = 확률적 활성, 프롬프트 = "가중치" | **[은유]** | `frozen`·$\mathrm{Var}>0$ 만 참. $\pi$ 를 "weight"라 부르는 건 범주 오류(gradient로 안 바뀜) |
| 하드 임계 게이트 $g(x)=x\cdot\mathbb{1}[s(x)\ge\tau]$ = ReLU/하드스레숄드 | **[엄밀]** | `REL/GROUND_THRESHOLD`, `_check_minimum`의 곱형 AND 게이트가 정확히 이 형태 |
| 최소 프로필 = 곱형 AND 게이트 $\prod_i \mathbb{1}[\cdot]$ | **[엄밀]** | `represent.py:_check_minimum` |
| 핀 결합점수 $s=r\cdot g$ + top-$K$ = "어텐션 유사 선택" | **[부분]** | $\arg\max$·top-$K$ 선택은 실재. 단 softmax·$\sqrt d$ 정규화 없음 |
| retrieve 보완성 = query–key 내적 | **[은유]** | 보완성은 유사도가 아니며 부호·기하가 어텐션과 다름. `retrieve.py` 미독 상태의 단정은 근거 부족 |
| UAT(보편근사) 적용 | **[은유·반증]** | UAT 전제(폭 증가·학습가능 weight·연속함수·결정론)가 **전부 깨짐** → 적용 불가. 이건 정직한 반증 |
| 역전파 | **[은유→부분]** | frozen $f_\theta$ 통과 gradient는 **없음**. 갱신되는 건 $\pi$ 가 아니라 입력 $X$(dialogue). Textual-gradient(TextGrad/DSPy) 유사물 |

**요지**: 신경망 유비에서 **엄밀하게 살아남는 건 "게이트 = ReLU류 하드 임계"뿐**이다. 나머지(가중치·어텐션·UAT·역전파)는
구조적 영감일 뿐 수학적 동치가 아니다. **그리고 그 살아남은 게이트가 바로 분산을 줄이는 실제 장치다** → §4.

---

## 4. 분산축소 정리 (교정된 통계)

우리 코드에서 $\mathrm{Var}[Y\mid x]$ 를 **실제로** 깎는 연산과, 그 통계적 정확성:

### 4.1 json_schema = 지지집합 절단 **[부분]**

$$ p(y\mid x,\pi,S) \propto p(y\mid x,\pi)\cdot \mathbb{1}[y\in S] $$

- enum 필드는 $|S|<\infty$ ⇒ **구조적 분산 유계**. 코드: `_VALUE_PROPS`, `BBOX_SCHEMA`, provenance enum.
- **⚠교정**: 이를 전역 재정규화 $p\cdot\mathbb1[y\in S]/Z$ 로 쓰면 이상화다. 실제 문법제약 디코딩은
  **토큰별 국소 정규화**(local, autoregressive)라 전역 절단과 다르다. 그리고 **자유문자열 필드에 `maxLength`/`pattern`을
  걸어 "$|S|<\infty$ ⇒ 분산 유계"라 주장하는 건 공허**하다 — 지지집합이 유한하지만 천문학적이라 의미 분산은 불변.

### 4.2 sanitize = 결정적 사영 **[엄밀]**

$$ y_{\text{final}} = \Pi(y),\quad \mathrm{Var}[\Pi(y)\mid \text{문자군 고정}] = 0,\quad \Pi\circ\Pi=\Pi\ (\text{멱등}) $$

- 코드: `llm.py:sanitize/_clean_text/_GARBAGE_CHARS`. CJK·제어문자 제거는 결정적이라 그 축의 분산을 0으로.

### 4.3 self-consistency 다수결 — **현재 미구현, 그리고 σ²/n은 틀림** **[은유→구현대상]**

여러 표본을 모아 집계하면 분산이 준다. **그러나 집계 대상별로 통계가 다르다** (적대적 검증의 핵심 교정):

| 출력 유형 | 올바른 집계 | 분산/집중 |
|---|---|---|
| 연속 스칼라 (드묾) | 표본평균 $\bar y_n$ | $\mathrm{Var}[\bar y_n]=\sigma^2/n$ **(이때만 σ²/n 성립)** |
| 범주형 `decision` (enum) | 다수결 mode | **지수적 집중** $P(\text{mode}\ne y^\ast)\le e^{-c n}$ (Hoeffding), **σ²/n 아님** |
| 중앙값 box 좌표 | median | 점근분산 $\approx \frac{\pi}{2}\sigma^2/n$ (가우시안), σ²/n 아님 |
| 자유문자열 서술 | "대표 표본 1개" | **분산 감소 0** (평균 낼 수 없음) |

- **⚠교정**: `_retry_json`(llm.py)은 다수결이 **아니라** "첫 유효 표본 채택"이라 분산 불변이고, 줄어드는 건
  파싱 실패율($p\to p^2$)뿐이다. 진짜 self-consistency는 **현재 코드에 없다** → 구현하려면 위 표의 올바른 통계로.
- 실무 결론: **`judge`의 범주형 결정에 $k$-표본 다수결**을 쓰면 재현성이 $k$ 에 대해 **지수적으로** 오른다(σ²/n보다 훨씬 강함).
  자유서술에는 투표가 무의미하니 결정 스칼라와 서술을 **분리**해야 한다(§6).

### 4.4 온도 — 낮추면 줄지만 0은 아니고, 주경로엔 못 걸 수도 **[부분]**

- 온도 $T\!\downarrow$ ⇒ $H(y)\!\downarrow$, $\mathrm{Var}\!\downarrow$ (**단조**). **⚠교정**: $\sigma^2\propto T^2$ 같은 깔끔한 2차 법칙은
  다토큰 자기회귀 생성엔 **없다**(단일 소프트맥스 스케일이 아니라 이산 분기가 지배).
- **⚠교정**: `temperature=0` 도 호스팅 모델(Gemini)에서 **결정성 보장 안 됨**(배치추론·MoE 라우팅·부동소수 리덕션 순서).
  또한 reasoning 모델(Claude adaptive thinking, EXAONE)은 API가 $T{=}1$ 을 강제하는 경우가 흔하다 → 온도 레버가
  정작 주 경로(`AnthropicExtractor`, Friendli)에 안 걸릴 수 있다. **배선 여부부터 확인**해야 하는 partial 레버.

---

## 5. 실행 로드맵 (측정가능한 레버만) — **L0~L3 구현 완료**

우선순위 순. 각 레버는 **계측 지표**를 동반한다("못 재면 못 줄인다"). L0~L3는 구현·테스트 완료(아래 파일 참조).

**✅ L0 · 분산 계측 하네스 (선행 필수).** → [`app/eval/variance.py`](../app/eval/variance.py), [`scripts/measure_variance.py`](../scripts/measure_variance.py)
동일 입력을 $m$ 회 재실행해 출력 분산을 재는 하네스. 필드 유형별 지표: 범주형 다수결 일치율, 스칼라 표본표준편차·변동계수,
집합값 평균 Jaccard $\overline{|Y_i\cap Y_j|/|Y_i\cup Y_j|}$, 자유문자열 토큰 Jaccard(프록시). `variance_report(outputs)` 가
필드별 `stability∈[0,1]` 과 전체 안정성을 낸다. `mode()`·`agreement_rate()` 는 **L2가 재사용**(dead code 아님).
실행: `python scripts/measure_variance.py --skill represent --m 5 --input examples/…json`.

**✅ L1 · `open_questions` 5공리 코드 집행.** → [`app/engine/represent.py`](../app/engine/represent.py) `enforce_question_axioms`
5공리를 provenance 근거로 결정적으로 집행: ①원자성(대상 필드별 1개) ②판정가능성·③비중복성(이미 결정된 필드 질문 폐기 —
`stated` 또는 conf$\ge0.6$ inferred) ④정보가치 정렬(최소프로필 필드 우선) ⑤예산($\le5$). `represent()`가 양 경로(mock/LLM)에
균일 적용하고 폐기 사유를 정직 집계 로그로 남긴다.

**✅ L2 · 범주형 결정에 $k$-표본 다수결 + 일치율.** → [`app/engine/judge.py`](../app/engine/judge.py) `_vote_llm_judge`
`judge`의 `decision` enum에 §4.3의 **지수적 집중** 다수결(`JUDGE_SAMPLES=k`). 대표 표본 채택 + `sample_agreement` 부착.
**일치율 자체가 보정 신호.** k=1(기본)이면 미계측(`None`) — 측정 없는 확신을 만들지 않는다.

**✅ L3 · 소프트 판단 → 하드 코드 게이트.** → [`app/engine/judge.py`](../app/engine/judge.py) `_apply_consistency_gate`
두 이전: (a) `confidence_band` 를 일치율에서 **결정적으로 도출**(LLM 자가보고 신뢰도=미보정을 코드 규칙으로 대체),
(b) 일치율 $<\tau$(`JUDGE_AGREEMENT_THRESHOLD`) → `needs_human=True` + 자동 '추천/조건부'를 `hold` 로 캡(저합의 자동추천을
deal-breaker 게이트와 같은 하드 성격으로 차단). `terminate` 는 캡 대상 아님(보류로 완화 금지).

**L4 · 온도 배선 + 계측 (설정 가능한 경로만).**
`llm.py:_chat` payload에 `temperature` 인자 추가(현재 없음 → 제공자 기본 $\approx1$). **단 §4.4의 한계 명시**: 0으로 못 내리는
경로가 있고 0도 결정성 보장 안 함. 배선 후 L0 하네스로 실제 분산 감소를 **측정**해서만 채택.

> **버리는 레버** (적대적 검증에서 탈락): ① 임계 **hysteresis**(밴드 스냅) — 독립 실행엔 참조할 이전 상태가 없어 **범주 오류**.
> ② 자유문자열 → enum 강제 — 의미 분산 안 줄고 **교차도메인 추상화(제품 핵심)를 파괴**. ③ 그라운딩·관련도 임계 상향으로
> "judge 분산" 축소 — 그 임계는 **vision 질문핀 경로**일 뿐 judge와 무관(**서브시스템 오인**).

---

## 6. 워크드 예제 — `judge` 결정 프롬프트 하드닝

가장 예측불가한 건 **정성적 판단 프롬프트**다. `judge`의 결정을 수치 계약으로 하드닝한다.
**핵심 설계**: 결정 가능한 스칼라(분산 축소 대상)와 자유서술(축소 불가)을 **분리**한다.

### Before (현재 — 느슨)
```
JUDGE_SYSTEM: "... 양측의 상을 재구성하고, 진행할지 보류할지 판단하라."
→ decision: "진행" | "보류"   (단일 표본, 임계 불명, 일치율 미측정)
```
문제: 같은 입력에 진행/보류가 갈린다. 왜 갈렸는지, 얼마나 확신인지 계측 불가.

### After (수치 계약 + 분리 + 다수결)
```
JUDGE_SYSTEM 출력 계약:
  fit_score ∈ {0.0, 0.1, …, 1.0}   # 11점 고정 스케일. 앵커 명시:
     0.0 = deal-breaker 존재  |  0.5 = 보완성은 있으나 실행 리스크 미해소
     1.0 = 보완성·의향·타이밍 3축 모두 충족
  decision = 1[fit_score ≥ τ],  τ=0.6 (코드 게이트, 프롬프트 아님)
  rationale: 자유서술(감사·설명용, 판정에 미사용)   ← 분산 축소 대상에서 제외
```
```
코드(제안):
  scores = [judge_once(...).fit_score for _ in range(k)]   # k=5, §4.3
  fit = median(scores)                                       # 스칼라 → median
  decision = fit >= 0.6                                      # 하드 코드 게이트(L3)
  agreement = mean(|s - fit| <= 0.1 for s in scores)         # 보정 신호(L2)
  if agreement < 0.6: route_to_human()                       # 저일치 = 사람에게
```

**효과 (L0로 측정할 것)**:
- 결정이 프롬프트의 소프트 판단 → **고정 임계 $\tau$ 의 코드 게이트**로 이동(재현성↑).
- $k$-표본으로 스칼라 분산 $\to \approx\frac{\pi}{2}\sigma^2/k$, 결정 재현성은 임계에서 멀수록↑.
- **일치율이 자동 보정 신호** — 경계 케이스를 스스로 사람에게 넘김(hallucinated confidence 방지).
- 자유서술은 그대로 두어 **교차도메인 표현력(제품 핵심)을 파괴하지 않음**.

> 주의: `fit_score`를 11점으로 **이산화**해도 그 자체가 "예측불가"를 없애진 않는다(§4.1의 국소정규화 한계).
> 실제 감소는 **다수결·median·코드 게이트·일치율 라우팅의 조합**에서 오며, 그 크기는 **L0 하네스로 측정해야만** 안다.

---

## 7. 정직한 한계

1. **근본적 확률성은 못 없앤다.** $y\sim p_\theta(\cdot\mid x,\pi)$ 의 $p_\theta$ 는 frozen이고 우리는 표본만 본다.
   모든 레버는 분산을 **줄일** 뿐 0으로 만들지 못한다(온도 0도, 스키마 강제도).
2. **예측불가 ≠ 부정확.** 투표는 mode 주변 분산만 줄이지 **계통 편향**은 못 줄인다. 낮은 분산이 곧 정답은 아니다.
3. **enum 강제의 대가.** 정성 필드를 유한 집합으로 가두면 분산은 줄지만 이 엔진의 존재 이유(회사의 상을 자유롭게
   재구성)를 깎는다. 그래서 §6은 **결정 스칼라만** 조이고 서술은 남긴다.
4. **"지배항" 주장은 L0 이후에만.** 어느 분산원이 큰지는 계측 전엔 미확정. 이 문서의 우선순위(§5)조차 L0 결과로 재정렬될 수 있다.
5. **집합값 출력의 분산.** 핀 같은 집합값 $Y$ 의 "분산"은 메트릭(Jaccard/대칭차) 위에서만 정의된다 — 스칼라 $\mathrm{Var}$
   기계를 문자 그대로 붙이지 말 것.

---

## 부록 — 검증 방법론

4개 렌즈 × (형식화 1 + 적대적 검증 3) = 16개 에이전트가 **실제 코드(`llm.py`/`prompts.py`/`vision.py`/`router.py`/
`represent.py`)를 읽고** 수식을 코드 심볼에 정박시킨 뒤, 3인 검증관(엄밀성·정직성·실행가능성)이 각 수식을
`rigorous/partial/metaphor/wrong`으로 적대적으로 판정했다. 본문의 **⚠교정**은 그 검증에서 다수가 `metaphor/wrong`으로
판정하거나 BS-flag를 단 지점이며, 그 교정을 반영해 살아남은 것만 [엄밀]/[부분]으로 표기했다.
전 렌즈 종합 판정: **"방향은 옳은 직관 + 여러 개의 진짜 수학 오류"의 혼합** — 그래서 교정이 문서의 본체다.
