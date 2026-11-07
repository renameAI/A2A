// 업무 지도에 투자 의사결정을 올린다 — 수집 로그의 invest.* 이벤트를 노드·관계로(결정적, 모델 호출 없음).
//
//   투자 건(deal) ← 작업(task:deal-…, 업무 유형 "투자")
//   근거(claim, 자료의 쪽·인용) → 자료(source)
//   쟁점·질문·강점·우려(issue) —based_on→ 근거,  답 —answers→ 질문
//   결정(decision) —based_on→ 근거·쟁점, —resolves→ 쟁점, —supersedes→ 이전 결정
//   가정(assumption) —supersedes→ 이전 가정,  계산(output) —computed_from→ 가정
//   보고서 섹션(output) —derived_from→ 근거
// 관계 하나는 ontology.js의 주장 튜플 a = (s, r, o, I, b, m, π, v, c, ζ)를 그대로 쓴다(edge()가 채운다).
const TYPES = { deal: '투자 건', issue: '쟁점', decision: '결정', assumption: '가정', claim: '근거', stage: '투자 단계', directive: '지시', project: '사업(PRN)', fund: '펀드', disbursement: '지급(IRN)' };
const REL = {
  about_deal: '투자 건', opened_deal: '투자 건 등록', in_stage: '단계', uploaded: '올림', quotes: '인용',
  raised: '제기함', answers: '답함', resolves: '해소함', supersedes: '대체함', locked: '잠금', computed_from: '계산 근거',
  registered_as: 'FRN 등록', assigned: '맡김', fulfilled_by: '받은 자료',
  in_project: '사업(PRN)', funded_by: '재원', disbursed_as: '지급 건', committed: '공식화함', delegated: '에이전트에게 맡김',
};
const STAGE_LABEL = { sourcing: '발굴', internal_ir: '내부 IR', impact_committee: '임팩트 심의위', investment_committee: '본투심', closing: '집행', post: '사후관리', dropped: '중단' };
const KIND_LABEL = { question: '질문', issue: '쟁점', bull: '강점', bear: '우려', answer: '답', comment: '코멘트', request: '받을 자료' };
const short = (s, n = 46) => { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? `${s.slice(0, n)}…` : s; };

