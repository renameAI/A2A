// 메리 딜룸 자체 점검 — 전자(Electron) 없이 node로: node selftest-invest.js
// 계산·검산(샘플수산 보고서 숫자), 딜룸 기록 → 수집 로그 → 업무 지도 노드·관계, 보고서 하네스(모델은 대역)를 본다.
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');
const { compute, fill } = require('./invest/calc');
const { createDealRoom } = require('./invest/store');
const { createPipeline } = require('./invest/pipeline');
const { createOntology } = require('./ontology');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'a2a-invest-'));
const events = path.join(tmp, 'events');
fs.mkdirSync(path.join(events, 'log'), { recursive: true });
let n = 0;
// 수집기 대역 — events.js의 봉투 중 업무 지도가 쓰는 칸만
const collect = {
  external(r) {
    const ev = { event_id: `e${++n}`, kind: r.kind, recorded_at: new Date(Date.now() + n).toISOString(), actor_kind: r.actor_kind, actor: r.actor,
      platform: r.platform, doc_type: r.doc_type, title: r.title, task_id: r.task_id, context: r.context, data: r.data,
      content_hash: r.content ? `sha256:${n.toString(16).padStart(8, '0')}` : null, span: r.span || null, derived_from: [] };
    fs.appendFileSync(path.join(events, 'log', '2026-10-28.jsonl'), `${JSON.stringify(ev)}\n`);
    return ev;
  },
};
const results = [];
const ok = (name, fn) => { try { fn(); results.push(`✓ ${name}`); } catch (e) { results.push(`✕ ${name}: ${e.message}`); process.exitCode = 1; } };

