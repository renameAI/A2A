// SSoT 임시 발행 — 업무 지도의 업무 단위(작업·투자 건·결정·지시)에 임시 Work ID를 발행한다. docs/WORKID.md.
//
// 정식 정책이 확정되기 전까지의 임시 운영(policy: provisional-v0):
//   ID       TMP-<종류>-<YYMM>-<순번>   WE 작업(Work Event) · DL 투자 건 · DC 결정 · DR 지시
//            TMP- 접두어 = 임시. 정책이 확정되면 Registry가 정식 ID를 주고, 이 ID는 external_id_mapping으로 옮긴다
//   불변     한 번 발행한 ID는 그 업무(ref)에 고정. 다시 발행하지 않는다(멱등) — 바뀌는 건 상태·계층뿐
//   담는 것  업무 유형과 그 SSoT(원본 시스템·기준 ID·가드), Work Context(FRN·PRN…), 계층(conversation·shadow·committed),
//            원본 위치(업무 지도 노드·이벤트 시각) — 원문은 담지 않는다(가리키기만)
// 자동 발행: 업무 지도를 볼 때마다(sync) 아직 ID가 없는 업무 단위에 발행한다. 사람이 누른 발행은 issued_by에 사람이 남는다.
const fs = require('fs');
const path = require('path');

const KIND = { task: 'WE', deal: 'DL', decision: 'DC', directive: 'DR' };
const KIND_LABEL = { WE: '작업', DL: '투자 건', DC: '결정', DR: '지시' };
const POLICY = 'provisional-v0';