// h = { node, edge, actorNode, nodes } — ontology.js가 넘긴다
function applyInvest(ev, h) {
  const d = ev.data || {};
  const at = ev.recorded_at;
  if (!d.deal_id) return;
  const deal = `deal:${d.deal_id}`;
  const task = `task:deal-${d.deal_id}`;
  const by = h.actorNode(ev.actor, at);
  const link = (s, r, o, extra) => (h.nodes.has(o) ? h.edge(s, r, o, ev, extra) : null);
  const ensureDeal = () => {
    if (!h.nodes.has(deal)) h.node(deal, 'deal', d.company || '투자 건', {}, at);
    if (!h.nodes.has(task)) { h.node(task, 'task', `투자 심사 · ${h.nodes.get(deal).label}`, { instruction: `투자 심사 · ${h.nodes.get(deal).label}` }, at); h.edge(task, 'about_deal', deal, ev); }
  };
  switch (ev.kind) {
    case 'invest.deal': {
      h.node(deal, 'deal', d.company, { fund: d.fund, amount: d.amount, method: d.method, frn: d.frn }, at);
      ensureDeal();
      h.edge(by, 'opened_deal', deal, ev);
      // FRN 등록 기업 — 에이전트가 EMA에서 본 같은 기업 노드(company:<FRN>)와 이어진다
      if (d.frn) h.edge(deal, 'registered_as', h.node(`company:${d.frn}`, 'company', d.company, { frn: d.frn }, at).id, ev);
      break;
    }
    case 'invest.directive': {
      ensureDeal();
      const dr = h.node(`directive:${d.directive_id}`, 'directive', short(ev.title, 46), { instructor: d.instructor, ask: d.ask, fund: d.fund, channel: d.channel, hash: ev.content_hash }, at).id;
      h.edge(h.actorNode(d.instructor, at), 'instructed', dr, ev);
      h.edge(dr, 'about_deal', deal, ev);
      h.edge(dr, 'instructed', task, ev);
      for (const a of d.assignees || []) h.edge(dr, 'assigned', h.actorNode(String(a).replace(/^@/, ''), at), ev);
      break;
    }
    case 'invest.stage': {
      ensureDeal();
      h.nodes.get(deal).props.stage = d.stage;
      h.edge(deal, 'in_stage', h.node(`stage:${d.stage}`, 'stage', STAGE_LABEL[d.stage] || d.stage, {}, at).id, ev);
      break;
    }
    case 'invest.source': {
      ensureDeal();
      const src = h.node(`doc:${d.source_id}`, 'source', d.name, { kind: d.kind, file_sha256: d.file_sha256, hash: ev.content_hash }, at).id;
      h.edge(src, 'about_deal', deal, ev);
      h.edge(by, 'uploaded', src, ev);
      break;
    }
    case 'invest.fact': {
      ensureDeal();
      const f = h.node(`fact:${d.fact_id}`, 'claim', `${d.basis === 'claim' ? '(미확인) ' : ''}${short(ev.title, 40)}`, { key: d.key, value: d.value, unit: d.unit, period: d.period, page: d.page, basis: d.basis, hash: ev.content_hash }, at).id;
      h.edge(f, 'about_deal', deal, ev);
      if (d.source_id) link(f, 'quotes', `doc:${d.source_id}`);
      break;
    }
    case 'invest.discussion': {
      ensureDeal();
      if (d.update_of) {         // 상태 변경(받음·해당 없음) — 같은 노드에 상태와 받은 근거를 잇는다
        const n = h.nodes.get(`issue:${d.update_of}`);
        if (n) { n.props.status = d.status; for (const x of d.evidence || []) link(`issue:${d.update_of}`, 'fulfilled_by', `fact:${x}`); }
        break;
      }
      const it = h.node(`issue:${d.item_id}`, 'issue', `${KIND_LABEL[d.kind] || ''} · ${short(ev.title, 38)}`, { kind: d.kind, section: d.section, options: d.options, from: d.from, priority: d.priority, status: d.kind === 'request' ? 'open' : undefined, hash: ev.content_hash }, at).id;
      h.edge(it, 'about_deal', deal, ev);
      h.edge(by, 'raised', it, ev);
      if (d.reply_to) link(it, 'answers', `issue:${d.reply_to}`);
      for (const x of d.evidence || []) link(it, 'based_on', x.startsWith('it_') ? `issue:${x}` : `fact:${x}`);
      break;
    }
    case 'invest.decision': {
      ensureDeal();
      const dc = h.node(`decision:${d.decision_id}`, 'decision', short(`${d.topic}: ${d.chosen}`, 46),
        { topic: d.topic, chosen: d.chosen, kind: d.kind, options: d.options, stage: d.stage, section: d.section, mode: d.mode || 'conversation', hash: ev.content_hash }, at).id;
      h.edge(dc, 'about_deal', deal, ev);
      h.edge(by, 'decided', dc, ev);
      for (const x of d.evidence || []) link(dc, 'based_on', x.startsWith('it_') ? `issue:${x}` : x.startsWith('dc_') ? `decision:${x}` : `fact:${x}`);
      for (const x of d.resolves || []) link(dc, 'resolves', `issue:${x}`);
      if (d.supersedes) { link(dc, 'supersedes', `decision:${d.supersedes}`); const old = h.nodes.get(`decision:${d.supersedes}`); if (old) old.props.superseded = true; }
      break;
    }
    case 'invest.assumptions': {
      ensureDeal();
      const pk = h.node(`pack:${d.pack_id}`, 'assumption', `가정 v${d.version}`, { version: d.version, hash: ev.content_hash }, at).id;
      h.edge(pk, 'about_deal', deal, ev);
      h.edge(by, 'locked', pk, ev);
      for (const x of d.evidence || []) link(pk, 'based_on', `fact:${x}`);
      if (d.supersedes) link(pk, 'supersedes', `pack:${d.supersedes}`);
      break;
    }
    case 'invest.compute': {
      ensureDeal();
      const cx = h.node(`calc:${d.compute_id}`, 'output', short(ev.title, 40), { fails: d.fails, warns: d.warns, hash: ev.content_hash }, at).id;
      link(cx, 'computed_from', `pack:${d.pack_id}`);
      h.edge(task, 'produced', cx, ev);
      break;
    }
    case 'invest.section': {
      ensureDeal();
      const sid = `section:${d.deal_id}:${d.section}:${ev.event_id}`;
      h.node(sid, 'output', `📄 ${short(ev.title, 38)}`, { section: d.section, status: d.status, verify: d.verify, judge: d.judge, model: d.model, hash: ev.content_hash }, at);
      h.edge(task, 'produced', sid, ev);
      h.edge(by, 'wrote', sid, ev);
      for (const x of d.cites || []) link(sid, 'derived_from', `fact:${x}`);
      break;
    }
    // Work ID — 투자 건을 PRN·펀드·지급(IRN/INV) 노드에 잇는다(같은 ID면 다른 업무와 같은 노드)
    case 'invest.work': {
      ensureDeal();
      const w = d.work || {};
      h.nodes.get(deal).props.work = w.anchor || undefined;
      if (w.frn) h.edge(deal, 'registered_as', h.node(`company:${w.frn}`, 'company', h.nodes.get(deal).label, { frn: w.frn }, at).id, ev);
      if (w.prn) h.edge(deal, 'in_project', h.node(`prn:${w.prn}`, 'project', `PRN ${w.prn}`, {}, at).id, ev);
      if (w.fund_id) h.edge(deal, 'funded_by', h.node(`fund:${w.fund_id}`, 'fund', w.fund_id, {}, at).id, ev);
      for (const x of [].concat(w.irn || [], w.inv || [])) h.edge(deal, 'disbursed_as', h.node(`disb:${x}`, 'disbursement', x, {}, at).id, ev);
      break;
    }
    // 공식화(WorkCommitted) — 탐색 대화의 결정이 사람의 명시적 Commit으로 공식 업무가 됨(G-COMMIT)
    case 'invest.commit': {
      const n = h.nodes.get(`decision:${d.decision_id}`);
      if (!n) break;
      n.props.mode = 'committed';
      n.props.committed_role = d.role;
      if (!n.label.startsWith('✔')) n.label = `✔ ${n.label}`;
      h.edge(by, 'committed', n.id, ev);
      break;
    }
    // 팀 채널 게시(심사역 승인) — 투자 건 작업 —게시함→ 채널
    case 'invest.post': {
      ensureDeal();
      if (d.channel) h.edge(task, 'posted', h.node(`channel:${ev.tenant}/#${d.channel}`, 'channel', `#${d.channel}`, {}, at).id, ev);
      break;
    }
    // 홈 에이전트에게 맡김 — 그 작업이 읽은 페이지가 이 투자 건의 근거가 된다
    case 'invest.delegate': {
      ensureDeal();
      if (d.agent_task_id) h.edge(task, 'delegated', h.node(`task:${d.agent_task_id}`, 'task', d.agent_task_id, {}, at).id, ev);
      break;
    }
    case 'invest.chat': {
      ensureDeal();
      const label = d.kind === 'thinking' ? `💭 ${short(ev.title, 40)}` : d.kind === 'ask' ? `❓ ${short(ev.title, 40)}` : short(ev.title, 44);
      const m = h.node(`chat:${d.msg_id}`, 'message', label, { kind: d.kind, role: d.role, section: d.section, hash: ev.content_hash }, at).id;
      h.edge(m, 'about_deal', deal, ev);
      h.edge(by, 'wrote', m, ev);
      break;
    }
    default: break;
  }
}

module.exports = { applyInvest, TYPES, REL };
