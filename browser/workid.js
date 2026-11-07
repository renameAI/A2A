// Work ID · SSoT — 업무 지도의 업무 단위를 공식 원본(SSoT)과 기준 ID에 잇는 임시 운영 규칙. docs/WORKID.md.
//
//   식별자   FRN(스타트업 등록 ID) · PRN(독립 사업) · IRN/INV(지급 건) · EIR(외부 투자유치) · fund_id
//   계층     conversation(탐색, 공식 업무 아님) → committed(사람이 명시적으로 공식화)
//   모드     shadow(AI는 읽기·분석·초안) / committed
//   권한     업무별 AI·PM·투자재무 검증자·중앙 관리자의 몫
//   가드     G-COMMIT(공식화 권한), G-DISBURSE(지급 증빙·중복), G-KPI-ELIGIBLE / G-KPI-2APPROVAL(KPI 적격성·2인 승인)
// 업무 유형(worktypes)마다 SSoT를 붙인다 — 그 유형의 공식 원본이 어느 시스템이고, 무엇을 기준점(anchor)으로 삼고,
// 어떤 가드를 거쳐, 누가 공식화하는지. 업무 지도는 이 SSoT를 가리키기만 하고 원본을 대신하지 않는다.
const FORMATS = {
  frn: /^[A-Z]{3}-[A-Z]{2}-\d{2}-\d{3}$/,          // EMA 등록 형식(예: AGF-KR-00-001)
  prn: /^[A-Z][A-Za-z0-9가-힣]{0,30}(-[A-Za-z0-9가-힣]{1,20})*$/,   // 예: AC-00. 발급 규칙은 Registry 몫이라 느슨하게
  irn: /^\d{2}-\d{2}(-.{1,40})?$/,                  // YY-NN(예: 26-03, 표시는 26-03-샘플펀드)
  inv: /^INV-\d{1,6}$/,                              // 전사 누적 지급 순번
  eir: /^EIR-[A-Za-z0-9-]{1,30}$/,                   // 외부 투자유치 사실(제안 식별자)
};

// 권한 매트릭스 — 업무 → 각 역할의 몫. 화면은 "누가 공식화할 수 있나"를 보여 주고, 공식화 이벤트에 역할을 남긴다
const AUTHORITY = {
  frn_match:   { ai: '후보·근거',  pm: '매칭 검토',         finance: null,             admin: '충돌 확정' },
  prn_issue:   { ai: '명칭 초안',  pm: '신청·업무 승인',     finance: null,             admin: '중복 확인·발급' },
  disbursement:{ ai: '조회·대사 초안', pm: '필요 범위 조회',  finance: '지급 사실 검증',   admin: '번호 관리' },
  kpi:         { ai: '적격 후보',  pm: '계약·참여 적격성 승인', finance: '집행·금액 사실 검증', admin: '정책·충돌 관리' },
  commit:      { ai: '추천·초안',  pm: '공식화·책임',        finance: '필요시 검증',      admin: '예외·권한 관리' },
  external:    { ai: '초안만',    pm: '수신자·내용 승인',     finance: '금액 포함 시 검증', admin: '정책 관리' },
};
const ROLES = { pm: '사업 PM·책임자', finance: '투자·재무 검증자', admin: '중앙 관리자' };

const GUARDS = {
  'G-COMMIT': 'Commit 권한 — 공식 업무로 올리는 것은 사람이 명시적으로',
  'G-DISBURSE': '지급 증빙·중복 검사 — IRN/INV는 지급 확인 뒤에만',
  'G-KPI-ELIGIBLE': 'KPI 계약 적격성 — AI가 계약 조건을 유추해 실적 인정 금지',
  'G-KPI-2APPROVAL': 'KPI 인정 2인 승인',
  'G-SHADOW': 'Shadow Mode — AI는 읽기·분석·초안만(외부 발송·원본 쓰기·지급·인정 확정 금지)',
};

// 업무 유형의 SSoT — 유형 이름으로 기본값을 고른다. 모르는 유형은 "미정"으로 두고 사람이 채운다(지어내지 않는다)
const PATTERNS = [
  { match: /^투자$/, ssot: { system: 'Work Registry', anchor: 'FRN', related: ['PRN', 'fund_id', 'IRN', 'INV', 'EIR'],
    guards: ['G-COMMIT', 'G-SHADOW'], authority: 'commit', note: '투자 결정·승인은 Registry가 원본. 지급은 Investment Ledger(IRN/INV)' } },
  { match: /투자 심사보고서|투자심사/, ssot: { system: 'Work Registry · Company Vault(보고서)', anchor: 'FRN', related: ['PRN', 'fund_id'],
    guards: ['G-COMMIT', 'G-SHADOW'], authority: 'commit', note: '자료 원본은 Drive·EMA·MYSCube — 보고서는 근거 링크로만 가리킨다' } },
  { match: /지급|집행|송금/, ssot: { system: 'Investment Ledger', anchor: 'IRN', related: ['INV', 'FRN', 'fund_id'], guards: ['G-DISBURSE', 'G-SHADOW'], authority: 'disbursement' } },
  { match: /KPI|실적 인정/, ssot: { system: 'Work Registry(KPI)', anchor: 'PRN', related: ['FRN', 'IRN', 'EIR'], guards: ['G-KPI-ELIGIBLE', 'G-KPI-2APPROVAL', 'G-SHADOW'], authority: 'kpi' } },
  { match: /메일|발송|대표에게|연락/, ssot: { system: 'Gmail·Slack(원본) · Work Registry(공식 Commit)', anchor: 'FRN', related: ['PRN'], guards: ['G-SHADOW'], authority: 'external', note: '발송은 사람이 — AI는 초안까지' } },
  { match: /사업|프로그램|액셀러레이팅|선발/, ssot: { system: 'MYSCube · EMA Station', anchor: 'PRN', related: ['FRN'], guards: ['G-SHADOW'], authority: 'commit' } },
];
const UNKNOWN = { system: null, anchor: null, related: [], guards: ['G-SHADOW'], authority: 'commit', note: 'SSoT 미정 — 이 유형의 공식 원본 시스템을 사람이 정해야 함' };

function ssotFor(typeName) {
  const p = PATTERNS.find((x) => x.match.test(String(typeName || '')));
  // 이름 패턴으로 고른 값은 늘 기본값(defaulted) — 사람이 setSsot으로 정한 값만 확정. matched는 패턴이 맞았는지
  return { ...(p ? p.ssot : UNKNOWN), defaulted: true, matched: !!p };
}

// Work Context = Primary Anchor + Related Context. 값이 형식에 안 맞으면 버리지 않고 invalid로 표시
function workContext(o = {}) {
  const out = {};
  const bad = [];
  for (const k of ['frn', 'prn', 'irn', 'inv', 'eir', 'fund_id']) {
    const v = o[k];
    if (v == null || v === '') continue;
    const list = Array.isArray(v) ? v : [v];
    const ok = list.map(String).filter((x) => (FORMATS[k] ? FORMATS[k].test(x) : true));
    for (const x of list) if (FORMATS[k] && !FORMATS[k].test(String(x))) bad.push(`${k}:${x}`);
    if (ok.length) out[k] = Array.isArray(v) ? ok : ok[0];
  }
  const anchor = out.frn ? `FRN:${out.frn}` : out.prn ? `PRN:${out.prn}` : null;
  return { ...out, anchor, ...(bad.length ? { invalid: bad } : {}) };
}

module.exports = { FORMATS, AUTHORITY, ROLES, GUARDS, ssotFor, workContext };