function createSsotRegistry({ dir, onto, worktypes = () => null }) {
  fs.mkdirSync(dir, { recursive: true });
  const FILE = path.join(dir, 'registry.jsonl');
  const byRef = new Map();
  const byId = new Map();
  const seq = new Map();            // "WE-2610" → 마지막 순번
  try {
    for (const line of fs.readFileSync(FILE, 'utf8').split('\n')) {
      if (!line) continue;
      try {
        const r = JSON.parse(line);
        byRef.set(r.ref, r); byId.set(r.id, r);
        const [, k, ym, n] = r.id.match(/^TMP-([A-Z]{2})-(\d{4})-(\d{4})$/) || [];
        if (k) seq.set(`${k}-${ym}`, Math.max(seq.get(`${k}-${ym}`) || 0, Number(n)));
      } catch { /* 깨진 줄 */ }
    }
  } catch { /* 처음 */ }

  const ymOf = (iso) => { const d = iso ? new Date(iso) : new Date(); return `${String(d.getFullYear()).slice(2)}${String(d.getMonth() + 1).padStart(2, '0')}`; };
  const out = (raw, id, r) => (raw.outIdx.get(id) || []).map((i) => raw.edges[i]).filter((e) => !r || e.r === r);
  const inn = (raw, id, r) => (raw.inIdx.get(id) || []).map((i) => raw.edges[i]).filter((e) => !r || e.r === r);

  // 업무 단위의 지금 모습 — 유형·SSoT·Work Context·계층·상태(발행 뒤에도 바뀔 수 있는 것)
  function live(raw, n) {
    const wt = worktypes();
    let task = n.type === 'task' ? n.id : null;
    let deal = n.type === 'deal' ? n.id : null;
    if (!deal) deal = out(raw, n.id, 'about_deal')[0]?.o || null;
    if (deal && !task) task = inn(raw, deal, 'about_deal').find((e) => e.s.startsWith('task:'))?.s || null;
    const typeEdge = task ? out(raw, task, 'is_a')[0] : null;
    const type = typeEdge ? wt?.get(typeEdge.o) : null;
    const ssot = type ? wt.ssotOf(type.id) : null;
    const dn = deal ? raw.nodes.get(deal) : null;
    const frn = dn?.props.frn || (deal ? out(raw, deal, 'registered_as')[0]?.o?.replace('company:', '') : null) || null;
    const prn = deal ? out(raw, deal, 'in_project')[0]?.o?.replace('prn:', '') : null;
    const fund = deal ? out(raw, deal, 'funded_by')[0]?.o?.replace('fund:', '') : null;
    // 계층: 결정은 공식화 여부, 작업은 에이전트가 한 일이면 shadow, 사람만이면 conversation
    let mode = 'conversation';
    if (n.type === 'decision') mode = n.props.mode === 'committed' ? 'committed' : 'conversation';
    else if (n.type === 'task' && out(raw, n.id, 'has_step').length) mode = 'shadow';
    else if (n.type === 'deal' && inn(raw, deal, 'about_deal').some((e) => raw.nodes.get(e.s)?.props.mode === 'committed')) mode = 'committed';
    return {
      title: n.label, status: n.props.status || n.props.stage || null,
      worktype: type ? { id: type.id, name: type.name } : null,
      ssot: ssot ? { system: ssot.system || 'SSoT 미정', anchor: ssot.anchor, guards: ssot.guards, defaulted: !!ssot.defaulted } : { system: '업무 유형 미정', anchor: null, guards: ['G-SHADOW'], defaulted: true },
      work: { frn, prn, fund_id: fund, anchor: frn ? `FRN:${frn}` : prn ? `PRN:${prn}` : null },
      mode, first: n.first, last: n.last, relations: (raw.outIdx.get(n.id) || []).length + (raw.inIdx.get(n.id) || []).length,
    };
  }

  function issue(raw, n, by = 'auto') {
    if (byRef.has(n.id)) return byRef.get(n.id);
    const k = KIND[n.type];
    if (!k) throw new Error('발행 대상이 아니에요(작업·투자 건·결정·지시만)');
    const ym = ymOf(n.first);
    const no = (seq.get(`${k}-${ym}`) || 0) + 1;
    seq.set(`${k}-${ym}`, no);
    const l = live(raw, n);
    const r = { id: `TMP-${k}-${ym}-${String(no).padStart(4, '0')}`, ref: n.id, kind: k, kindLabel: KIND_LABEL[k], policy: POLICY,
      issued_at: new Date().toISOString(), issued_by: by, at_issue: { title: l.title, worktype: l.worktype, ssot: l.ssot, work: l.work, mode: l.mode } };
    fs.appendFileSync(FILE, `${JSON.stringify(r)}\n`);
    byRef.set(r.ref, r); byId.set(r.id, r);
    return r;
  }

  const eligible = (n) => !!KIND[n.type] && !(n.type === 'task' && !n.first);

  return {
    POLICY,
    // 아직 ID가 없는 업무 단위에 자동 발행 — 업무 지도를 볼 때 부른다. 새로 발행한 수를 돌려준다
    sync() {
      const raw = onto.raw();
      let n = 0;
      for (const node of raw.nodes.values()) if (eligible(node) && !byRef.has(node.id)) { issue(raw, node, 'auto'); n++; }
      return n;
    },
    issue(ref, by) {
      const raw = onto.raw();
      const n = raw.nodes.get(ref);
      if (!n) throw new Error('없는 노트예요');
      return issue(raw, n, by);
    },
    // 노트 한 장의 SSoT — 발행 기록 + 지금 모습(바뀐 상태·계층)
    get(ref) {
      const raw = onto.raw();
      const n = raw.nodes.get(ref);
      if (!n) return null;
      const r = byRef.get(ref) || null;
      return { eligible: eligible(n), record: r, live: eligible(n) ? live(raw, n) : null };
    },
    stats() {
      const raw = onto.raw();
      let total = 0;
      for (const node of raw.nodes.values()) if (eligible(node)) total++;
      const byKind = {};
      for (const r of byId.values()) byKind[r.kind] = (byKind[r.kind] || 0) + 1;
      return { issued: byId.size, eligible: total, byKind, policy: POLICY };
    },
    list: () => [...byId.values()],
  };
}

module.exports = { createSsotRegistry, KIND, POLICY };
