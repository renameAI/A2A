// 업무 흐름 온톨로지 — 수집 로그(events.js, append-only JSONL)를 노드·관계로 바꾼다. 결정적(모델 호출 없음).
//
// 관계 하나 = 계획서 식 (1)의 주장 튜플 a = (s, r, o, I, b, m, π, v, c, ζ) 하나.
//   s·r·o  주어·관계·목적어(노드 id) — 업무 흐름이라 수집 시점에 이미 정해진다(누가 지시했고, 어느 단계가 무엇을 읽었는가)
//   I      유효 구간 — 업무 흐름 사실은 "그때 일어난 일"이라 null
//   b      기록 시점(recorded_at)        m  양태 — 언제나 'recorded'(관찰된 행동). 내용의 참·거짓 주장이 아니다
//   π      원문 구간(span)               v  원문 버전(content_hash)
//   c      맥락(task_id·지시 이벤트)       ζ  출처(platform·actor·doc_type·kind·event_id)
// 내용 주장(원문 속 "A사는 B를 한다")은 여기서 만들지 않는다 — 그건 추출·라벨링 단계(EVENTS.md 원칙 1).
// 하네스 개선(harness.js)의 신호·교훈·버전도 같은 그래프에 노드로 붙는다(docs/HARNESS.md).
const fs = require('fs');
const path = require('path');

// 노드 유형 — 화면의 색·이름표와 같이 쓴다
const TYPES = {
  person: '사람', agent: '에이전트', task: '작업', step: '단계', source: '원문', output: '산출물',
  approval: '승인', channel: '채널', message: '메시지', site: '사이트', platform: '플랫폼', company: '기업',
  harness: '하네스', signal: '신호', lesson: '교훈', probe: '재실행', worktype: '업무 유형',
};
const REL = {
  instructed: '지시함', ran_with: '하네스로 실행', called_in: '에서 불림', used_context: '맥락으로 붙임',
  has_step: '단계', performed: '수행함', read: '읽음', visited: '방문함', queried: '조회함', hosted_on: '위치',
  from_platform: '출처 플랫폼', mentions_company: '기업 언급', viewed: '봄', selected: '골라 냄', within: '안의 구간',
  produced: '만듦', derived_from: '근거', reused: '다시 씀', asked_approval: '승인 요청', decided: '결정함', gated_by: '승인 대상',
  wrote: '씀', in_channel: '채널', posted: '게시함', echo_of: '메아리', signal_on: '신호', based_on: '근거 신호',
  proposes: '제안', adopted: '채택함', next_version: '다음 버전', probed: '재실행함', compares: '비교', recalled: '꺼내 씀', is_a: '업무 유형', sub_of: '상위 유형', applies_to: '적용 유형', narrated: '중간 글',
};
// 메리 딜룸(invest/onto.js) — 투자 건·쟁점·결정·가정·근거
const invest = require('./invest/onto');
Object.assign(TYPES, invest.TYPES);
Object.assign(REL, invest.REL);

