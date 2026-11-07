// 업무 이벤트 수집 — 브라우저를 지나가는 업무(웹 탭, 에이전트 작업, 승인, 팀 채팅)를 출처가 붙은 원자료로 남긴다.
//
// 지식이 아니라 원자료다. 주장 튜플 a=(s,r,o,I,b,m,π,v,c,ζ) 중 수집 시점에 아는 것(b·π·v·c·ζ)만 채우고,
// s·r·o·I·m은 나중 추출 단계가 채운다(docs/EVENTS.md).
//
// 저장(userData/events/):
//   log/YYYY-MM-DD.jsonl   이벤트 한 줄씩, 덧붙이기만 한다(append-only). 날짜는 UTC.
//   blobs/ab/<sha256>.txt  원문(마스킹 뒤) — 콘텐츠 해시가 이름이라 같은 원문은 한 번만 저장된다.
//   blobs/ab/<sha256>.json 그 원문을 처음 본 이벤트·출처 종류(content_origin)
//   chat-seen.jsonl        본 채팅 메시지(tenant:cid:seq → 해시) — 다시 켜도 같은 메시지를 또 쌓지 않는다
//
// 원칙: 수집이 앱을 멈추게 하면 안 된다. 모든 공개 함수는 예외를 삼키고 경고만 남긴다.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const SCHEMA_VERSION = 1;
const WORLD = 1921;                 // page-tools(1919)·passwords와 다른 격리 월드 — 에이전트의 [번호] 표시를 건드리지 않는다
const AGENT_VIEW_CHARS = 12000;     // page-tools SNAPSHOT의 MAX_TEXT — 에이전트가 실제로 본 앞부분(π)
const CONFIG_FILE = path.join(__dirname, 'collect.config.json');

// ── 마스킹 ── 저장 전에, 해시 전에. 원문 블롭에도 마스킹된 글만 남는다.
function luhn(digits) {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2) { d *= 2; if (d > 9) d -= 9; }
    sum += d;
  }
  return sum % 10 === 0;
}
const MASKS = [
  // 카드번호: 13~19자리(공백·하이픈 허용) + Luhn 검사 — 전화번호·주문번호 오탐을 줄인다
  ['card', /\b(?:\d[ -]?){12,18}\d\b/g, (m) => luhn(m.replace(/\D/g, ''))],
  // 주민·외국인등록번호: YYMMDD-[1-8]XXXXXX (2020년 이후 번호는 검증숫자가 없어 모양만 본다)
  ['rrn', /\b\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[- ]?[1-8]\d{6}\b/g, () => true],
  // API 키·토큰 모양
  ['secret', /\b(?:sk-(?:ant-)?[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b/g, () => true],
];
const MASK_LABEL = { card: '[카드번호]', rrn: '[주민번호]', secret: '[비밀키]' };
function mask(text) {
  let out = String(text ?? '');
  const hits = {};
  for (const [name, re, ok] of MASKS) {
    out = out.replace(re, (m) => {
      if (!ok(m)) return m;
      hits[name] = (hits[name] || 0) + 1;
      return MASK_LABEL[name];
    });
  }
  return { text: out, hits };
}
// 이벤트 안의 짧은 문자열(제목, 도구 입력 등)도 같은 규칙으로 — 객체는 깊이 따라간다
function maskDeep(v, hits, depth = 0) {
  if (typeof v === 'string') {
    const r = mask(v);
    for (const [k, n] of Object.entries(r.hits)) hits[k] = (hits[k] || 0) + n;
    return r.text;
  }
  if (depth > 6 || v === null || typeof v !== 'object') return v;
  if (Array.isArray(v)) return v.map((x) => maskDeep(x, hits, depth + 1));
  return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, maskDeep(x, hits, depth + 1)]));
}

