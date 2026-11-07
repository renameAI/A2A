// 메리 딜룸을 앱에 붙이는 곳 — main.js는 createInvest()와 call()만 쓴다.
// 화면(셸)에서 오는 호출만 받는다(main.js의 invest:call 처리기가 보낸 쪽을 확인). 인자는 여기서 모양을 다시 본다.
const fs = require('fs');
const path = require('path');
const { createDealRoom } = require('./store');
const { createPipeline } = require('./pipeline');
const { createCopilot } = require('./copilot');
const { createFrn } = require('./frn');
const { fill, fmt } = require('./calc');
const { SECTIONS, STAGES, rubricOf } = require('./template');
const { ROLES, AUTHORITY, GUARDS } = require('../workid');

const str = (v, n = 4000) => (typeof v === 'string' ? v.slice(0, n) : '');
const ids = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string' && /^[a-z]{1,4}_[a-z0-9]{6,16}$/.test(x)).slice(0, 30) : []);
const MAX_FILE = 40 * 1024 * 1024;

function createInvest({ userData, collect, user, apiKey, worktypes = () => null, ema = () => null, bridge = null, send = () => {}, pickFiles = async () => [], ask = null }) {
  const dir = path.join(userData, 'invest');
  const room = createDealRoom({ dir, collect, user, onChange: (dealId) => send('invest:changed', dealId) });
  const pipe = createPipeline({ room, apiKey, dir, ask, onProgress: (p) => send('invest:progress', p) });
  const frn = createFrn({ ema });
  const copilot = createCopilot({ room, pipe, frn, bridge, send, typeDeal: (id) => typeDeal(id) });

  // 업무 유형 "투자" > "투자 심사보고서" — 투자 건마다 작업(task:deal-…)을 이 유형에 붙인다
  function typeDeal(dealId) {
    const wt = worktypes();
    if (!wt) return;
    try {
      const parent = wt.createType({ name: '투자', desc: '투자 검토·심의·집행·사후관리 — 의사결정과 논의가 쌓이는 업무' });
      const child = wt.createType({ name: '투자 심사보고서', desc: '자료에서 근거를 뽑고 가정을 잠그고 투자심사보고서를 써서 심의에 올린다', parent: parent.id });
      wt.assign(`deal-${dealId}`, child.id, 'merry', 1);
    } catch (e) { console.warn('[invest] 업무 유형', e.message); }
  }

  function view(dealId) {
    const deal = room.get(dealId);
    const cx = room.latestCompute(dealId);
    const vars = cx?.vars || {};
    const sections = {};
    for (const [k, s] of Object.entries(deal.sections)) sections[k] = { ...s, rendered: fill(s.html, vars).text, verify: undefined, judge: s.judge ? { items: s.judge.items } : null };
    return {
      deal: { ...deal, sections: undefined, chat: undefined, sources: deal.sources.map(({ file, ...s }) => s) },
      sections,
      vars: Object.fromEntries(Object.entries(vars).map(([k, x]) => [k, { display: fmt(x), formula: x.formula }])),
      checks: cx?.checks || [],
      chat: deal.chat.slice(-300),
      copilot: copilot.state(dealId),
      frn: frn.status(),
      ssow: { roles: ROLES, authority: AUTHORITY.commit, guards: GUARDS, ssot: (() => { const wt = worktypes(); const t = wt?.state().types.find((x) => x.name === '투자 심사보고서'); return t?.ssot || null; })() },
      template: { sections: SECTIONS.map((s) => ({ key: s.key, title: s.title, pages: s.pages, rubric: rubricOf(s.key).map((r) => r.q) })), stages: STAGES },
    };
  }

  const handlers = {
    list: () => room.list(),
    deal: (id) => view(str(id, 40)),
    create: (o = {}) => {
      const d = room.create({ company: str(o.company, 80), fund: str(o.fund, 80), amount: Number(o.amount) || null, method: str(o.method, 60), vision: str(o.vision, 200) });
      typeDeal(d.id);
      return d;
    },
    setStage: (id, stage, date) => room.setStage(str(id, 40), str(stage, 40), /^\d{4}-\d{2}-\d{2}$/.test(date || '') ? date : null),
    // 자료 올리기 — 파일 고르기는 메인이 연다(화면은 경로를 못 고른다)
    pickSource: async (id) => {
      const dealId = str(id, 40);
      room.get(dealId);
      const files = await pickFiles();
      const out = [];
      for (const f of files.slice(0, 10)) {
        const st = fs.statSync(f);
        if (!st.isFile() || st.size > MAX_FILE) continue;
        const name = path.basename(f);
        out.push(room.addSource(dealId, { name, buf: fs.readFileSync(f), kind: /계약|contract|SHA|SPA/i.test(name) ? 'contract' : /재무|financ|감사/i.test(name) ? 'financials' : /IR|deck/i.test(name) ? 'ir_deck' : 'other',
          mime: /\.pdf$/i.test(name) ? 'application/pdf' : 'text/plain' }));
      }
      return out.map(({ file, ...s }) => s);
    },
    extract: (id, sourceId) => pipe.extract(str(id, 40), str(sourceId, 40)),
    // 대화창 — 메리와 함께 쓰기(invest/copilot.js). 오래 걸리는 일은 바로 돌려주고 진행은 invest:progress로
    attach: async (id) => { const dealId = str(id, 40); const out = await handlers.pickSource(dealId); copilot.attached(dealId, out); return out; },
    attachBytes: (id, name, bytes) => {
      const dealId = str(id, 40);
      if (!(bytes instanceof Uint8Array) || bytes.length > MAX_FILE) throw new Error('40MB 이하 파일만 올릴 수 있어요');
      const n = path.basename(str(name, 200)) || 'file';
      if (!/\.(pdf|txt|md|csv)$/i.test(n)) throw new Error('PDF·텍스트·CSV만 올릴 수 있어요');
      const src = room.addSource(dealId, { name: n, buf: Buffer.from(bytes), kind: /계약|contract/i.test(n) ? 'contract' : /재무|financ|감사/i.test(n) ? 'financials' : /IR|deck/i.test(n) ? 'ir_deck' : 'other',
        mime: /\.pdf$/i.test(n) ? 'application/pdf' : 'text/plain' });
      const { file, ...pub } = src;
      return pub;
    },
    readAttached: (id, sourceIds) => { const dealId = str(id, 40); const d = room.get(dealId); return copilot.attached(dealId, d.sources.filter((s) => (sourceIds || []).includes(s.id))); },
    say: (id, text, section) => copilot.user(str(id, 40), str(text, 8000), { section: str(section, 40) || null }),
    answer: (id, msgId, o = {}) => copilot.answer(str(id, 40), str(msgId, 40), { choice: o.choice != null ? str(o.choice, 300) : null, text: o.text != null ? str(o.text, 4000) : null, skip: !!o.skip }),
    stop: (id) => copilot.stop(str(id, 40)),
    // FRN 연결 — 대표·앵커 지시(메신저 원문)를 붙여 넣으면 검토를 연다(확정하지 않고 받을 자료부터)
    intake: (text, o = {}) => copilot.intake(str(text, 8000), { instructor: str(o.instructor, 60), channel: str(o.channel, 60) }),
    frnStatus: () => frn.status(),
    // Work ID · 공식화 — workid.js, docs/WORKID.md
    setWork: (id, o = {}) => room.setWork(str(id, 40), { prn: str(o.prn, 40) || undefined, fund_id: str(o.fund_id, 60) || undefined,
      irn: Array.isArray(o.irn) ? o.irn.map((x) => str(x, 60)).filter(Boolean) : undefined, inv: Array.isArray(o.inv) ? o.inv.map((x) => str(x, 20)).filter(Boolean) : undefined,
      eir: Array.isArray(o.eir) ? o.eir.map((x) => str(x, 40)).filter(Boolean) : undefined }),
    commit: (id, decisionId, o = {}) => room.commit(str(id, 40), str(decisionId, 40), { role: str(o.role, 20), note: str(o.note, 300) }),
    // 받을 자료 상태를 사람이 바꾼다 — 받음 / 해당 없음 / 다시 열기
    setRequest: (id, itemId, status, note) => {
      if (!['received', 'waived', 'open', 'partial'].includes(status)) throw new Error('모르는 상태');
      room.updateItem(str(id, 40), str(itemId, 40), { status }, { note: str(note, 200) });
      return { ok: true };
    },
    // 사람이 종이에서 직접 고친 섹션 — 사람 수정으로 기록(업무 지도에 사람이 씀)
    saveSectionHtml: (id, key, html) => {
      const dealId = str(id, 40);
      const cur = room.get(dealId).sections[str(key, 40)];
      if (!cur) throw new Error('아직 없는 섹션이에요');
      const h = str(html, 400000);
      if (h === cur.html) return { same: true };
      room.saveSection(dealId, { ...cur, html: h, status: 'edited', editedBy: user() }, { byAgent: false, cites: [...new Set([...h.matchAll(/\[f:([a-z0-9_]+)\]/g)].map((m) => m[1]))] });
      return { ok: true };
    },
    draftPack: (id) => pipe.draftPack(str(id, 40)),
    lockPack: (id, pack, o = {}) => {
      if (!pack || typeof pack !== 'object' || Array.isArray(pack)) throw new Error('가정 모양이 맞지 않아요');
      if (JSON.stringify(pack).length > 200000) throw new Error('가정이 너무 커요');
      const r = room.lockPack(str(id, 40), pack, { evidence: ids(o.evidence), note: str(o.note, 400) });
      return { version: r.pack.version, checks: r.compute.checks };
    },
    plan: (id) => pipe.plan(str(id, 40)),
    perspectives: (id) => pipe.perspectives(str(id, 40)),
    write: (id, key) => copilot.write(str(id, 40), str(key, 40)),
    writeNow: async (id, key) => { const s = await pipe.writeSection(str(id, 40), str(key, 40)); return { key: s.key, status: s.status, summary: s.summary }; },
    addItem: (id, o = {}) => room.addItem(str(id, 40), { kind: str(o.kind, 20), text: str(o.text), section: str(o.section, 40) || null, replyTo: str(o.replyTo, 40) || null, evidence: ids(o.evidence) }),
    decide: (id, o = {}) => room.decide(str(id, 40), { topic: str(o.topic, 200), chosen: str(o.chosen, 1000), rationale: str(o.rationale, 2000),
      evidence: ids(o.evidence), resolves: ids(o.resolves), supersedes: str(o.supersedes, 40) || null, section: str(o.section, 40) || null }),
    lesson: (sectionKey, text) => { pipe.addLesson(str(sectionKey, 40) || '*', str(text, 300), 'human'); return true; },
  };
  // 자체 점검 전용 — 파일 고르기 창 없이 경로로 자료를 넣는다(A2A_SELFTEST_DIR일 때만 열린다)
  if (process.env.A2A_SELFTEST_DIR) {
    handlers.addSourcePath = (id, file) => {
      const name = path.basename(String(file));
      return room.addSource(str(id, 40), { name, buf: fs.readFileSync(String(file)), kind: 'report', mime: /\.pdf$/i.test(name) ? 'application/pdf' : 'text/plain' });
    };
  }

  return {
    room, pipe, copilot,
    call: (name, ...args) => {
      if (!Object.hasOwn(handlers, name)) throw new Error('blocked');
      return handlers[name](...args);
    },
  };
}

module.exports = { createInvest };