const hostOf = (u) => { try { return new URL(u).host; } catch { return ''; } };
const short = (s, n = 60) => { s = String(s ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? `${s.slice(0, n)}…` : s; };
const bare = (h) => String(h || '').replace(/^sha256:/, '');

function createOntology({ root, taskInfo = () => null }) {
  let nodes = new Map();          // id → { id, type, label, props, first, last, n }
  let edges = [];                 // { id, s, r, o, b, m, pi, v, c, zeta }
  let outIdx = new Map();         // id → edge idx[]
  let inIdx = new Map();
  let byHash = new Map();         // 해시 → 노드 id(원문·산출물·지시·메시지)
  let offsets = new Map();        // 로그 파일 → 읽은 바이트
  let lastStep = new Map();       // task id → 마지막 단계 노드 (읽기·승인을 그 단계에 잇는다)
  let stepNo = new Map();
  let extra = [];                 // 하네스 쪽에서 붙인 노드·관계(재구성 때 다시 붙인다)
  let onNew = null;               // 새 이벤트가 들어오면(신호를 다시 계산하라고) — setExtra 안에서는 부르지 않는다
  let quiet = false;

  function reset() {
    nodes = new Map(); edges = []; outIdx = new Map(); inIdx = new Map(); byHash = new Map();
    offsets = new Map(); lastStep = new Map(); stepNo = new Map();
  }

  function node(id, type, label, props = {}, at = null) {
    let n = nodes.get(id);
    if (!n) { n = { id, type, label: label || id, props: {}, first: at, last: at, n: 0 }; nodes.set(id, n); }
    if (label && (n.label === n.id || !n.label)) n.label = label;
    for (const [k, v] of Object.entries(props)) if (v !== undefined && v !== null && v !== '') n.props[k] = v;
    if (at) { if (!n.first || at < n.first) n.first = at; if (!n.last || at > n.last) n.last = at; }
    n.n++;
    return n;
  }
  function edge(s, r, o, ev, extraFields = {}) {
    if (!s || !o || s === o) return null;
    const e = {
      id: edges.length, s, r, o, I: null, b: ev?.recorded_at || null, m: 'recorded',
      pi: extraFields.pi ?? ev?.span ?? null, v: extraFields.v ?? ev?.content_hash ?? null,
      c: { task_id: ev?.task_id ?? null, instruction: ev?.context?.instruction ?? null },
      zeta: { platform: ev?.platform ?? null, actor: ev?.actor ?? null, doc_type: ev?.doc_type ?? null, kind: ev?.kind ?? null, event_id: ev?.event_id ?? null },
    };
    edges.push(e);
    (outIdx.get(s) || outIdx.set(s, []).get(s)).push(e.id);
    (inIdx.get(o) || inIdx.set(o, []).get(o)).push(e.id);
    return e;
  }
  const personId = (actor) => (String(actor || '').startsWith('agent:') ? `agent:${String(actor).slice(6)}` : `person:${actor || '?'}`);
  function actorNode(actor, at) {
    const id = personId(actor);
    return node(id, id.startsWith('agent:') ? 'agent' : 'person', id.startsWith('agent:') ? `${id.slice(6)}의 에이전트` : String(actor || '?'), {}, at).id;
  }
  function siteNode(url, at) {
    const h = hostOf(url);
    return h ? node(`site:${h}`, 'site', h, {}, at).id : null;
  }
  function blobText(hash, max = 4000) {
    const h = bare(hash);
    if (!h) return '';
    try { return fs.readFileSync(path.join(root(), 'blobs', h.slice(0, 2), `${h}.txt`), 'utf8').slice(0, max); } catch { return ''; }
  }
  function sourceNode(ev, type = 'source') {
    const h = bare(ev.content_hash);
    if (!h) return null;
    const id = byHash.get(h) || `src:${h}`;
    const label = ev.title || (ev.source_url ? `${hostOf(ev.source_url)}${new URL(ev.source_url, 'x:/').pathname === '/' ? '' : new URL(ev.source_url, 'x:/').pathname}` : short(blobText(h, 120), 40)) || h.slice(0, 10);
    node(id, nodes.get(id)?.type || type, label, { hash: `sha256:${h}`, url: ev.source_url, doc_type: ev.doc_type, platform: ev.platform,
      origin: ev.content_origin, evidence_candidate: ev.evidence_candidate || undefined }, ev.recorded_at);
    byHash.set(h, id);
    return id;
  }
  const hashNode = (h) => byHash.get(bare(h)) || null;

  // EMA 등록(FRN) 결과 — 알려진 모양({frn, name})만 결정적으로 꺼낸다
  function companiesIn(text) {
    const out = [];
    const re = /"frn"\s*:\s*"([A-Z]{3}-[A-Z]{2}-\d{2}-\d{3})"[^{}]*?"name"\s*:\s*"([^"]{1,80})"/g;
    let m;
    while ((m = re.exec(text)) && out.length < 50) out.push({ frn: m[1], name: m[2] });
    return out;
  }

  function apply(ev) {
    const at = ev.recorded_at;
    const task = ev.task_id ? `task:${ev.task_id}` : null;
    switch (ev.kind) {
      case 'agent.instruction': {
        const t = taskInfo(ev.task_id);
        const text = blobText(ev.content_hash, 400);
        node(task, 'task', t?.title || short(text, 50) || ev.task_id, {
          instruction: short(text, 400), ihash: nodes.get(task)?.props.ihash || ev.content_hash, user: ev.actor,
          status: ev.data?.dropped ? 'dropped' : t?.status, harness: ev.data?.harness?.edit || ev.data?.harness?.base,
        }, at);
        if (ev.content_hash) byHash.set(bare(ev.content_hash), task);
        edge(actorNode(ev.actor, at), 'instructed', task, ev);
        if (ev.context?.thread) edge(task, 'called_in', node(`channel:${ev.tenant}/${ev.context.thread.cid}`, 'channel', `#${ev.context.thread.channel}`, {}, at).id, ev);
        for (const h of ev.derived_from || []) { const n = hashNode(h); if (n && n !== task) edge(task, 'used_context', n, ev, { v: h }); }
        const hv = ev.data?.harness;
        // 채택한 버전으로 돌았으면 그 버전 노드로(교훈·신호까지 이어진다), 아니면 기본 하네스 지문으로
        if (hv?.base) edge(task, 'ran_with', node(hv.edit ? `harness:${hv.edit}` : `harness:${hv.base}`, 'harness',
          hv.edit ? `하네스 ${hv.edit}` : `기본 하네스 ${hv.base}`, { base: hv.base, ema: hv.ema }, at).id, ev);
        for (const id of ev.data?.recall || []) if (nodes.has(id) && id !== task) edge(task, 'recalled', id, ev);   // 시작할 때 꺼내 붙인 지난 기록
        break;
      }
      case 'agent.tool_call': {
        if (!task) break;
        node(task, 'task', ev.task_id, {}, at);
        const k = (stepNo.get(task) || 0) + 1;
        stepNo.set(task, k);
        const tool = ev.data?.tool || '?';
        const input = ev.data?.input || {};
        const hint = input.url || input.channel || input.query || input.text || input.title || (input.index != null ? `[${input.index}]` : '');
        const id = `step:${ev.event_id}`;
        node(id, 'step', `${k}. ${tool}${hint ? ` ${short(hint, 30)}` : ''}`, { tool, ok: ev.data?.ok, summary: short(ev.data?.summary, 300), input: short(JSON.stringify(input), 300), order: k }, at);
        edge(task, 'has_step', id, ev);
        edge(actorNode(ev.actor, at), 'performed', id, ev);
        if (input.url) { const s = siteNode(input.url, at); if (s) edge(id, 'visited', s, ev); }
        if (tool.startsWith('ema__')) edge(id, 'queried', node('platform:ema-station', 'platform', 'EMA Station', {}, at).id, ev);
        if (tool === 'post_to_channel' && input.channel) edge(id, 'posted', node(`channel:${ev.tenant}/#${input.channel}`, 'channel', `#${String(input.channel).replace(/^#/, '')}`, {}, at).id, ev);
        lastStep.set(task, id);
        break;
      }
      case 'agent.read': {
        const isTool = ev.doc_type === 'mcp_tool_result';
        const src = sourceNode(ev, 'source');
        if (!src) break;
        if (task) {
          node(task, 'task', ev.task_id, {}, at);
          edge(lastStep.get(task) || task, 'read', src, ev);
        }
        if (isTool) {
          const p = node(`platform:${ev.platform}`, 'platform', ev.platform === 'ema-station' ? 'EMA Station' : ev.platform, {}, at).id;
          edge(src, 'from_platform', p, ev);
          for (const c of companiesIn(blobText(ev.content_hash, 200000))) {
            edge(src, 'mentions_company', node(`company:${c.frn}`, 'company', c.name, { frn: c.frn }, at).id, ev);
          }
        } else {
          const s = siteNode(ev.source_url, at);
          if (s) edge(src, 'hosted_on', s, ev);
        }
        break;
      }
      case 'web.snapshot': {
        const src = sourceNode(ev);
        if (!src) break;
        const s = siteNode(ev.source_url, at);
        if (s) edge(src, 'hosted_on', s, ev);
        if (ev.actor_kind === 'human') edge(actorNode(ev.actor, at), 'viewed', src, ev);
        break;
      }
      case 'web.selection': {
        const src = sourceNode(ev, 'source');
        if (!src) break;
        nodes.get(src).props.doc_type = 'selection';
        edge(actorNode(ev.actor, at), 'selected', src, ev);
        const within = hashNode(ev.span?.within);
        if (within) edge(src, 'within', within, ev);
        break;
      }
      case 'agent.text':
      case 'agent.page': {
        const h = bare(ev.content_hash);
        if (!h) break;
        const id = `out:${h}`;
        node(id, 'output', ev.kind === 'agent.page' ? `📄 ${ev.title || '결과 페이지'}` : short(blobText(h, 200), 46) || '답변',
          { hash: `sha256:${h}`, doc_type: ev.doc_type, cited: ev.data?.cited_urls?.length || undefined }, at);
        byHash.set(h, id);
        // 도구를 부르기 전에 쓴 중간 글(agent_narration)은 답과 구분한다 — 신호·꺼내 붙이기는 답(produced)만 본다
        if (task) { node(task, 'task', ev.task_id, {}, at); edge(task, ev.doc_type === 'agent_narration' ? 'narrated' : 'produced', id, ev); }
        for (const d of ev.derived_from || []) { const n = hashNode(d); if (n && n !== id) edge(id, 'derived_from', n, ev, { v: d }); }
        break;
      }
      case 'agent.output_ref': {
        const n = hashNode(ev.content_hash) || (ev.task_id && `task:${ev.task_id}`);
        if (n && nodes.has(n)) edge(actorNode(ev.actor, at), 'reused', n, ev);
        break;
      }
      case 'agent.run_end': {
        if (!task) break;
        const t = taskInfo(ev.task_id);
        node(task, 'task', ev.task_id, { ended: at, status: t?.status || nodes.get(task)?.props.status || 'ended' }, at);
        break;
      }
      case 'approval.decision': {
        const id = `appr:${ev.event_id}`;
        const prompt = String(ev.data?.prompt || '');
        node(id, 'approval', `${ev.approval_status === 'approved' ? '✓' : '✕'} ${short(prompt.split('\n')[0], 44)}`,
          { status: ev.approval_status, prompt: short(prompt, 600), simulated: ev.data?.simulated || undefined }, at);
        edge(actorNode(ev.actor, at), 'decided', id, ev);
        if (task) { node(task, 'task', ev.task_id, {}, at); edge(task, 'asked_approval', id, ev); }
        const st = task && lastStep.get(task);
        if (st) edge(st, 'gated_by', id, ev);
        break;
      }
      case 'chat.message': {
        const h = bare(ev.content_hash);
        const id = ev.source_url ? `msg:${ev.source_url.replace('a2a-chat://', '')}` : `msg:${ev.event_id}`;
        node(id, 'message', short(blobText(h, 160), 46) || '메시지', { hash: h ? `sha256:${h}` : undefined, url: ev.source_url, observation: ev.observation,
          evidence_candidate: ev.evidence_candidate || undefined, by_agent: ev.actor_kind === 'agent' || undefined }, at);
        if (h) byHash.set(h, id);
        edge(actorNode(ev.actor || ev.author, at), 'wrote', id, ev);
        const m = /^a2a-chat:\/\/([^/]+)\/(\d+)\//.exec(ev.source_url || '');
        if (m) edge(id, 'in_channel', node(`channel:${m[1]}/${m[2]}`, 'channel', ev.context?.thread?.channel ? `#${ev.context.thread.channel}` : `채널 ${m[2]}`, {}, at).id, ev);
        if (task) edge(`task:${ev.task_id}`, 'posted', id, ev);
        for (const d of ev.derived_from || []) { const n = hashNode(d); if (n && n !== id) edge(id, ev.observation === 'echo' ? 'echo_of' : 'derived_from', n, ev, { v: d }); }
        break;
      }
      case 'chat.deleted': {
        const id = ev.source_url ? `msg:${ev.source_url.replace('a2a-chat://', '')}` : null;
        if (id && nodes.has(id)) nodes.get(id).props.deleted = true;
        break;
      }
      default: {
        if (String(ev.kind).startsWith('invest.')) { invest.applyInvest(ev, { node, edge, actorNode, nodes }); break; }
        // 어댑터(바깥 플랫폼·루틴 에이전트) — <platform>.<동사>
        if (!ev.platform || ev.kind === 'collector.start' || ev.kind === 'agent.note') break;
        if (!/^(web|agent|chat|approval|collector)\./.test(ev.kind)) {
          const src = sourceNode(ev, ev.content_origin === 'agent' ? 'output' : 'source');
          if (!src) break;
          edge(src, 'from_platform', node(`platform:${ev.platform}`, 'platform', ev.platform, {}, at).id, ev);
          if (ev.actor || ev.author) edge(actorNode(ev.actor || ev.author, at), 'wrote', src, ev);
          for (const d of ev.derived_from || []) { const n = hashNode(d); if (n && n !== src) edge(src, 'derived_from', n, ev, { v: d }); }
        }
      }
    }
  }

  // 로그를 이어서 읽는다(파일마다 읽은 자리까지). 잘린 마지막 줄은 다음에 다시
  function refresh() {
    const dir = root() && path.join(root(), 'log');
    if (!dir || !fs.existsSync(dir)) return 0;
    let added = 0;
    for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.jsonl')).sort()) {
      const file = path.join(dir, f);
      const size = fs.statSync(file).size;
      const from = offsets.get(file) || 0;
      if (size <= from) continue;
      const fd = fs.openSync(file, 'r');
      const buf = Buffer.alloc(size - from);
      fs.readSync(fd, buf, 0, buf.length, from);
      fs.closeSync(fd);
      const text = buf.toString('utf8');
      const end = text.lastIndexOf('\n');
      if (end < 0) continue;
      for (const line of text.slice(0, end).split('\n')) {
        if (!line) continue;
        try { apply(JSON.parse(line)); added++; } catch { /* 깨진 줄 */ }
      }
      offsets.set(file, from + Buffer.byteLength(text.slice(0, end + 1)));
    }
    if (added) for (const x of extra) applyExtra(x);
    if (added && onNew && !quiet) { quiet = true; try { onNew(); } finally { quiet = false; } }
    return added;
  }

  // 하네스 쪽 노드·관계 — 신호·교훈·버전·재실행. 다시 만들 때도 붙도록 기억해 둔다
  function applyExtra(x) {
    node(x.id, x.type, x.label, x.props || {}, x.at || null);
    for (const [r, o] of x.links || []) if (nodes.has(o) || o.startsWith('harness:')) {
      if (!nodes.has(o)) node(o, 'harness', o.replace('harness:', '하네스 '), {}, x.at || null);
      if (!edges.some((e) => e.s === x.id && e.r === r && e.o === o)) {
        edge(x.id, r, o, { recorded_at: x.at, kind: `harness.${x.type}`, platform: 'a2a-harness', event_id: x.id });
      }
    }
  }
  function setExtra(list) {
    extra = list;
    reset();
    const q = quiet; quiet = true;
    try { refresh(); } finally { quiet = q; }
    for (const x of extra) applyExtra(x);
  }

  const view = (n) => n && ({ id: n.id, type: n.type, typeLabel: TYPES[n.type] || n.type, label: n.label, first: n.first, last: n.last });
  const edgeView = (e, dir) => {
    const other = nodes.get(dir === 'out' ? e.o : e.s);
    return { id: e.id, r: e.r, rel: REL[e.r] || e.r, b: e.b, v: e.v, pi: e.pi, c: e.c, zeta: e.zeta, node: view(other) };
  };

  return {
    TYPES, REL, refresh, setExtra, blobText, onEvents: (fn) => { onNew = fn; },
    summary() {
      refresh();
      const types = {};
      for (const n of nodes.values()) types[n.type] = (types[n.type] || 0) + 1;
      const recent = [...nodes.values()].filter((n) => ['task', 'output', 'company', 'lesson', 'signal'].includes(n.type))
        .sort((a, b) => String(b.last).localeCompare(String(a.last))).slice(0, 12).map(view);
      return { nodes: nodes.size, edges: edges.length, types, typeLabels: TYPES, recent };
    },
    list(type, q = '', limit = 300) {
      refresh();
      const ql = String(q || '').toLowerCase();
      return [...nodes.values()]
        .filter((n) => (!type || n.type === type) && (!ql || n.label.toLowerCase().includes(ql) || n.id.toLowerCase().includes(ql) ||
          Object.values(n.props).some((v) => String(v).toLowerCase().includes(ql))))
        .sort((a, b) => String(b.last).localeCompare(String(a.last))).slice(0, limit)
        .map((n) => ({ ...view(n), degree: (outIdx.get(n.id)?.length || 0) + (inIdx.get(n.id)?.length || 0) }));
    },
    node(id) {
      refresh();
      const n = nodes.get(id);
      if (!n) return null;
      const out = (outIdx.get(id) || []).map((i) => edgeView(edges[i], 'out'));
      const back = (inIdx.get(id) || []).map((i) => edgeView(edges[i], 'in'));
      const hash = n.props.hash || (n.type === 'task' ? null : null);
      return { ...view(n), props: n.props, out, back, text: hash ? blobText(hash, 6000) : '' };
    },
    // 이웃 그래프 — center가 없으면 전체(많으면 연결이 많은 순으로 자른다)
    graph({ center = null, depth = 2, types = null, limit = 400 } = {}) {
      refresh();
      let keep;
      if (center && nodes.has(center)) {
        keep = new Set([center]);
        let frontier = [center];
        for (let d = 0; d < depth; d++) {
          const next = [];
          for (const id of frontier) {
            for (const i of [...(outIdx.get(id) || []), ...(inIdx.get(id) || [])]) {
              const e = edges[i];
              const o = e.s === id ? e.o : e.s;
              if (!keep.has(o) && keep.size < limit) { keep.add(o); next.push(o); }
            }
          }
          frontier = next;
        }
      } else {
        const deg = (id) => (outIdx.get(id)?.length || 0) + (inIdx.get(id)?.length || 0);
        keep = new Set([...nodes.values()].filter((n) => !types || types.includes(n.type)).sort((a, b) => deg(b.id) - deg(a.id)).slice(0, limit).map((n) => n.id));
      }
      const es = [];
      const seen = new Set();
      for (const e of edges) {
        if (!keep.has(e.s) || !keep.has(e.o)) continue;
        const k = `${e.s}|${e.r}|${e.o}`;
        if (seen.has(k)) continue;
        seen.add(k);
        es.push({ s: e.s, o: e.o, r: e.r });
      }
      return { nodes: [...keep].map((id) => view(nodes.get(id))), edges: es };
    },
    // 주장 튜플(JSONL 한 줄씩) — 온톨로지 쪽으로 넘길 때
    claims() {
      refresh();
      return edges.map((e) => ({ s: e.s, r: e.r, o: e.o, I: e.I, b: e.b, m: e.m, pi: e.pi, v: e.v, c: e.c, zeta: e.zeta,
        s_type: nodes.get(e.s)?.type, o_type: nodes.get(e.o)?.type }));
    },
    // 하네스(신호 찾기)용 원본 접근
    raw: () => { refresh(); return { nodes, edges, outIdx, inIdx, byHash }; },
  };
}

module.exports = { createOntology, TYPES, REL };