const normalize = (t) => String(t ?? '').replace(/\r\n?/g, '\n').replace(/[ \t]+\n/g, '\n').trim();
const sha256 = (t) => crypto.createHash('sha256').update(t, 'utf8').digest('hex');
const httpLike = (u) => /^https?:\/\//i.test(String(u || ''));
const hostOf = (u) => { try { return new URL(u).hostname.toLowerCase(); } catch { return ''; } };
const dayOf = (ms) => new Date(ms).toISOString().slice(0, 10);

const DEFAULT_CONFIG = {
  enabled: true,
  capture: { web: true, agent: true, approval: true, chat: true },
  exclude_domains: [],
  exclude_channels: [],
  settle_ms: 1500,
  max_text_chars: 200000,
};
function loadConfig(file) {
  let raw = {};
  try { raw = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { if (e.code !== 'ENOENT') console.warn('[collect] 설정 파일을 읽지 못함:', e.message); }
  const cfg = { ...DEFAULT_CONFIG, ...raw, capture: { ...DEFAULT_CONFIG.capture, ...(raw.capture || {}) } };
  cfg.exclude_domains = (cfg.exclude_domains || []).map((d) => String(d).toLowerCase().replace(/^\*?\./, '')).filter(Boolean);
  cfg.exclude_channels = (cfg.exclude_channels || []).map((c) => String(c).replace(/^#/, ''));
  if (process.env.A2A_COLLECT === '0') cfg.enabled = false;
  return cfg;
}

function createCollector() {
  let root = null;                  // userData/events — init 전에는 모든 기록이 아무 일도 안 한다
  let cfg = { ...DEFAULT_CONFIG, enabled: false };
  let getProfile = () => ({ user: null, tenant: null });
  const session = crypto.randomBytes(3).toString('hex');
  let seq = 0;
  const metaCache = new Map();      // hash → meta
  const chatSeen = new Map();       // tenant:cid:seq → hash
  const channelNames = new Map();   // cid → name
  const lastSnap = new Map();       // webContents.id → { url, hash, text, at }
  const reads = new Map();          // task_id → { hashes:Set, excluded:n } — 에이전트가 근거로 본 원문
  const agentOutputs = [];          // 최근 에이전트 산출물 { hash, text } — 사람 글에 섞여 다시 들어오는지 본다
  const agent = { runTask: null, instruction: null, text: '', tool: null };
  let warned = 0;

  const safe = (fn) => (...a) => {
    if (!root || !cfg.enabled) return undefined;
    try {
      const r = fn(...a);
      return r && typeof r.then === 'function' ? r.catch(warn) : r;
    } catch (e) { warn(e); return undefined; }
  };
  function warn(e) { if (warned++ < 20) console.warn('[collect]', e?.stack || e); }

  // ── 저장 ──
  function newId(ms) { return `ev_${ms.toString(36).padStart(9, '0')}_${session}_${(++seq).toString(36)}`; }
  function blobPath(hash, ext) { return path.join(root, 'blobs', hash.slice(0, 2), `${hash}.${ext}`); }
  function readMeta(hash) {
    if (metaCache.has(hash)) return metaCache.get(hash);
    let m = null;
    try { m = JSON.parse(fs.readFileSync(blobPath(hash, 'json'), 'utf8')); } catch { /* 없음 */ }
    if (m) cacheMeta(hash, m);
    return m;
  }
  function cacheMeta(hash, m) {
    metaCache.set(hash, m);
    if (metaCache.size > 5000) metaCache.delete(metaCache.keys().next().value);
  }
  function writeAtomic(file, data) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, data);
    fs.renameSync(tmp, file);
  }
  // 원문 넣기 — 같은 해시가 있으면 쓰지 않고 처음 본 기록을 돌려준다
  function putBlob(text, { eventId, at, origin, docType }) {
    const hash = sha256(text);
    const meta = readMeta(hash);
    if (meta) return { hash, bytes: meta.bytes, isNew: false, meta };
    const m = { hash, bytes: Buffer.byteLength(text), first_event: eventId, first_seen: new Date(at).toISOString(),
                content_origin: origin, doc_type: docType };
    writeAtomic(blobPath(hash, 'txt'), text);
    writeAtomic(blobPath(hash, 'json'), JSON.stringify(m));
    cacheMeta(hash, m);
    return { hash, bytes: m.bytes, isNew: true, meta: m };
  }
  function append(ev) {
    const file = path.join(root, 'log', `${dayOf(Date.parse(ev.recorded_at))}.jsonl`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.appendFileSync(file, `${JSON.stringify(ev)}\n`);
    return ev;
  }

  // 이벤트 하나 — 봉투(필수 필드)를 채우고, content가 있으면 블롭으로 떼어 해시만 남긴다
  function record(kind, f) {
    const at = Date.now();
    const id = newId(at);
    const hits = {};
    const p = getProfile();
    let content = null;
    let observation = null, firstEvent = null;
    if (f.content != null) {
      let text = normalize(f.content);
      let truncated = false;
      if (text.length > cfg.max_text_chars) { text = text.slice(0, cfg.max_text_chars); truncated = true; }
      const m = mask(text);
      for (const [k, n] of Object.entries(m.hits)) hits[k] = (hits[k] || 0) + n;
      if (m.text) {
        const b = putBlob(m.text, { eventId: id, at, origin: f.content_origin, docType: f.doc_type });
        content = { hash: b.hash, bytes: b.bytes, truncated };
        if (b.isNew) observation = 'first';
        else if (b.meta.content_origin === 'agent' && f.content_origin !== 'agent') observation = 'echo';
        else observation = 'revisit';
        firstEvent = b.isNew ? null : b.meta.first_event;
        if (f.content_origin === 'agent') {
          agentOutputs.push({ hash: b.hash, text: m.text });
          if (agentOutputs.length > 50) agentOutputs.shift();
        }
      }
    }
    const taskId = f.task_id ?? null;
    const ev = {
      schema_version: SCHEMA_VERSION,
      event_id: id,
      session_id: session,
      kind,
      recorded_at: new Date(at).toISOString(),                         // b
      observed_at: f.observed_at || null,                              // 원문 쪽 시각(채팅 created_at 등)
      actor_kind: f.actor_kind,                                        // human | agent — 이 이벤트의 행동을 한 쪽
      actor: f.actor ?? null,
      content_origin: f.content_origin ?? null,                        // 원문을 쓴 쪽: external | human | agent
      user: p.user, tenant: p.tenant,
      platform: f.platform,                                            // ζ
      doc_type: f.doc_type ?? null,                                    // ζ
      author: f.author ?? null,                                        // ζ
      approval_status: f.approval_status ?? null,                      // ζ
      source_url: f.source_url ? maskDeep(f.source_url, hits) : null,
      title: f.title ? maskDeep(String(f.title).slice(0, 300), hits) : null,
      content_hash: content ? `sha256:${content.hash}` : null,         // v
      content_bytes: content?.bytes ?? null,
      span: f.span ?? (content ? { kind: 'whole', start: 0, end: null, truncated: content.truncated } : null),   // π
      observation,                                                     // first | revisit | echo
      first_event: firstEvent,
      evidence_candidate: !!(f.candidate && observation === 'first' && f.content_origin !== 'agent'),
      task_id: taskId,                                                 // c
      task_bound: taskId !== null,
      context: f.context || {},                                        // c — instruction, thread, tab, routine
      derived_from: (f.derived_from || []).map((h) => (h.startsWith('sha256:') ? h : `sha256:${h}`)),
      ...(f.derived_from_unknown ? { derived_from_unknown: true } : {}),
      label_kind: f.label_kind ?? null,
      data: f.data ? maskDeep(f.data, hits) : null,
      masked: Object.keys(hits).length ? hits : null,
    };
    return append(ev);
  }

  // ── 제외 ──
  const excludedUrl = (u) => {
    const h = hostOf(u);
    return !!h && cfg.exclude_domains.some((d) => h === d || h.endsWith(`.${d}`));
  };
  const EXCLUDED = '[제외 도메인]';
  // 도구 입력·요약 속 URL 중 제외 도메인은 지운다
  const scrubUrls = (v) => (typeof v === 'string'
    ? v.replace(/https?:\/\/[^\s"'<>)]+/g, (u) => (excludedUrl(u) ? EXCLUDED : u))
    : v && typeof v === 'object'
      ? (Array.isArray(v) ? v.map(scrubUrls) : Object.fromEntries(Object.entries(v).map(([k, x]) => [k, scrubUrls(x)])))
      : v);

  // ── 웹 탭 ──
  const TEXT_SCRIPT = `(() => {
    const t = (document.body ? document.body.innerText : '').replace(/\\n{3,}/g, '\\n\\n');
    return { url: location.href, title: document.title, text: t };
  })()`;
  async function readTab(wc) {
    if (!wc || wc.isDestroyed()) return null;
    const url = wc.getURL();
    if (!httpLike(url)) return null;
    if (excludedUrl(url)) return { excluded: true, url };
    const r = await wc.executeJavaScriptInIsolatedWorld(WORLD, [{ code: TEXT_SCRIPT }]);
    if (!r || excludedUrl(r.url)) return { excluded: true, url };
    return r;
  }
  async function snapshotTab(wc, { actorKind, tabId, reason, force = false }) {
    const r = await readTab(wc);
    if (!r || r.excluded) return r;
    const prev = lastSnap.get(wc.id);
    const text = normalize(r.text);
    if (!text) return null;
    // 같은 탭·같은 주소·같은 해시를 1분 안에 또 보면(SPA 해시 이동 등) 이벤트를 늘리지 않는다
    const hash = sha256(mask(text.slice(0, cfg.max_text_chars)).text);
    if (!force && prev && prev.url === r.url && prev.hash === hash && Date.now() - prev.at < 60000) return { ...prev, skipped: true };
    const ev = record('web.snapshot', {
      actor_kind: actorKind, actor: actorKind === 'human' ? getProfile().user : `agent:${getProfile().user}`,
      content_origin: 'external', platform: 'web', doc_type: 'web_page',
      source_url: r.url, title: r.title, content: text, candidate: true,
      task_id: actorKind === 'agent' ? agent.runTask : null,
      context: { tab_id: tabId ?? null, reason },
      span: { kind: 'page_text', start: 0, end: text.length, truncated: text.length > cfg.max_text_chars },
    });
    const h = ev.content_hash?.slice(7);
    lastSnap.set(wc.id, { url: r.url, hash: h, text: mask(text).text, at: Date.now(), event: ev.event_id });
    if (lastSnap.size > 40) lastSnap.delete(lastSnap.keys().next().value);
    return { url: r.url, hash: h, event: ev.event_id, observation: ev.observation, len: text.length };
  }

  const api = {
    SCHEMA_VERSION, mask, sha256,
    hashOf: (text) => sha256(mask(normalize(text)).text),   // 블롭 이름과 같은 해시(마스킹·정규화 뒤)
    get root() { return root; },
    get config() { return cfg; },
    get session() { return session; },

    init(userData, { profile, configFile } = {}) {
      try {
        cfg = loadConfig(configFile || process.env.A2A_COLLECT_CONFIG || CONFIG_FILE);
        root = path.join(userData, 'events');
        if (profile) getProfile = profile;
        fs.mkdirSync(path.join(root, 'log'), { recursive: true });
        try {
          for (const line of fs.readFileSync(path.join(root, 'chat-seen.jsonl'), 'utf8').split('\n')) {
            if (!line) continue;
            try { const { k, h } = JSON.parse(line); chatSeen.set(k, h); } catch { /* 끝이 잘린 줄 */ }
          }
        } catch { /* 처음 */ }
        if (cfg.enabled) record('collector.start', { actor_kind: 'system', platform: 'a2a-browser', data: { config: { capture: cfg.capture, exclude_domains: cfg.exclude_domains.length } } });
      } catch (e) { warn(e); root = null; }
    },
    reloadConfig(configFile) { cfg = loadConfig(configFile || process.env.A2A_COLLECT_CONFIG || CONFIG_FILE); return cfg; },
    excludedUrl,

    // 웹 탭: 다 읽히고 잠깐 뒤(동적 내용이 자리 잡게) 본문을 남긴다. isAgent()는 그 순간 에이전트가 이 탭에서 일하는지.
    watchTab: safe((wc, isAgent, tabId) => {
      if (!cfg.capture.web) return;
      let timer = null;
      const sched = (reason) => () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          if (wc.isDestroyed()) return;
          snapshotTab(wc, { actorKind: isAgent() ? 'agent' : 'human', tabId, reason }).catch(warn);
        }, cfg.settle_ms);
      };
      wc.on('did-finish-load', sched('load'));
      wc.on('did-navigate-in-page', sched('in-page'));
      wc.once('destroyed', () => { clearTimeout(timer); lastSnap.delete(wc.id); });
    }),
    snapshotTab: safe((wc, opts = {}) => snapshotTab(wc, { actorKind: 'human', reason: 'manual', ...opts })),

    // 사용자가 고른 글(오른쪽 클릭 → AI에게 물어보기) — π = 그 탭 마지막 본문 안의 위치
    selection: safe((wc, text) => {
      const url = wc.getURL();
      if (!cfg.capture.web || !httpLike(url) || excludedUrl(url)) return;
      const sel = normalize(text);
      const prev = lastSnap.get(wc.id);
      const within = prev && prev.url === url ? prev : null;
      const at = within ? within.text.indexOf(mask(sel).text) : -1;
      return record('web.selection', {
        actor_kind: 'human', actor: getProfile().user, content_origin: 'external', platform: 'web', doc_type: 'selection',
        source_url: url, title: wc.getTitle(), content: sel,
        span: { kind: 'selection', within: within ? `sha256:${within.hash}` : null, start: at >= 0 ? at : null, end: at >= 0 ? at + sel.length : null },
        context: { within_event: within?.event ?? null },
      });
    }),

    // ── 에이전트 ──
    // runAgent가 agent.run을 부르기 직전. dropped = 이미 다른 실행이 돌고 있어 이 지시는 실행되지 않는다(C4).
    // 실행 중 이벤트의 task_id는 currentTask가 아니라 여기서 정한 runTask를 따른다 — currentTask가 null이 돼도 잃지 않는다.
    agentStart: safe(({ taskId, text, labels, room, sources = [], dropped = false, harness = null, recall = [], worktype = null }) => {
      if (!cfg.capture.agent) return;
      const ev = record('agent.instruction', {
        actor_kind: 'human', actor: getProfile().user, content_origin: 'human', platform: 'a2a-browser', doc_type: 'instruction',
        author: getProfile().user, content: text, task_id: taskId ?? null,
        derived_from: sources.filter(Boolean),
        context: room ? { thread: { cid: room.cid, channel: room.name } } : {},
        data: { mentions: labels || [], dropped, ...(harness ? { harness } : {}), ...(recall.length ? { recall } : {}), ...(worktype ? { worktype } : {}) },   // harness: 이 실행을 한 하네스 버전, recall: 꺼내 붙인 지도 노드(docs/HARNESS.md)
      });
      if (dropped) return ev;
      flushText(false);
      agent.runTask = taskId ?? null;
      agent.instruction = ev.event_id;
      const r = readSet(taskId);
      for (const h of sources) if (h) r.hashes.add(h.replace(/^sha256:/, ''));
      if (ev.content_hash) r.hashes.add(ev.content_hash.slice(7));
      return ev;
    }),
    // emitAgent가 보내는 이벤트 그대로. fallbackTaskId = 그 순간 currentTask?.id(없으면 null)
    agentEvent: safe((ev, fallbackTaskId) => {
      if (!cfg.capture.agent || !ev) return;
      const taskId = agent.runTask ?? fallbackTaskId ?? null;
      switch (ev.type) {
        case 'text': agent.text += ev.delta || ''; break;
        case 'tool': flushText(false, taskId); agent.tool = { name: ev.name, input: ev.input }; break;
        case 'tool_done': {
          const t = agent.tool && agent.tool.name === ev.name ? agent.tool : { name: ev.name, input: null };
          agent.tool = null;
          let input = scrubUrls(t.input);
          // 비밀번호 칸이라 거부된 입력은 친 글을 남기지 않는다
          if (t.name === 'type' && /비밀번호/.test(ev.summary || '') && input) input = { ...input, text: '[비밀번호 칸 — 기록 안 함]' };
          record('agent.tool_call', {
            actor_kind: 'agent', actor: `agent:${getProfile().user}`, platform: 'a2a-browser', doc_type: 'tool_call',
            task_id: taskId, context: { instruction: agent.instruction },
            data: { tool: t.name, input, ok: !!ev.ok, summary: scrubUrls(ev.summary || '') },
          });
          break;
        }
        case 'page': {
          const spec = ev.spec || {};
          const cited = [];
          for (const b of spec.blocks || []) for (const x of b.items || []) if (x && typeof x.url === 'string') cited.push(x.url);
          record('agent.page', {
            actor_kind: 'agent', actor: `agent:${getProfile().user}`, content_origin: 'agent', platform: 'a2a-browser', doc_type: 'result_page_spec',
            title: spec.title, content: JSON.stringify(spec), task_id: taskId,
            derived_from: derived(taskId), context: { instruction: agent.instruction, tab_id: ev.tabId ?? null },
            data: { cited_urls: scrubUrls([...new Set(cited)]).slice(0, 50) },
          });
          break;
        }
        case 'note': case 'error':
          record('agent.note', {
            actor_kind: 'agent', actor: `agent:${getProfile().user}`, platform: 'a2a-browser', doc_type: 'note',
            task_id: taskId, context: { instruction: agent.instruction },
            data: { level: ev.type, text: scrubUrls(String(ev.text || ev.message || '').slice(0, 2000)) },
          });
          break;
        case 'done':
          flushText(true, taskId);
          record('agent.run_end', { actor_kind: 'agent', actor: `agent:${getProfile().user}`, platform: 'a2a-browser',
                                    task_id: taskId, context: { instruction: agent.instruction } });
          agent.runTask = null;
          agent.instruction = null;
          agent.tool = null;
          break;
        default: break;
      }
    }),
    // 에이전트가 페이지를 읽었다(read_page·screenshot) — 같은 본문을 우리 쪽에서도 읽어 해시로 남기고, 근거 목록에 더한다
    agentObserved: safe(async (wc, how) => {
      if (!cfg.capture.agent) return;
      const taskId = agent.runTask;
      const r = await snapshotTab(wc, { actorKind: 'agent', reason: how, force: false });
      const set = readSet(taskId);
      if (r?.excluded) { set.excluded++; return; }
      if (r?.hash) {
        set.hashes.add(r.hash);
        record('agent.read', {
          actor_kind: 'agent', actor: `agent:${getProfile().user}`, platform: 'web', doc_type: 'web_page',
          source_url: r.url, task_id: taskId, context: { instruction: agent.instruction, snapshot_event: r.event },
          derived_from: [r.hash],
          span: how === 'screenshot' ? { kind: 'viewport_image', within: `sha256:${r.hash}` }
                                     : { kind: 'page_text', within: `sha256:${r.hash}`, start: 0, end: Math.min(r.len ?? AGENT_VIEW_CHARS, AGENT_VIEW_CHARS) },
          data: { how },
        });
      }
    }),
    // 에이전트가 바깥 플랫폼 도구(MCP — EMA Station 등)로 받은 결과 — 웹 탭을 읽은 것과 같은 자리(agent.read).
    // 결과 원문은 그 플랫폼에 사람이 입력한 기록이라 content_origin: external, 처음 본 것이면 증거 후보.
    // 해시를 그 작업의 근거(readSet)에 넣어, 에이전트가 이걸 보고 쓴 답·게시에 derived_from으로 붙게 한다.
    agentToolResult: safe(({ platform, tool, input, text, sourceUrl = null }) => {
      if (!cfg.capture.agent || !text) return null;
      const taskId = agent.runTask;
      const ev = record('agent.read', {
        actor_kind: 'agent', actor: `agent:${getProfile().user}`, content_origin: 'external',
        platform: String(platform), doc_type: 'mcp_tool_result', source_url: sourceUrl,
        title: String(tool), content: text, candidate: true, task_id: taskId,
        span: { kind: 'tool_result', start: 0, end: text.length },
        context: { instruction: agent.instruction },
        data: { tool: String(tool), input },
      });
      if (ev.content_hash) readSet(taskId).hashes.add(ev.content_hash.slice(7));
      return ev;
    }),
    // @로 붙인 탭·이전 결과·메시지 — 해시를 돌려주면 runAgent가 agentStart의 sources로 넘긴다
    sourceTab: safe(async (wc, tabId) => {
      const r = await snapshotTab(wc, { actorKind: 'human', tabId, reason: 'mention' });
      return r?.hash || null;
    }),
    sourceTaskResult: safe((task) => {
      if (!task?.summary) return null;
      const ev = record('agent.output_ref', {
        actor_kind: 'human', actor: getProfile().user, content_origin: 'agent', platform: 'a2a-browser', doc_type: 'agent_answer',
        title: task.title, content: task.summary, task_id: task.id, derived_from: derived(task.id),
        data: { reason: 'mention' },
      });
      return ev.content_hash?.slice(7) || null;
    }),

    // 승인/거절 — 독립 라벨(D_t)이 아니라 작업 중 사용자가 준 승인 신호
    approval: safe(({ text, ok, simulated = false }) => {
      if (!cfg.capture.approval) return;
      return record('approval.decision', {
        actor_kind: 'human', actor: getProfile().user, platform: 'a2a-browser', doc_type: 'approval',
        approval_status: ok ? 'approved' : 'rejected', label_kind: 'approval_signal',
        task_id: agent.runTask, context: { instruction: agent.instruction },
        data: { decision: ok ? 'approved' : 'rejected', prompt: scrubUrls(String(text || '').slice(0, 4000)), simulated },
      });
    }),

    // ── 팀 채팅 ──
    chatChannels: (list) => {
      try { for (const c of Array.isArray(list) ? list : []) if (Number.isInteger(c?.id)) channelNames.set(c.id, c.name); } catch { /* 무시 */ }
      return list;
    },
    // direction: sent(이 브라우저가 보냄) | received(목록으로 받음). byAgent·taskId는 보낸 쪽만 안다.
    chatMessage: safe((cid, msg, { direction, byAgent = false, taskId = null } = {}) => {
      if (!cfg.capture.chat || !msg || !Number.isInteger(msg.seq)) return;
      const name = channelNames.get(cid) || null;
      if (name && cfg.exclude_channels.includes(name)) return;
      const p = getProfile();
      const key = `${p.tenant}:${cid}:${msg.seq}`;
      const body = String(msg.body ?? '');
      const deleted = !!msg.deleted;
      const h = deleted ? 'deleted' : sha256(mask(normalize(body)).text);
      const prev = chatSeen.get(key);
      if (prev === h) return;
      if (deleted && prev === undefined) return;                  // 처음 보는 지운 글은 남길 게 없다
      chatSeen.set(key, h);
      fs.appendFileSync(path.join(root, 'chat-seen.jsonl'), `${JSON.stringify({ k: key, h })}\n`);
      const isAgent = byAgent || body.startsWith('🤖');
      let derivedFrom = [];
      let unknown = false;
      const data = { direction, seq: msg.seq, kind: msg.kind || null, ...(prev !== undefined ? { revision: true } : {}) };
      if (isAgent && byAgent) derivedFrom = derived(taskId ?? agent.runTask);
      else if (isAgent) unknown = true;                            // 다른 사람의 에이전트 글 — 근거를 모른다
      else {
        // 사람 글에 에이전트 산출물이 섞여 있으면(결과를 붙여 보냄) 그 산출물을 가리킨다
        const clean = mask(normalize(body)).text;
        const hit = agentOutputs.filter((o) => o.text.length >= 30 && clean.includes(o.text)).map((o) => o.hash);
        if (hit.length) { derivedFrom = [...new Set(hit)]; data.contains_agent_output = true; }
      }
      if (deleted) {
        return record('chat.deleted', { actor_kind: 'human', actor: msg.author ?? null, platform: 'a2a-chat', doc_type: 'chat_message',
          source_url: `a2a-chat://${p.tenant}/${cid}/${msg.seq}`, context: { thread: { cid, channel: name } }, data });
      }
      return record('chat.message', {
        actor_kind: isAgent ? 'agent' : 'human', actor: msg.author ?? (direction === 'sent' ? p.user : null),
        content_origin: isAgent ? 'agent' : 'human', platform: 'a2a-chat', doc_type: 'chat_message', author: msg.author ?? p.user,
        observed_at: msg.created_at || null, source_url: `a2a-chat://${p.tenant}/${cid}/${msg.seq}`,
        content: body, candidate: !isAgent && !data.contains_agent_output,
        task_id: byAgent ? (taskId ?? agent.runTask) : null,
        derived_from: derivedFrom, derived_from_unknown: unknown,
        context: { thread: { cid, channel: name, parent_seq: msg.parent_seq ?? null } },
        span: { kind: 'message', start: 0, end: body.length },
        data,
      });
    }),
    // 어댑터 입구(collect-adapters.js createSink가 검사한 뒤에만 부른다)
    external: safe((r) => record(r.kind, {
      ...r,
      source_url: r.source_url && excludedUrl(r.source_url) ? null : r.source_url,
      content: r.source_url && excludedUrl(r.source_url) ? null : r.content,
    })),
    chatMessages: (cid, res) => {
      try { for (const m of res?.messages || []) api.chatMessage(cid, m, { direction: 'received' }); } catch (e) { warn(e); }
      return res;
    },
  };

  function readSet(taskId) {
    const k = taskId ?? '(unbound)';
    if (!reads.has(k)) {
      reads.set(k, { hashes: new Set(), excluded: 0 });
      if (reads.size > 60) reads.delete(reads.keys().next().value);
    }
    return reads.get(k);
  }
  const derived = (taskId) => [...readSet(taskId).hashes];
  // 도구 사이의 에이전트 글 한 덩어리 — 마지막 덩어리(final)가 최종 답
  function flushText(final, taskId = agent.runTask) {
    const t = agent.text;
    agent.text = '';
    if (!t.trim()) return;
    const set = readSet(taskId);
    record('agent.text', {
      actor_kind: 'agent', actor: `agent:${getProfile().user}`, content_origin: 'agent', platform: 'a2a-browser',
      doc_type: final ? 'agent_answer' : 'agent_narration', author: `agent:${getProfile().user}`,
      content: t, task_id: taskId, derived_from: [...set.hashes],
      context: { instruction: agent.instruction },
      data: { final, ...(set.excluded ? { excluded_sources: set.excluded } : {}) },
    });
  }
  return api;
}

module.exports = { createCollector, mask, normalize, sha256, loadConfig, SCHEMA_VERSION, collect: createCollector() };
