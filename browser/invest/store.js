// 메리 딜룸 — 투자 건 하나의 자료·근거·논의·결정·가정·계산·보고서 섹션.
//
// 모든 변경은 두 곳에 남는다:
//   1) userData/invest/ops.jsonl  — 이 화면의 상태(다시 읽어서 복원)
//   2) 수집 로그(events.js)        — kind = invest.<동사>. 업무 지도(ontology.js)가 투자 건·쟁점·결정·가정 노드로 바꾼다
// 결정은 지우지 않는다. 바뀌면 새 결정이 이전 결정을 supersedes 한다(누가·언제·무슨 근거로 바꿨는지가 남게).
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { compute } = require('./calc');
const { STAGES, SECTIONS } = require('./template');
const { workContext, ROLES } = require('../workid');

const id = (p) => `${p}_${crypto.randomBytes(5).toString('hex')}`;
const short = (s, n) => { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? `${s.slice(0, n)}…` : s; };
const KINDS = ['question', 'issue', 'bull', 'bear', 'answer', 'comment', 'request'];

function createDealRoom({ dir, collect = null, user = () => 'me', onChange = () => {} }) {
  fs.mkdirSync(path.join(dir, 'files'), { recursive: true });
  const OPS = path.join(dir, 'ops.jsonl');
  const deals = new Map();

  const blank = (d) => ({ ...d, work: d.work || workContext({ frn: d.frn }), stage: d.stage || 'sourcing', sources: [], facts: [], items: [], decisions: [], packs: [], computes: [], sections: {}, runs: [], chat: [], directives: [] });
  // ops 하나를 상태에 반영 — 파일에서 다시 읽을 때와 새로 쓸 때 같은 길로
  function reduce(op) {
    if (op.op === 'deal') { deals.set(op.deal.id, blank(op.deal)); return; }
    const d = deals.get(op.dealId);
    if (!d) return;
    switch (op.op) {
      case 'deal.update': Object.assign(d, op.patch); break;
      case 'stage': d.stage = op.stage; d.stageDates = { ...(d.stageDates || {}), [op.stage]: op.date || op.at.slice(0, 10) }; break;
      case 'source': d.sources.push(op.source); break;
      case 'facts': d.facts.push(...op.facts); break;
      case 'item': d.items.push(op.item); break;
      case 'item.status': { const it = d.items.find((x) => x.id === op.itemId); if (it) it.status = op.status; break; }
      case 'decision': {
        if (op.decision.supersedes) { const old = d.decisions.find((x) => x.id === op.decision.supersedes); if (old) old.supersededBy = op.decision.id; }
        for (const iid of op.decision.resolves || []) { const it = d.items.find((x) => x.id === iid); if (it) it.status = 'resolved'; }
        d.decisions.push(op.decision);
        break;
      }
      case 'pack': d.packs.push(op.pack); break;
      case 'compute': d.computes.push(op.result); break;
      case 'section': d.sections[op.section.key] = op.section; break;
      case 'run': d.runs.push(op.run); break;
      case 'chat': d.chat.push(op.msg); break;
      case 'commit': { const dc = d.decisions.find((x) => x.id === op.decisionId); if (dc) Object.assign(dc, { status: 'committed', committed: op.committed }); break; }
      case 'directive': d.directives.push(op.directive); break;
      case 'item.update': { const it = d.items.find((x) => x.id === op.itemId); if (it) Object.assign(it, op.patch); break; }
      case 'chat.update': { const m = d.chat.find((x) => x.id === op.msgId); if (m) Object.assign(m, op.patch); break; }
      default: break;
    }
  }
  try {
    for (const line of fs.readFileSync(OPS, 'utf8').split('\n')) { if (line) { try { reduce(JSON.parse(line)); } catch { /* 깨진 줄 */ } } }
  } catch { /* 처음 */ }

  // 기록 — 상태 파일 + 수집 로그. 수집이 꺼져 있어도 딜룸은 동작한다
  function write(op, ev) {
    op.at = op.at || new Date().toISOString();
    op.by = op.by || user();
    fs.appendFileSync(OPS, `${JSON.stringify(op)}\n`);
    reduce(op);
    if (ev && collect) {
      // 모든 딜 이벤트에 Work ID(FRN·PRN·fund_id·IRN…)와 계층(conversation 탐색 / shadow AI 초안 / committed 공식)을 붙인다
      const dd = op.dealId ? deals.get(op.dealId) : null;
      ev.data = { ...(ev.data || {}), work: dd?.work || null, mode: ev.mode || (ev.actor_kind === 'agent' ? 'shadow' : 'conversation'), correlation_id: op.dealId ? `deal-${op.dealId}` : null };
      try {
        collect.external({
          actor_kind: ev.actor_kind || 'human', actor: ev.actor || op.by, platform: 'merry',
          content_origin: ev.content_origin || (ev.actor_kind === 'agent' ? 'agent' : 'human'),
          task_id: op.dealId ? `deal-${op.dealId}` : null, context: { deal: op.dealId || null, instruction: ev.instruction || null },
          ...ev,
        });
      } catch (e) { console.warn('[invest] 수집 실패', e.message); }
    }
    onChange(op.dealId || op.deal?.id);
    return op;
  }
  const get = (dealId) => { const d = deals.get(dealId); if (!d) throw new Error('없는 투자 건이에요'); return d; };

  const api = {
    list: () => [...deals.values()].map((d) => ({
      id: d.id, company: d.company, fund: d.fund, amount: d.amount, stage: d.stage, created: d.created,
      open: d.items.filter((x) => x.status === 'open').length, decisions: d.decisions.filter((x) => !x.supersededBy).length,
      sections: Object.keys(d.sections).length, total: SECTIONS.length,
    })).sort((a, b) => String(b.created).localeCompare(String(a.created))),
    get: (dealId) => get(dealId),

    create({ company, fund = '', amount = null, method = '', vision = '', frn = null }) {
      company = short(company, 80);
      if (!company) throw new Error('회사 이름이 필요해요');
      const deal = { id: id('deal'), company, fund: short(fund, 80), amount: Number(amount) || null, method: short(method, 60), vision: short(vision, 200), frn: frn || null, work: workContext({ frn }), created: new Date().toISOString(), owner: user() };
      write({ op: 'deal', deal, dealId: deal.id }, {
        kind: 'invest.deal', doc_type: 'deal', title: company,
        content: `투자 건 등록: ${company}${fund ? ` · ${fund}` : ''}${deal.amount ? ` · ${deal.amount.toLocaleString('ko-KR')}원` : ''}${method ? ` · ${method}` : ''}`,
        data: { deal_id: deal.id, company, fund: deal.fund, amount: deal.amount, method: deal.method, frn: deal.frn },
      });
      return deal;
    },
    setStage(dealId, stage, date = null) {
      get(dealId);
      if (!STAGES.some((s) => s.key === stage)) throw new Error('모르는 단계');
      write({ op: 'stage', dealId, stage, date }, {
        kind: 'invest.stage', doc_type: 'stage', content: `${deals.get(dealId).company} 단계: ${STAGES.find((s) => s.key === stage).label}${date ? ` (${date})` : ''}`,
        data: { deal_id: dealId, stage, date },
      });
    },
    // 자료 — 파일은 userData/invest/files/<해시>.<확장자>로 복사해 둔다(원문 버전 v)
    addSource(dealId, { name, buf, kind = 'other', mime = 'application/pdf', url = null }) {
      get(dealId);
      const hash = crypto.createHash('sha256').update(buf).digest('hex');
      const ext = (path.extname(name || '') || '.pdf').slice(0, 8);
      const file = path.join(dir, 'files', `${hash}${ext}`);
      if (!fs.existsSync(file)) fs.writeFileSync(file, buf, { mode: 0o600 });
      const source = { id: id('src'), name: short(name, 120), kind, mime, hash, bytes: buf.length, file, url: /^https?:\/\//.test(String(url || '')) ? short(url, 400) : null };
      write({ op: 'source', dealId, source }, {
        kind: 'invest.source', doc_type: kind, title: source.name, content_origin: 'external', source_url: source.url,
        content: `자료: ${source.name} (${kind}, ${Math.round(buf.length / 1024)}KB, sha256 ${hash.slice(0, 12)})`,
        data: { deal_id: dealId, source_id: source.id, file_sha256: hash, name: source.name, kind },
      });
      return source;
    },
    // 근거 — 자료에서 뽑은 값과 원문 인용(쪽 번호). 사람이 고친 근거는 by가 사람
    addFacts(dealId, sourceId, facts, { byAgent = false } = {}) {
      const d = get(dealId);
      const src = d.sources.find((s) => s.id === sourceId) || null;
      const clean = facts.slice(0, 300).map((f) => ({
        id: id('f'), sourceId: src?.id || null, key: short(f.key, 60), label: short(f.label || f.key, 80),
        value: typeof f.value === 'number' ? f.value : short(f.value, 300), unit: short(f.unit, 16), period: short(f.period, 20),
        page: Number.isInteger(f.page) ? f.page : null, quote: short(f.quote, 400), section: short(f.section, 30),
        basis: ['claim', 'registry', 'document', 'web'].includes(f.basis) ? f.basis : (src?.kind === 'directive' ? 'claim' : src?.kind === 'frn' ? 'registry' : src?.kind === 'web' ? 'web' : 'document'),
        url: /^https?:\/\//.test(String(f.url || '')) ? short(f.url, 400) : (src?.url || null),
      })).filter((f) => f.key && (f.quote || f.value !== ''));
      write({ op: 'facts', dealId, facts: clean }, null);
      for (const f of clean) {
        collect?.external({
          kind: 'invest.fact', actor_kind: byAgent ? 'agent' : 'human', actor: byAgent ? `agent:${user()}` : user(), platform: 'merry',
          content_origin: 'external', doc_type: 'fact', task_id: `deal-${dealId}`, title: `${f.label}: ${f.value}${f.unit ? ` ${f.unit}` : ''}`, source_url: f.url || null,
          content: f.quote || `${f.label}: ${f.value}`, span: f.page ? { kind: 'pdf_page', page: f.page, within: src ? `file:${src.hash}` : null } : null,
          context: { deal: dealId }, data: { deal_id: dealId, fact_id: f.id, source_id: f.sourceId, key: f.key, value: f.value, unit: f.unit, period: f.period, page: f.page, basis: f.basis, url: f.url, work: d.work || null, mode: byAgent ? 'shadow' : 'conversation', correlation_id: `deal-${dealId}` },
        });
      }
      onChange(dealId);
      return clean;
    },
    // 논의 — 질문·쟁점·강점(bull)·우려(bear)·답·코멘트. 객관식 선택지는 options로
    addItem(dealId, { kind = 'comment', text, section = null, replyTo = null, evidence = [], options = null, byAgent = false, stance = null, from = null, priority = null }) {
      const d = get(dealId);
      if (!KINDS.includes(kind)) throw new Error('모르는 논의 종류');
      text = String(text || '').trim().slice(0, 4000);
      if (!text) throw new Error('내용이 비었어요');
      const item = { id: id('it'), kind, text, section, replyTo, evidence: evidence.filter((x) => typeof x === 'string').slice(0, 20),
        options: Array.isArray(options) ? options.map((o) => short(o, 120)).slice(0, 6) : null, stance, byAgent: !!byAgent,
        from, priority, status: kind === 'question' || kind === 'issue' || kind === 'request' ? 'open' : 'note', at: new Date().toISOString(), by: byAgent ? `agent:${user()}` : user() };
      write({ op: 'item', dealId, item }, {
        kind: 'invest.discussion', doc_type: kind, actor_kind: byAgent ? 'agent' : 'human', actor: item.by,
        content: text, title: short(text, 80),
        data: { deal_id: dealId, item_id: item.id, kind, section, reply_to: replyTo, evidence: item.evidence, options: item.options, stance, from, priority },
      });
      if (replyTo && kind === 'answer') { const q = d.items.find((x) => x.id === replyTo); if (q && q.status === 'open') write({ op: 'item.status', dealId, itemId: replyTo, status: 'answered' }, null); }
      return item;
    },
    // 결정 — 무엇을(topic) 어떻게(chosen) 왜(rationale) 무엇을 근거로(evidence) 정했는지. 바꾸면 supersedes
    decide(dealId, { topic, chosen, rationale = '', options = null, evidence = [], resolves = [], supersedes = null, section = null, kind = 'judgment' }) {
      const d = get(dealId);
      topic = short(topic, 120); chosen = String(chosen || '').trim().slice(0, 1000);
      if (!topic || !chosen) throw new Error('무엇을 어떻게 정했는지 적어 주세요');
      if (supersedes && !d.decisions.some((x) => x.id === supersedes)) throw new Error('바꿀 결정을 찾지 못했어요');
      const decision = { id: id('dc'), topic, chosen, rationale: String(rationale).slice(0, 2000), kind,
        options: Array.isArray(options) ? options.map((o) => short(o, 120)).slice(0, 6) : null,
        evidence: evidence.filter((x) => typeof x === 'string').slice(0, 30), resolves: resolves.filter((x) => typeof x === 'string'),
        supersedes, section, stage: d.stage, status: 'proposed', at: new Date().toISOString(), by: user() };
      write({ op: 'decision', dealId, decision }, {
        kind: 'invest.decision', doc_type: 'decision', label_kind: 'decision',
        content: `[결정] ${topic}\n→ ${chosen}${decision.rationale ? `\n근거: ${decision.rationale}` : ''}`, title: `${topic}: ${short(chosen, 60)}`,
        data: { deal_id: dealId, decision_id: decision.id, topic, chosen, kind, options: decision.options, evidence: decision.evidence,
          resolves: decision.resolves, supersedes, section, stage: d.stage },
      });
      return decision;
    },
    // 가정 잠금 — 잠근 뒤에만 계산·보고서가 이 가정을 쓴다. 잠글 때 바로 계산(결정적)
    lockPack(dealId, pack, { evidence = [], note = '' } = {}) {
      const d = get(dealId);
      const prev = d.packs[d.packs.length - 1] || null;
      const p = { id: id('pk'), version: d.packs.length + 1, pack, evidence: evidence.slice(0, 50), note: short(note, 400), supersedes: prev?.id || null, at: new Date().toISOString(), by: user() };
      write({ op: 'pack', dealId, pack: p }, {
        kind: 'invest.assumptions', doc_type: 'assumption_pack', label_kind: 'decision',
        content: `가정 잠금 v${p.version}${note ? ` — ${note}` : ''}\n${JSON.stringify(pack)}`, title: `가정 v${p.version}`,
        data: { deal_id: dealId, pack_id: p.id, version: p.version, evidence: p.evidence, supersedes: p.supersedes },
      });
      const r = compute(pack);
      const result = { id: id('cx'), packId: p.id, vars: r.vars, checks: r.checks, at: new Date().toISOString() };
      const key = (n) => r.vars[n]?.value;
      write({ op: 'compute', dealId, result }, {
        kind: 'invest.compute', doc_type: 'computation', actor_kind: 'agent', actor: 'code:invest/calc', content_origin: 'agent',
        // 소수는 유효숫자 9자리까지만 — 긴 숫자열은 수집기 마스킹(카드번호 검사)에 걸린다
        content: `계산 (가정 v${p.version})\n${Object.entries(r.vars).map(([k, x]) => `${k} = ${Number.isInteger(x.value) ? x.value : Number(x.value.toPrecision(9))}  # ${x.formula}`).join('\n')}\n검산:\n${r.checks.map((c) => `[${c.status}] ${c.message}`).join('\n')}`,
        title: `계산 v${p.version}${key('exit_irr_base') != null ? ` · IRR ${(key('exit_irr_base') * 100).toFixed(1)}%` : ''}`,
        data: { deal_id: dealId, compute_id: result.id, pack_id: p.id, fails: r.checks.filter((c) => c.status === 'fail').length, warns: r.checks.filter((c) => c.status === 'warn').length },
      });
      return { pack: p, compute: result };
    },
    saveSection(dealId, section, { byAgent = true, model = null, cites = [], derivedFrom = [] } = {}) {
      get(dealId);
      const s = { ...section, at: new Date().toISOString(), model };
      write({ op: 'section', dealId, section: s }, {
        kind: 'invest.section', doc_type: 'report_section', actor_kind: byAgent ? 'agent' : 'human', actor: byAgent ? `agent:${user()}` : user(),
        content_origin: byAgent ? 'agent' : 'human', content: s.text, title: s.title, derived_from: derivedFrom,
        data: { deal_id: dealId, section: s.key, status: s.status, cites, verify: s.verify?.summary || null, judge: s.judge?.summary || null, model },
      });
      return s;
    },
    // 대화 — 심사역과 메리가 주고받은 말, 메리의 생각 요약, 객관식 질문. 말·생각·질문은 업무 지도에도(invest.chat)
    chat: (dealId) => get(dealId).chat,
    say(dealId, { role, kind = 'msg', text, section = null, options = null, label = null, data = null }) {
      get(dealId);
      const msg = { id: id('m'), role, kind, text: String(text || '').slice(0, 20000), section, options, label, data, status: kind === 'ask' ? 'open' : null, at: new Date().toISOString(), by: role === 'user' ? user() : `agent:${user()}` };
      const record = kind === 'msg' || kind === 'thinking' || kind === 'ask';
      write({ op: 'chat', dealId, msg }, record ? {
        kind: 'invest.chat', doc_type: kind === 'msg' ? 'chat_message' : kind, actor_kind: role === 'user' ? 'human' : 'agent', actor: msg.by,
        content: msg.text, title: short(msg.text, 80), data: { deal_id: dealId, msg_id: msg.id, kind, role, section, label, options },
      } : null);
      return msg;
    },
    // 지시 — 누가(대표 등) 무엇을 하라고 했는지. 원문·언급된 사람·펀드를 그대로 남긴다(업무 지도: 지시 → 투자 건)
    directive(dealId, { text, instructor, assignees = [], ask = '', fund = '', channel = '' }) {
      get(dealId);
      const directive = { id: id('dr'), text: String(text).slice(0, 8000), instructor: short(instructor, 60) || '지시자 미상', assignees: assignees.map((a) => short(a, 40)).slice(0, 10),
        ask: short(ask, 300), fund: short(fund, 120), channel: short(channel, 60), at: new Date().toISOString(), by: user() };
      write({ op: 'directive', dealId, directive }, {
        kind: 'invest.directive', doc_type: 'directive', content_origin: 'human', author: directive.instructor, content: directive.text,
        title: `${directive.instructor}: ${short(directive.ask || directive.text, 70)}`, label_kind: 'instruction',
        data: { deal_id: dealId, directive_id: directive.id, instructor: directive.instructor, assignees: directive.assignees, ask: directive.ask, fund: directive.fund, channel: directive.channel },
      });
      return directive;
    },
    // Work ID 연결 — PRN(사업)·fund_id·IRN/INV(지급)·EIR. 형식이 틀린 값은 invalid로 남는다(버리지 않음)
    setWork(dealId, patch = {}) {
      const d = get(dealId);
      const work = workContext({ ...(d.work || {}), ...patch, frn: patch.frn ?? d.work?.frn ?? d.frn });
      write({ op: 'deal.update', dealId, patch: { work, frn: work.frn || d.frn } }, {
        kind: 'invest.work', doc_type: 'work_id', content: `Work ID: ${Object.entries(work).filter(([k]) => k !== 'anchor').map(([k, v]) => `${k.toUpperCase()} ${Array.isArray(v) ? v.join(',') : v}`).join(' · ')}`,
        title: `Work ID ${work.anchor || ''}`, data: { deal_id: dealId, work },
      });
      return work;
    },
    // 공식화(Commit) — 탐색 대화의 결정을 사람이 명시적으로 공식 업무로(G-COMMIT). AI는 하지 않는다
    commit(dealId, decisionId, { role, note = '' } = {}) {
      const d = get(dealId);
      const dc = d.decisions.find((x) => x.id === decisionId);
      if (!dc) throw new Error('없는 결정');
      if (dc.supersededBy) throw new Error('이미 바뀐 결정은 공식화할 수 없어요');
      if (!ROLES[role]) throw new Error('공식화하는 역할을 골라 주세요');
      const committed = { by: user(), role, roleLabel: ROLES[role], note: short(note, 300), at: new Date().toISOString() };
      Object.assign(dc, { status: 'committed', committed });
      fs.appendFileSync(OPS, `${JSON.stringify({ op: 'commit', dealId, decisionId, committed, at: committed.at, by: committed.by })}\n`);
      collect?.external({
        kind: 'invest.commit', actor_kind: 'human', actor: user(), platform: 'merry', content_origin: 'human', doc_type: 'work_commit', label_kind: 'commit',
        task_id: `deal-${dealId}`, context: { deal: dealId }, title: `공식화: ${dc.topic}`,
        content: `[WorkCommitted] ${dc.topic} → ${dc.chosen}\n역할: ${ROLES[role]}${note ? `\n메모: ${note}` : ''}`,
        data: { deal_id: dealId, decision_id: decisionId, role, guard: 'G-COMMIT', work: d.work || null, mode: 'committed', correlation_id: `deal-${dealId}` },
      });
      onChange(dealId);
      return dc;
    },
    // 요청 항목 상태 — 받음(received, 근거 id와 함께) / 해당 없음(waived)
    updateItem(dealId, itemId, patch, { note = '' } = {}) {
      const it = get(dealId).items.find((x) => x.id === itemId);
      if (!it) throw new Error('없는 항목');
      write({ op: 'item.update', dealId, itemId, patch }, {
        kind: 'invest.discussion', doc_type: 'status', content: `${short(it.text, 120)} → ${patch.status || ''}${note ? ` (${note})` : ''}`, title: `${patch.status}: ${short(it.text, 60)}`,
        data: { deal_id: dealId, item_id: `${itemId}`, update_of: itemId, status: patch.status, evidence: patch.evidence || [] },
      });
    },
    updateChat(dealId, msgId, patch) { write({ op: 'chat.update', dealId, msgId, patch }, null); },
    // 메리가 한 일(채널 게시·홈 에이전트 위임) — 사람이 승인한 실행은 committed
    action(dealId, { kind, text = '', data = {}, mode = 'committed' }) {
      get(dealId);
      write({ op: 'run', dealId, run: { step: kind, ...data } }, {
        kind: `invest.${kind}`, doc_type: kind, actor_kind: 'agent', actor: `agent:${user()}`, content_origin: 'agent', mode,
        content: String(text).slice(0, 20000), title: short(text, 80), data: { deal_id: dealId, ...data },
      });
    },
    logRun(dealId, run) { write({ op: 'run', dealId, run: { ...run, at: new Date().toISOString() } }, null); },
    latestCompute: (dealId) => { const d = get(dealId); return d.computes[d.computes.length - 1] || null; },
    latestPack: (dealId) => { const d = get(dealId); return d.packs[d.packs.length - 1] || null; },
    fileOf: (dealId, sourceId) => get(dealId).sources.find((s) => s.id === sourceId)?.file || null,
  };
  return api;
}

module.exports = { createDealRoom, KINDS };
