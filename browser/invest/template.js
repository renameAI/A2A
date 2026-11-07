// 임팩트 펀드 투자심사보고서 틀 — 실무에서 쓰는 최종 보고서의 목차와 표 구성을 따른다.
//
// 섹션마다:
//   needs     쓰기 전에 있어야 할 정보(STORM식 사전 질문의 출발점). 없으면 질문으로 돌린다.
//   viewpoints 이 섹션을 읽는 사람의 관점 — 관점마다 질문을 만들게 한다(STORM 2402.14207)
//   rubric    예/아니오로 채점하는 항목(CheckEval 2403.18771, FinReportBench 2608.04374). 작성 모델과 다른 모델이 채점한다
//   vars      이 섹션이 숫자를 부를 때 쓰는 계산 변수(calc.js). 숫자는 이 변수로만 쓴다
//   pages     분량 목표(쪽) — 섹션마다 따로 부른다(LongWriter 2408.07055)
// 공통 규칙(COMMON)은 모든 섹션 채점에 더한다.
const COMMON = [
  { id: 'c_cite', q: '사실 문장마다 근거([f:…] 또는 {{var:…}})가 붙어 있는가' },
  { id: 'c_nonum', q: '근거 없는 숫자가 없는가(숫자는 {{var:…}} 또는 인용한 근거 안의 값만)' },
  { id: 'c_gap', q: '모르는 값은 지어내지 않고 [확인 필요]로 남겼는가' },
  { id: 'c_tone', q: '개조식(ul/li 목록·h4 소제목, 글 앞에 기호를 직접 쓰지 않음)과 "~함/~임" 종결로 인수인의견 톤을 지켰는가' },
  { id: 'c_scope', q: '이 섹션 범위 밖 내용을 쓰지 않았는가' },
];