(async () => {
  // 1. 계산 — 가상의 투자 건(숫자는 모두 지어낸 값). 맞는 검산 하나와 일부러 틀린 표 셋
  const MM = 1e6;
  const pack = {
    deal: { amount: 300000000, price: 150000, shares: 2000, preShares: 18000, postShares: 20000, statedStake: 0.1, investDate: '2025-01-15', exitDate: '2029-01-15', exitShares: 21000 },
    pnl: { company: { 2025: { revenue: 300 * MM, cogs: 200 * MM, gross: 250 * MM, sga: 900 * MM, op: -650 * MM, ni: -600 * MM } },
      reviewer: { 2029: { revenue: 30000 * MM, cogs: 16000 * MM, gross: 14000 * MM, sga: 10000 * MM, op: 4000 * MM, ni: 3000 * MM } } },
    valuation: { exitYear: 2029, basis: 'reviewer', perBand: [13, 15, 17], statedPeerCount: 4, discount: [0.222, 0.336],
      peers: [{ name: 'Peer A', per: 24 }, { name: 'Peer B', per: 21 }, { name: 'Peer C', per: 11 }, { name: 'Peer D', per: 29 }],
      stated: { min: { multiple: 6.0, irr: 0.56 }, base: { multiple: 14.3, irr: 0.944 }, max: { multiple: 16.2, irr: 1.0 } } },
  };
  const c = compute(pack);
  ok('투자금 검산 통과(150,000원 × 2,000주)', () => assert.equal(c.checks.find((x) => x.id === 'amount_math').status, 'pass'));
  ok('기업가치(base) = 순이익 3,000백만 × 15', () => assert.equal(Math.round(c.vars.exit_value_base.value / MM), 45000));
  ok('멀티플(base) ≈ 14.29배(희석 반영)', () => assert.ok(Math.abs(c.vars.exit_multiple_base.value - 14.286) < 0.01));
  ok('회사 제시 손익 표 오류를 잡음(300−200≠250)', () => assert.equal(c.checks.find((x) => x.id === 'pnl_gross_company_2025').status, 'fail'));
  ok('Min 시나리오 멀티플 표(6.0배)가 다른 가정으로 계산된 것을 잡음', () => assert.equal(c.checks.find((x) => x.id === 'stated_multiple_min').status, 'fail'));
  ok('적용 PER 13~17배가 할인 반영 범위(약 14.1~16.5배)를 벗어남', () => assert.equal(c.checks.find((x) => x.id === 'per_band').status, 'warn'));
  ok('글 속 변수 채우기 + 모르는 변수 표시', () => { const f = fill('IRR {{var:exit_irr_base}} {{var:nope}}', c.vars); assert.match(f.text, /IRR 9\d\.\d%/); assert.deepEqual(f.unknown, ['nope']); });

  // 2. 딜룸 → 수집 로그 → 업무 지도
  const room = createDealRoom({ dir: path.join(tmp, 'invest'), collect, user: () => 'boram' });
  const deal = room.create({ company: '샘플수산', fund: '테스트 임팩트 펀드', amount: 300000000, method: '신주 / RCPS' });
  room.setStage(deal.id, 'internal_ir', '2024-11-04');
  const src = room.addSource(deal.id, { name: 'IR.pdf', buf: Buffer.from('%PDF-1.4 test'), kind: 'ir_deck' });
  const facts = room.addFacts(deal.id, src.id, [
    { key: 'revenue_2023_actual', label: '2023 매출', value: 226356363, unit: 'KRW', period: '2023A', page: 16, quote: '매출액 226,356,363', section: 'company' },
    { key: 'peer_per_avg', label: 'Peer 평균 PER', value: 21.38, unit: 'x', page: 42, quote: '평균 5.17 21.38', section: 'valuation' },
  ], { byAgent: true });
  const q = room.addItem(deal.id, { kind: 'question', text: '적용 PER 밴드를 어떻게 정할까요?', section: 'valuation', options: ['13/15/17배', '14/15/16배'], byAgent: true });
  const a = room.addItem(deal.id, { kind: 'answer', text: '14/15/16배', replyTo: q.id });
  const d1 = room.decide(deal.id, { topic: '적용 PER 밴드', chosen: '13 / 15 / 17배', rationale: 'Peer 평균 21.38배', evidence: [facts[1].id, a.id], resolves: [q.id], section: 'valuation' });
  const d2 = room.decide(deal.id, { topic: '적용 PER 밴드', chosen: '14 / 15 / 16배', rationale: '코스닥 신규 상장 할인율 22.2~33.6% 반영 — 본문과 표를 맞춤', evidence: [facts[1].id], supersedes: d1.id, section: 'valuation' });
  room.lockPack(deal.id, pack, { evidence: [facts[0].id], note: '심사역 추정 확정' });

  const st = room.get(deal.id);
  ok('질문 → 답하면 answered, 결정이 해소하면 resolved', () => assert.equal(st.items.find((x) => x.id === q.id).status, 'resolved'));
  ok('바뀐 결정은 지우지 않고 supersededBy로 남음', () => { assert.equal(st.decisions.find((x) => x.id === d1.id).supersededBy, d2.id); assert.equal(st.decisions.length, 2); });
  ok('가정 잠그면 바로 계산', () => assert.ok(room.latestCompute(deal.id).vars.exit_irr_base));
  ok('딜룸을 다시 열어도 같은 상태(ops.jsonl)', () => {
    const again = createDealRoom({ dir: path.join(tmp, 'invest'), collect: null, user: () => 'boram' }).get(deal.id);
    assert.equal(again.decisions.length, 2); assert.equal(again.facts.length, 2); assert.equal(again.packs.length, 1);
  });

  const onto = createOntology({ root: () => events });
  const { nodes, edges } = onto.raw();
  const has = (id) => nodes.has(id);
  const rel = (s, r, o) => edges.some((e) => e.s === s && e.r === r && e.o === o);
  ok('업무 지도: 투자 건 노드 + 작업(task:deal-…)', () => { assert.ok(has(`deal:${deal.id}`)); assert.ok(has(`task:deal-${deal.id}`)); });
  ok('업무 지도: 근거가 자료를 인용', () => assert.ok(rel(`fact:${facts[0].id}`, 'quotes', `doc:${src.id}`)));
  ok('업무 지도: 결정이 근거·쟁점에 기대고 질문을 해소', () => { assert.ok(rel(`decision:${d1.id}`, 'based_on', `fact:${facts[1].id}`)); assert.ok(rel(`decision:${d1.id}`, 'resolves', `issue:${q.id}`)); });
  ok('업무 지도: 새 결정이 이전 결정을 대체', () => { assert.ok(rel(`decision:${d2.id}`, 'supersedes', `decision:${d1.id}`)); assert.ok(nodes.get(`decision:${d1.id}`).props.superseded); });
  ok('업무 지도: 답이 질문에 답함, 사람이 결정함', () => { assert.ok(rel(`issue:${a.id}`, 'answers', `issue:${q.id}`)); assert.ok(rel('person:boram', 'decided', `decision:${d2.id}`)); });
  ok('업무 지도: 계산이 가정에서 나옴', () => assert.ok(edges.some((e) => e.r === 'computed_from' && e.s.startsWith('calc:'))));
  ok('업무 지도: 관계마다 주장 튜플(b·c·ζ)', () => { const e = edges.find((x) => x.r === 'supersedes'); assert.ok(e.b && e.c.task_id === `deal-${deal.id}` && e.zeta.platform === 'merry' && e.zeta.kind === 'invest.decision'); });

  // 3. 보고서 하네스 — 모델 대역: 첫 초안은 근거 없는 숫자, 고친 뒤 통과
  let writes = 0;
  const ask = async (role, system, content) => {
    const text = typeof content === 'string' ? content : '';
    if (role === 'write') {
      writes++;
      return writes === 1
        ? '<h2>2.1 Peer Group</h2><p>평균 PER은 21.38배임 [f:' + facts[1].id + ']. 시장은 999억 원 규모임.</p>'
        : '<h2>2.1 Peer Group</h2><p>Peer 평균 PER은 21.38배임 [f:' + facts[1].id + ']. 기준 IRR은 {{var:exit_irr_base}}임.</p><p>시장 규모는 [확인 필요: 출처 있는 시장 규모].</p>';
    }
    if (role === 'check') return { claims: [{ text: 'Peer 평균 PER 21.38배', facts: [facts[1].id], verdict: 'supported' }] };
    if (role === 'judge') return { items: [{ id: 'va_per', pass: writes > 1, why: writes > 1 ? '' : '근거 없는 시장 규모' }, { id: 'c_nonum', pass: writes > 1, why: '' }] };
    return {};
  };
  const pipe = createPipeline({ room, apiKey: () => 'x', dir: path.join(tmp, 'invest'), ask });
  const sec = await pipe.writeSection(deal.id, 'valuation');
  ok('하네스: 근거 없는 숫자(999)를 도구가 찾아 한 번 고쳐 씀', () => { assert.equal(writes, 2); assert.equal(sec.summary.rounds, 1); });
  ok('하네스: 고친 섹션은 변수·근거만 쓰고 통과', () => { assert.equal(sec.summary.looseNumbers, 0); assert.equal(sec.summary.unknownVars, 0); assert.equal(sec.status, 'verified'); });
  ok('하네스: 섹션이 업무 지도에 산출물로(근거 연결)', () => {
    const o2 = createOntology({ root: () => events }).raw();
    const sid = [...o2.nodes.keys()].find((k) => k.startsWith(`section:${deal.id}:valuation`));
    assert.ok(sid); assert.ok(o2.edges.some((e) => e.s === sid && e.r === 'derived_from' && e.o === `fact:${facts[1].id}`));
  });

  // 4. FRN 연결 — 지시로 검토 열기(모델 대역): 지시·FRN 기업·미확인 주장·받을 자료가 업무 지도에
  const { createCopilot } = require('./invest/copilot');
  const ask2 = async (role, system) => {
    if (/지시를 구조화/.test(system)) return { frn: 'AGF-KR-00-001', company: '(주)샘플바다 (B Corp)', instructor: null, assignees: ['@앵커_홍길동'], ask: '샘플펀드로 검토', fund: '샘플펀드(지역 10억원 몫)',
      claims: [{ label: '전년 영업이익률', value: 0.3, unit: '%', period: '2025A', text: '작년 영업이익 30% 선', section: 'overview' }, { label: '올해 매출 전망', value: 8.25e9, unit: 'KRW', period: '2026E', text: '올해 매출 약 80~85억원 예상', section: 'overview' }],
      context: ['샘플증권과 상장 검토 중'] };
    if (/부족한 정보|받아 내는 것/.test(system)) return { requests: [{ what: '전년 감사보고서', why: '영업이익률 30% 확인', section: 'overview', from: '회사', priority: '필수' }, { what: '올해 월별 매출 실적', why: '매출 전망 확인', section: 'overview', from: '회사', priority: '중요' }], questions: [], request_message: '안녕하세요 …', summary: '아는 것: … 모르는 것: …' };
    if (/근거"를 뽑습니다/.test(system)) return JSON.stringify({ facts: [{ key: 'frn_name', label: '등록 기업명', value: '(주)샘플바다', page: null, quote: '"name":"(주)샘플바다"', section: 'company' }] });
    if (/열린 자료 요청과/.test(system)) return { matches: [] };
    return {};
  };
  const pipe2 = createPipeline({ room, apiKey: () => 'x', dir: path.join(tmp, 'invest'), ask: ask2 });
  const fakeFrn = { status: () => 'on', get: async (c) => `{"frn":"${c}","name":"(주)샘플바다","sector":"AGF"}`, search: async () => '' };
  const sent = [];
  const cp = createCopilot({ room, pipe: pipe2, frn: fakeFrn, send: (ch, p) => sent.push(p) });
  cp.intake('(주)샘플바다 (B Corp)\nFRN: AGF-KR-00-001\n@앵커_홍길동 IR 자료 보강하고 있으니 샘플펀드(지역 10억원 몫) 검토해보면 어떨까요?\n* 작년 영업이익 30% 선/ 올해 매출 약 80~85억원 예상', { instructor: '대표' });
  for (let i = 0; i < 50 && !sent.some((p) => p.type === 'intake' && p.state === 'done'); i++) await new Promise((r) => setTimeout(r, 20));
  const nd = room.list().find((x) => x.company === '(주)샘플바다');
  ok('FRN 연결: EMA 등록명으로 투자 건을 열고 FRN을 붙임', () => { assert.ok(nd); assert.equal(room.get(nd.id).frn, 'AGF-KR-00-001'); });
  const D2 = room.get(nd.id);
  ok('FRN 연결: 지시 속 숫자는 미확인 주장, 등록 정보는 FRN 근거', () => { assert.equal(D2.facts.filter((f) => f.basis === 'claim').length, 2); assert.ok(D2.facts.some((f) => f.basis === 'registry')); });
  ok('FRN 연결: 확정하지 않음 — 단계 발굴, 결정 0, 받을 자료 2', () => { assert.equal(D2.stage, 'sourcing'); assert.equal(D2.decisions.length, 0); assert.equal(D2.items.filter((x) => x.kind === 'request' && x.status === 'open').length, 2); });
  const req = D2.items.find((x) => x.kind === 'request');
  room.updateItem(nd.id, req.id, { status: 'received', evidence: [D2.facts[0].id] }, { note: '감사보고서 받음' });
  const o3 = createOntology({ root: () => events }).raw();
  const r3 = (s1, r, o) => o3.edges.some((e) => e.s === s1 && e.r === r && e.o === o);
  ok('업무 지도: 대표 —지시함→ 지시 —투자 건→ 투자 건, 앵커에게 맡김', () => {
    const dr = [...o3.nodes.keys()].find((k) => k.startsWith('directive:'));
    assert.ok(r3('person:대표', 'instructed', dr)); assert.ok(r3(dr, 'about_deal', `deal:${nd.id}`)); assert.ok(r3(dr, 'assigned', 'person:앵커_홍길동'));
  });
  ok('업무 지도: 투자 건 —FRN 등록→ company:AGF-KR-00-001(에이전트가 EMA에서 본 기업 노드와 같은 id)', () => assert.ok(r3(`deal:${nd.id}`, 'registered_as', 'company:AGF-KR-00-001')));
  ok('업무 지도: 받을 자료 → 받음 상태와 받은 근거', () => { assert.equal(o3.nodes.get(`issue:${req.id}`).props.status, 'received'); assert.ok(r3(`issue:${req.id}`, 'fulfilled_by', `fact:${D2.facts[0].id}`)); });

  // 5. Work ID · 공식화(Commit) · 업무 유형 SSoT — workid.js
  const { ssotFor, workContext } = require('./workid');
  const w = room.setWork(nd.id, { prn: 'AC-00', fund_id: 'sample-fund', irn: ['26-03', 'bad irn'], inv: ['INV-331'] });
  ok('Work ID: FRN 기준점 + PRN·fund·IRN·INV, 형식 틀린 값은 invalid로 남김', () => { assert.equal(w.anchor, 'FRN:AGF-KR-00-001'); assert.deepEqual(w.irn, ['26-03']); assert.ok(w.invalid.includes('irn:bad irn')); });
  const dcx = room.decide(nd.id, { topic: '검토 펀드', chosen: '샘플펀드(경북도 10억 몫)로 검토' });
  ok('결정은 탐색 단계(proposed)로 시작 — AI·대화는 공식화하지 않음', () => assert.equal(room.get(nd.id).decisions.find((x) => x.id === dcx.id).status, 'proposed'));
  assert.throws(() => room.commit(nd.id, dcx.id, { role: 'ai' }));
  room.commit(nd.id, dcx.id, { role: 'pm', note: '팀장 확인' });
  ok('공식화(Commit)는 사람이 역할과 함께 — 다시 읽어도 유지', () => {
    const again = createDealRoom({ dir: path.join(tmp, 'invest'), collect: null, user: () => 'boram' }).get(nd.id).decisions.find((x) => x.id === dcx.id);
    assert.equal(again.status, 'committed'); assert.equal(again.committed.role, 'pm');
  });
  const o4 = createOntology({ root: () => events }).raw();
  const r4 = (s1, r, o) => o4.edges.some((e) => e.s === s1 && e.r === r && e.o === o);
  ok('업무 지도: 투자 건 —사업(PRN)→ prn:AC-00, —재원→ fund, —지급 건→ 26-03·INV-331', () => {
    assert.ok(r4(`deal:${nd.id}`, 'in_project', 'prn:AC-00')); assert.ok(r4(`deal:${nd.id}`, 'funded_by', 'fund:sample-fund'));
    assert.ok(r4(`deal:${nd.id}`, 'disbursed_as', 'disb:26-03')); assert.ok(r4(`deal:${nd.id}`, 'disbursed_as', 'disb:INV-331'));
  });
  ok('업무 지도: 사람 —공식화함→ 결정(✔, mode committed), 다른 결정은 탐색', () => {
    assert.ok(r4('person:boram', 'committed', `decision:${dcx.id}`)); const n = o4.nodes.get(`decision:${dcx.id}`); assert.equal(n.props.mode, 'committed'); assert.ok(n.label.startsWith('✔'));
    assert.equal(o4.nodes.get(`decision:${d2.id}`).props.mode, 'conversation');
  });
  ok('이벤트마다 Work ID와 계층(mode)이 붙음', () => {
    const lines = fs.readFileSync(path.join(events, 'log', '2026-10-28.jsonl'), 'utf8').trim().split('\n').map((l) => JSON.parse(l)).filter((e) => e.data?.deal_id === nd.id);
    assert.ok(lines.every((e) => e.data.mode)); assert.ok(lines.some((e) => e.data.work?.frn === 'AGF-KR-00-001'));
  });
  ok('업무 유형 SSoT: 투자 심사보고서 → Registry·FRN·G-COMMIT, 모르는 유형은 미정(지어내지 않음)', () => {
    const a = ssotFor('투자 심사보고서'); assert.equal(a.anchor, 'FRN'); assert.ok(a.guards.includes('G-COMMIT'));
    const u = ssotFor('사내 회식 장소 찾기'); assert.equal(u.system, null); assert.ok(u.defaulted); assert.equal(u.matched, false); assert.ok(a.defaulted && a.matched);
  });

  // 6. SSoT 임시 발행 — 업무 단위마다 TMP-ID, 멱등, 다시 열어도 같은 ID, 계층은 지금 모습으로
  const { createSsotRegistry } = require('./ssot');
  const ontoS = createOntology({ root: () => events });
  const wtStub = { get: () => null, ssotOf: () => null };
  const regDir = path.join(tmp, 'ssot');
  const reg = createSsotRegistry({ dir: regDir, onto: ontoS, worktypes: () => wtStub });
  const first = reg.sync();
  const sst = reg.stats();
  ok('SSoT: 작업·투자 건·결정·지시마다 임시 ID 자동 발행', () => { assert.ok(first >= 6); assert.equal(sst.issued, sst.eligible); assert.ok(sst.byKind.DL >= 2 && sst.byKind.DC >= 3 && sst.byKind.DR >= 1); });
  ok('SSoT: 다시 sync해도 새로 발행 안 함(멱등)', () => assert.equal(reg.sync(), 0));
  const dealRec = reg.get(`deal:${nd.id}`);
  ok('SSoT: ID 형식 TMP-DL-YYMM-0001, 기준 ID FRN, 정책 provisional-v0', () => { assert.match(dealRec.record.id, /^TMP-DL-\d{4}-\d{4}$/); assert.equal(dealRec.live.work.anchor, 'FRN:AGF-KR-00-001'); assert.equal(dealRec.record.policy, 'provisional-v0'); });
  ok('SSoT: 공식화된 결정은 committed, 나머지는 탐색', () => { assert.equal(reg.get(`decision:${dcx.id}`).live.mode, 'committed'); assert.equal(reg.get(`decision:${d2.id}`).live.mode, 'conversation'); });
  ok('SSoT: 다시 열어도 같은 ID(불변)', () => { const again = createSsotRegistry({ dir: regDir, onto: ontoS, worktypes: () => wtStub }); assert.equal(again.get(`deal:${nd.id}`).record.id, dealRec.record.id); assert.equal(again.sync(), 0); });

  // 7. 홈 에이전트와 같은 기준 — 팀 채널 게시(대화창 승인 뒤에만), 홈 에이전트에게 맡긴 웹 페이지가 근거로
  const posted = [];
  const bridge = {
    channels: async () => [{ id: 7, name: 'general' }, { id: 8, name: '투자-비공개' }],
    post: async (cid, text) => { posted.push({ cid, text }); return { seq: 42 }; },
    delegate: async () => ({ taskId: 't999', status: 'done', answer: '보도 2건 확인' }),
    reads: () => [{ url: 'https://news.example.com/a', title: '샘플바다 상장 추진', text: '샘플바다가 샘플증권과 상장을 추진한다고 2026-09-01 밝혔다.' }],
  };
  const ask3 = async (role, system) => {
    if (/근거"를 뽑습니다/.test(system)) return JSON.stringify({ facts: [{ key: 'ipo_plan', label: '상장 추진', value: '샘플증권 상장 추진', quote: '샘플증권과 상장을 추진', page: null, section: 'company' }] });
    if (/직접 읽고 답합니다/.test(system)) return '검토 요청: (주)샘플바다 FRN AGF-KR-00-001 [f:' + D2.facts[0].id + ']';
    return {};
  };
  const pipe3 = createPipeline({ room, apiKey: () => 'x', dir: path.join(tmp, 'invest'), ask: ask3 });
  const sent3 = [];
  const cp3 = createCopilot({ room, pipe: pipe3, bridge, send: (ch, p) => sent3.push(p) });
  const idle3 = async () => { for (let i = 0; i < 100 && cp3.state(nd.id).busy; i++) { const w = cp3.state(nd.id).waiting[0]; if (w) cp3.answer(nd.id, w, { choice: '올리기' }); await new Promise((r) => setTimeout(r, 10)); } };
  cp3.user(nd.id, '@general에 검토 요청 보내줘');
  await idle3();
  ok('채널 게시: 초안 → 대화창 승인 → #general에, [f:id]는 사람이 읽는 출처로', () => { assert.equal(posted.length, 1); assert.equal(posted[0].cid, 7); assert.ok(!/\[f:/.test(posted[0].text)); });
  cp3.user(nd.id, '취소 테스트: #general에 올려줘');
  for (let i = 0; i < 100 && cp3.state(nd.id).busy; i++) { const w = cp3.state(nd.id).waiting[0]; if (w) cp3.answer(nd.id, w, { choice: '취소' }); await new Promise((r) => setTimeout(r, 10)); }
  ok('채널 게시: 승인하지 않으면 올리지 않음', () => assert.equal(posted.length, 1));
  cp3.user(nd.id, '웹에서 검색해서 상장 추진 기사 찾아봐', { section: 'exec' });
  await idle3();
  const webF = room.get(nd.id).facts.filter((f) => f.basis === 'web');
  ok('홈 에이전트에게 맡김: 읽은 페이지 → 출처 URL이 달린 웹 근거(섹션이 골라져 있어도 웹 요청이 먼저)', () => { assert.ok(webF.length >= 1); assert.equal(webF[0].url, 'https://news.example.com/a'); });
  const o5 = createOntology({ root: () => events }).raw();
  ok('업무 지도: 투자 건 작업 —에이전트에게 맡김→ task:t999, —게시함→ #general', () => {
    assert.ok(o5.edges.some((e) => e.s === `task:deal-${nd.id}` && e.r === 'delegated' && e.o === 'task:t999'));
    assert.ok(o5.edges.some((e) => e.s === `task:deal-${nd.id}` && e.r === 'posted' && /#general$/.test(e.o)));
  });

  console.log(results.join('\n'));
  console.log(`\n${results.filter((r) => r.startsWith('✓')).length}/${results.length} 통과`);
  fs.rmSync(tmp, { recursive: true, force: true });
})().catch((e) => { console.error(e); process.exit(1); });