const SECTIONS = [
  {
    key: 'cover', title: '표지 · 투자 요약', pages: 1,
    needs: ['회사 법인명·사업자번호', '한 줄 비전', '내부 IR·임팩트 심의·본투심 날짜', '투자 금액·계정(펀드)·방식', '심사 담당과 기여 비율(발굴/심사/사후관리)'],
    viewpoints: ['투자심의위원'],
    vars: ['invest_amount'],
    rubric: [
      { id: 'cv_terms', q: '투자 대상·금액(한글과 숫자)·계정·방식이 모두 있는가' },
      { id: 'cv_dates', q: '심의 일정 세 가지(내부 IR, 임팩트 심의위, 본투심)가 있는가' },
      { id: 'cv_owner', q: '심사 담당과 단계별 기여 비율의 합이 100%인가' },
    ],
  },
  {
    key: 'exec', title: 'Executive Summary', pages: 4,
    needs: ['투자 요약 표(대상·대표·방식·금액·재원·투자가치·주식수와 지분율·특이사항)', '사업 개요와 서비스 개요', '주목적/특수목적 투자 해당 여부', '투자 포인트(의도성·부가성·수익성)', '변화이론'],
    viewpoints: ['투자심의위원', '임팩트 심의위원', 'LP'],
    vars: ['invest_amount', 'pre_money', 'stake_post', 'shares_bought'],
    rubric: [
      { id: 'ex_table', q: '투자 요약 표의 숫자가 본문(투자 조건·밸류에이션)과 같은 변수로 적혔는가' },
      { id: 'ex_fit', q: '주목적(초기창업·소셜벤처)·특수목적(지역 등) 해당 여부를 근거와 함께 판정했는가' },
      { id: 'ex_points', q: '투자 포인트가 의도성·부가성·수익성 세 축으로 나뉘었는가' },
      { id: 'ex_toc', q: '변화이론(Problem → R&D → Output → Outcome → Impact)이 있는가' },
      { id: 'ex_balance', q: '좋은 점만이 아니라 핵심 리스크가 한 줄이라도 있는가' },
    ],
  },
  {
    key: 'impact_screen', title: '소셜벤처 판별표', pages: 1,
    needs: ['임팩트(미션·실행·규모·수준·지속성·종합)', '임팩트 시나리오(이해관계자·의도 A/B/C·규모·수준·지속성·리스크)', '사업성(기술·시장)'],
    viewpoints: ['임팩트 심의위원'],
    vars: [],
    rubric: [
      { id: 'is_rows', q: '판별 항목마다 본문 쪽수 또는 [확인 필요]가 적혔는가' },
      { id: 'is_abc', q: '이해관계자별 의도를 A(Avoid harm)/B(Benefit)/C(Contribute)로 분류했는가' },
      { id: 'is_market', q: '시장 기준(SAM 3,000억/SOM 1,000억 이상, CAGR 6% 이상)을 충족 여부로 판정했는가' },
    ],
  },
  {
    key: 'overview', title: 'I. 투자 개요 (기업 개요 · 투자 조건 · 손익 추정)', pages: 5,
    needs: ['기업 개요 표(설립일·자본금·액면가·업종·인증·직원수)', '투자 조건(단가·주식수·기업가치·우선권·전환·상환·사용 용도·제재)', '손익 추정: 회사 제시와 심사역 추정 두 표', '추정 의견(주요 가정·매출 추정·원가 추정)'],
    viewpoints: ['심사역', 'CFO', '준법감시'],
    vars: ['price_per_share', 'shares_bought', 'stake_post', 'pre_money', 'post_money'],
    rubric: [
      { id: 'ov_terms', q: 'RCPS면 배당·잔여재산 우선권, 전환 조건, 상환 조건이 모두 있는가' },
      { id: 'ov_two', q: '손익 추정이 회사 제시와 심사역 추정 두 줄로 나뉘었는가' },
      { id: 'ov_why', q: '심사역 추정이 회사 제시와 다른 이유(매출·원가 가정)를 항목별로 설명했는가' },
      { id: 'ov_math', q: '손익 표의 행끼리 맞는가(계산 검산 통과)' },
    ],
  },
  {
    key: 'company', title: 'II. 기업 현황', pages: 4,
    needs: ['연혁', '주주 현황(투자 전·후 주식수와 지분율)', '자본금 변동', '경영진·핵심 구성원', '요약 재무상태표와 손익계산서(실적)'],
    viewpoints: ['심사역', '사후관리 담당'],
    vars: ['stake_post'],
    rubric: [
      { id: 'co_cap', q: '주주 현황 표의 지분율 합이 100%이고 투자 후 지분율이 계산과 같은가' },
      { id: 'co_safe', q: 'SAFE·전환사채 등 미전환 증권이 있으면 따로 밝혔는가' },
      { id: 'co_team', q: '대표와 핵심 인력의 경력이 사업과 어떻게 이어지는지 적었는가' },
      { id: 'co_fin', q: '실적(Actual)과 추정(Estimate)을 구분했는가' },
    ],
  },
  {
    key: 'market', title: 'III. 시장 분석', pages: 9,
    needs: ['주요 가정(시장 규모를 어떤 수요로 추정했는지)', '전체·국가별 시장(출처와 연도)', '국내 시장', '경쟁 시나리오와 경쟁사 비교', '차별화 포인트'],
    viewpoints: ['심사역', '경쟁사', '고객'],
    vars: [],
    rubric: [
      { id: 'mk_src', q: '시장 수치마다 출처와 기준 연도가 있는가' },
      { id: 'mk_assume', q: '시장 추정의 주요 가정이 상자로 먼저 제시되었는가' },
      { id: 'mk_comp', q: '경쟁 시나리오(잠재 경쟁 포함)와 비교표가 있는가' },
      { id: 'mk_diff', q: '차별화 포인트가 정량 근거(비용·효율·인력 등)와 함께 있는가' },
    ],
  },
  {
    key: 'business', title: 'IV. 사업 분석', pages: 6,
    needs: ['사업 개요와 비즈니스를 통한 임팩트', '단기·중장기·장기 전략', '핵심 자원과 프로세스', '이해관계자 가치 제안(AS-IS / TO-BE)', '사업화 전략(단기·장기 비즈니스 모델)'],
    viewpoints: ['심사역', '고객', '협력사'],
    vars: [],
    rubric: [
      { id: 'bz_asis', q: '프로세스별 AS-IS / TO-BE 비교가 있는가' },
      { id: 'bz_stake', q: '이해관계자별 가치 제안이 A/B/C 분류와 함께 있는가' },
      { id: 'bz_model', q: '수익 모델(누가 무엇에 돈을 내는지)이 분명한가' },
    ],
  },
  {
    key: 'impact', title: 'V. 투자 적합성 및 임팩트', pages: 4,
    needs: ['주목적/특수목적 판정', '투자 포인트', '변화이론과 분석(임팩트·장기·단기 성과)', 'SDGs 세부 목표', 'IRIS+ 지표와 트래킹 지표 산식', '임팩트 유지 기간'],
    viewpoints: ['임팩트 심의위원', 'LP'],
    vars: [],
    rubric: [
      { id: 'im_sdg', q: 'SDGs는 세부 목표 번호(예: 12.5)까지 적혔는가' },
      { id: 'im_iris', q: 'IRIS+ 지표 코드(PI…)와 측정 산식이 있는가' },
      { id: 'im_measure', q: '측정이 아직 어려운 부분은 그렇다고 밝혔는가' },
    ],
  },
  {
    key: 'support', title: 'VI. 투자사 성장지원 전략', pages: 1,
    needs: ['마일스톤별 성장 지원 방안', '연계할 파트너(대기업·스타트업·기관)', '후속 투자 연계'],
    viewpoints: ['사후관리 담당'],
    vars: [],
    rubric: [
      { id: 'sp_mile', q: '지원 방안이 회사의 마일스톤에 묶여 있는가' },
      { id: 'sp_named', q: '연계 파트너가 이름과 역할로 구체적인가' },
    ],
  },
  {
    key: 'valuation', title: 'VII. 손익 및 수익성 분석 (밸류에이션 · Exit)', pages: 5,
    needs: ['Peer Group 선정 기준(사업·재무·일반 유사성 3단계)', 'Peer별 시가총액·매출·순이익·PER·PBR', '적용 PER과 할인율 근거', 'Min/Avg/Max 수익성 표', 'Exit 전략(시점·방식·대안)'],
    viewpoints: ['투자심의위원', 'CFO', 'LP'],
    vars: ['peer_per_avg', 'per_discounted_low', 'per_discounted_high', 'exit_net_income', 'exit_value_min', 'exit_value_base', 'exit_value_max',
      'exit_multiple_min', 'exit_multiple_base', 'exit_multiple_max', 'exit_irr_min', 'exit_irr_base', 'exit_irr_max', 'holding_years'],
    rubric: [
      { id: 'va_funnel', q: 'Peer 선정이 단계별 기준과 제외 사유와 함께 제시되었는가' },
      { id: 'va_count', q: '본문의 Peer 회사 수와 표의 회사 수가 같은가' },
      { id: 'va_per', q: '적용 PER 밴드가 Peer 평균과 할인율에서 어떻게 나왔는지 이어지는가' },
      { id: 'va_table', q: 'Min/Avg/Max 표의 모든 칸이 같은 지분·주식수 가정으로 계산되었는가(계산 검산 통과)' },
      { id: 'va_exit', q: 'Exit 시점·방식(구주 매각·M&A·IPO)과 대안이 있는가' },
    ],
  },
  {
    key: 'risk', title: 'VIII. 임팩트 리스크 및 사업 리스크', pages: 1,
    needs: ['기술·제품 리스크', '이해관계자 리스크', '해외 진출 리스크', '인력 리스크', '각 리스크의 대응 방안'],
    viewpoints: ['비관적 심사역', '임팩트 심의위원'],
    vars: [],
    rubric: [
      { id: 'rk_pair', q: '리스크마다 대응 방안이 짝으로 있는가' },
      { id: 'rk_kill', q: '투자를 실패로 만들 수 있는 핵심 리스크가 분명히 드러나는가' },
    ],
  },
  {
    key: 'conclusion', title: 'IX. 종합 결론', pages: 1,
    needs: ['한 단락 요약', '투자 의견(진행/조건부/보류)'],
    viewpoints: ['투자심의위원'],
    vars: ['invest_amount', 'exit_irr_base'],
    rubric: [
      { id: 'cl_opinion', q: '투자 의견이 분명한가(진행·조건부·보류)' },
      { id: 'cl_consistent', q: '결론이 앞 섹션의 결정·리스크와 모순되지 않는가' },
    ],
  },
];

// 투자 건의 단계 — 보고서 표지의 일정과 같다
const STAGES = [
  { key: 'sourcing', label: '발굴' },
  { key: 'internal_ir', label: '내부 IR' },
  { key: 'impact_committee', label: '임팩트 심의위' },
  { key: 'investment_committee', label: '본투심' },
  { key: 'closing', label: '집행' },
  { key: 'post', label: '사후관리' },
  { key: 'dropped', label: '중단' },
];

const section = (key) => SECTIONS.find((s) => s.key === key) || null;
const rubricOf = (key) => [...(section(key)?.rubric || []), ...COMMON];

module.exports = { SECTIONS, STAGES, COMMON, section, rubricOf };
