// 자체 점검 — 업무 이벤트 수집(events.js). selftest.js가 부른다.
//   A2A_SELFTEST_DIR=<폴더> npm start                          전체 점검 안에서
//   A2A_SELFTEST_DIR=<폴더> A2A_SELFTEST_ONLY=events npm start   수집 점검만
//   (A2A_SELFTEST_AGENT=1이면 실제 에이전트 실행 뒤 afterAgent로 진짜 궤적도 본다)
//
// 시험 페이지는 따로 띄운다: 카드번호·주민번호가 든 문서, 값이 채워진 비밀번호 칸, 같은 내용의 사본 주소.
// 제외 도메인은 같은 서버를 localhost로 열어 본다(127.0.0.1은 수집, localhost는 제외).
const http = require('http');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { createSink } = require('./collect-adapters');

// 같은 점검 폴더로 여러 번 돌려도 "처음 본 원문"이 되게 실행마다 다른 표식을 넣는다
const RUN = Date.now().toString(36);
const PW = 'S3cret-Collect-pw!';
const CARD = '4111 1111 1111 1111';
const RRN = '900101-1234567';
const DOC = `<!doctype html><meta charset="utf-8"><title>파트너 계약 메모</title>
<body style="font:16px sans-serif;margin:60px"><h1>파트너 계약 메모</h1>
<p>제휴 담당: 김하늘 매니저 · 051-123-4567</p>
<p>법인카드 ${CARD}로 선결제, 담당자 주민번호 ${RRN} (서류 확인용)</p>
<p>계약 기간은 2027년 1월부터 12월까지.</p><p>점검 ${RUN}</p></body>`;
const LOGIN = `<!doctype html><meta charset="utf-8"><title>수집 시험 로그인</title>
<body style="font:16px sans-serif;margin:60px"><form><input name="u" value="kim@partner.example">
<input id="p" type="password"><button type="button">로그인</button></form>
<script>document.getElementById('p').value = ${JSON.stringify(PW)};</script></body>`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(req.url.startsWith('/login') ? LOGIN : DOC);
    }).listen(0, '127.0.0.1', () => resolve(srv));
  });
}

function readAll(root) {
  const dir = path.join(root, 'log');
  const out = [];
  for (const f of fs.existsSync(dir) ? fs.readdirSync(dir).sort() : []) {
    for (const l of fs.readFileSync(path.join(dir, f), 'utf8').split('\n')) if (l) { try { out.push(JSON.parse(l)); } catch { /* 잘린 줄 */ } }
  }
  return out;
}
// 폴더 전체(로그·블롭·메타)에 어떤 문자열이 있는지
function grepTree(dir, needle) {
  const hits = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (fs.readFileSync(p).includes(Buffer.from(needle))) hits.push(path.relative(dir, p));
    }
  };
  if (fs.existsSync(dir)) walk(dir);
  return hits;
}
const blobText = (root, h) => fs.readFileSync(path.join(root, 'blobs', h.slice(7, 9), `${h.slice(7)}.txt`), 'utf8');
const blobCount = (root) => {
  let n = 0;
  const b = path.join(root, 'blobs');
  if (fs.existsSync(b)) for (const d of fs.readdirSync(b)) n += fs.readdirSync(path.join(b, d)).filter((f) => f.endsWith('.txt')).length;
  return n;
};
async function waitFor(fn, ms = 6000) {
  const end = Date.now() + ms;
  for (;;) {
    const v = fn();
    if (v || Date.now() > end) return v;
    await sleep(200);
  }
}

module.exports = async function eventsSelftest(ctx, step, dir) {
  const c = ctx.collect;
  const root = c.root;
  step('collect-on', !!root && c.config.enabled && readAll(root).some((e) => e.kind === 'collector.start'), { root });
  if (!root) return;
  const srv = await serve();
  const port = srv.address().port;
  const base = `http://127.0.0.1:${port}`;
  const since = () => { const n = readAll(root).length; return () => readAll(root).slice(n); };
  try {
    // 1) 웹 탭 본문 — 다 읽히면 해시로 남는다. 카드·주민번호는 가려진다.
    let fresh = since();
    const t1 = ctx.openTab(`${base}/doc`);
    const snap1 = await waitFor(() => fresh().find((e) => e.kind === 'web.snapshot' && e.source_url === `${base}/doc`));
    const text1 = snap1 ? blobText(root, snap1.content_hash) : '';
    step('events-web-snapshot', !!snap1 && snap1.observation === 'first' && snap1.evidence_candidate && snap1.actor_kind === 'human' &&
         snap1.platform === 'web' && snap1.title === '파트너 계약 메모' && text1.includes('김하늘') && snap1.schema_version === 1 &&
         Array.isArray(snap1.derived_from) && snap1.task_id === null && /^sha256:[0-9a-f]{64}$/.test(snap1.content_hash),
         snap1 && { hash: snap1.content_hash, observation: snap1.observation, span: snap1.span });
    step('events-masking', !!snap1 && !text1.includes('4111') && !text1.includes(RRN) && text1.includes('[카드번호]') &&
         text1.includes('[주민번호]') && text1.includes('051-123-4567') && snap1.masked?.card === 1 && snap1.masked?.rrn === 1,
         snap1 && { masked: snap1.masked, sample: text1.slice(0, 200) });

    // 2) 같은 내용을 다른 탭·다른 주소로 다시 보면 — 재방문으로만 남고 원문(증거)은 늘지 않는다
    const blobsBefore = blobCount(root);
    fresh = since();
    ctx.openTab(`${base}/doc?copy=1`);
    const snap2 = await waitFor(() => fresh().find((e) => e.kind === 'web.snapshot' && e.source_url === `${base}/doc?copy=1`));
    step('events-dedupe', !!snap2 && snap2.content_hash === snap1?.content_hash && snap2.observation === 'revisit' &&
         snap2.first_event === snap1.event_id && !snap2.evidence_candidate && blobCount(root) === blobsBefore,
         snap2 && { observation: snap2.observation, first_event: snap2.first_event, blobsBefore, blobsAfter: blobCount(root) });

    // 3) 비밀번호 칸 값은 어디에도 남지 않는다(페이지 본문·에이전트 도구 입력)
    fresh = since();
    ctx.openTab(`${base}/login`);
    const snap3 = await waitFor(() => fresh().find((e) => e.kind === 'web.snapshot' && e.source_url === `${base}/login`));
    c.agentStart({ taskId: 'tSELFTEST', text: '[시험] 계약 메모 정리', labels: [], sources: [] });
    ctx.emitAgent({ type: 'tool', name: 'type', input: { index: 2, text: PW } });
    ctx.emitAgent({ type: 'tool_done', name: 'type', ok: true, summary: '거부: 비밀번호 칸에는 입력하지 않습니다. 사용자에게 맡기세요.' });
    const pwHits = grepTree(root, PW);
    step('events-no-password', !!snap3 && pwHits.length === 0 &&
         fresh().some((e) => e.kind === 'agent.tool_call' && e.data?.input?.text === '[비밀번호 칸 — 기록 안 함]'),
         { snapshot: !!snap3, filesWithPassword: pwHits });

    // 4) 에이전트 산출물에는 근거 원문 해시(derived_from)가 붙는다. currentTask가 없어도(C4) task_id는 agentStart가 정한 것.
    fresh = since();
    const wc1 = t1.webContents;
    await c.agentObserved(wc1, 'read_page');
    ctx.emitAgent({ type: 'text', delta: `계약 메모를 정리했습니다(점검 ${RUN}). 제휴 담당은 김하늘 매니저이고 계약 기간은 2027년 1월부터 12월까지입니다.` });
    ctx.emitAgent({ type: 'page', spec: { title: '[시험] 계약 메모', blocks: [{ type: 'sources', items: [{ title: '메모', url: `${base}/doc` }] }] }, tabId: null });
    ctx.emitAgent({ type: 'done' });
    const evs4 = fresh();
    const read = evs4.find((e) => e.kind === 'agent.read');
    const answer = evs4.find((e) => e.kind === 'agent.text' && e.data?.final);
    const pageEv = evs4.find((e) => e.kind === 'agent.page');
    const instr = readAll(root).find((e) => e.kind === 'agent.instruction' && e.task_id === 'tSELFTEST');
    const ok4 = !!answer && !!pageEv && answer.task_id === 'tSELFTEST' && answer.actor_kind === 'agent' && answer.content_origin === 'agent' &&
      !answer.evidence_candidate && answer.derived_from.includes(snap1?.content_hash) && answer.derived_from.includes(instr?.content_hash) &&
      pageEv.derived_from.includes(snap1?.content_hash) && read?.derived_from?.[0] === snap1?.content_hash &&
      evs4.every((e) => e.kind === 'web.snapshot' || e.task_id === 'tSELFTEST');
    step('events-agent-derived-from', ok4, { answer: answer && { task_id: answer.task_id, derived_from: answer.derived_from },
                                             read: read && read.span, page: pageEv?.data });

    // 실행 밖 이벤트·겹친 지시(C4) — task_id가 없으면 null + task_bound:false, 겹친 지시는 dropped로 남는다
    fresh = since();
    ctx.emitAgent({ type: 'note', text: '[시험] 실행 밖 메모' });
    c.agentStart({ taskId: 'tDROPPED', text: '[시험] 겹친 지시', dropped: true });
    const evs5 = fresh();
    const loose = evs5.find((e) => e.kind === 'agent.note');
    const dropped = evs5.find((e) => e.kind === 'agent.instruction');
    step('events-task-null-safe', loose?.task_id === null && loose?.task_bound === false && dropped?.data?.dropped === true,
         { loose: loose && { task_id: loose.task_id, task_bound: loose.task_bound }, dropped: dropped?.data });

    // 5) 에이전트 글이 다시 들어오면(사람이 붙여 보냄 / 그대로 복사) 증거가 아니라 메아리
    fresh = since();
    const agentText = blobText(root, answer.content_hash);
    const fake = 900000 + Math.floor(Math.random() * 99999);          // 엔진에 없는 채널 번호(점검 전용)
    c.chatMessage(fake, { seq: 1, author: 'alice', body: agentText, created_at: new Date().toISOString() }, { direction: 'received' });
    c.chatMessage(fake, { seq: 2, author: 'boram', body: `확인 부탁드려요\n\n${agentText}`, created_at: new Date().toISOString() }, { direction: 'received' });
    c.chatMessage(fake, { seq: 3, author: 'alice', body: '🤖 다른 사람 에이전트의 글', created_at: new Date().toISOString() }, { direction: 'received' });
    c.chatMessage(fake, { seq: 1, author: 'alice', body: agentText }, { direction: 'received' });   // 같은 메시지 다시 → 안 쌓임
    const evs6 = fresh().filter((e) => e.kind === 'chat.message');
    const [echo, mixed, other] = evs6;
    step('events-echo-guard', evs6.length === 3 && echo.observation === 'echo' && !echo.evidence_candidate &&
         mixed.data?.contains_agent_output && mixed.derived_from.includes(answer.content_hash) && !mixed.evidence_candidate &&
         other.actor_kind === 'agent' && other.derived_from_unknown === true,
         evs6.map((e) => ({ seq: e.data?.seq, observation: e.observation, actor: e.actor_kind, candidate: e.evidence_candidate, derived: e.derived_from.length })));

    // 6) 승인/거절 — label_kind는 승인 신호(독립 라벨 아님)
    fresh = since();
    ctx.onConfirm(() => false);
    const okDecision = await ctx.confirm('[시험] #영업에 게시:\n수집 점검');
    ctx.onConfirm(null);
    const appr = fresh().find((e) => e.kind === 'approval.decision');
    step('events-approval', okDecision === false && appr?.label_kind === 'approval_signal' && appr.data?.decision === 'rejected' &&
         appr.approval_status === 'rejected' && appr.actor_kind === 'human' && appr.data?.simulated === true, appr && appr.data);

    // 7) 선택 구간 — π는 그 탭 본문 안의 위치
    fresh = since();
    c.selection(wc1, '계약 기간은 2027년 1월부터 12월까지.');
    const sel = fresh().find((e) => e.kind === 'web.selection');
    step('events-selection-span', sel?.span?.within === snap1?.content_hash && Number.isInteger(sel?.span?.start) && sel.span.start > 0, sel?.span);

    // 8) 제외 도메인 — 같은 서버를 localhost로 열면 아무것도 남지 않는다
    const cfgFile = path.join(dir, 'collect-test.config.json');
    fs.writeFileSync(cfgFile, JSON.stringify({ ...c.config, exclude_domains: [...c.config.exclude_domains, 'localhost'], settle_ms: 800 }));
    c.reloadConfig(cfgFile);
    fresh = since();
    const tx = ctx.openTab(`http://localhost:${port}/doc?private=1`);
    await sleep(3000);
    await c.agentObserved(tx.webContents, 'read_page');
    c.agentStart({ taskId: 'tEXCL', text: '[시험] 제외', sources: [] });
    ctx.emitAgent({ type: 'tool', name: 'navigate', input: { url: `http://localhost:${port}/doc?private=1` } });
    ctx.emitAgent({ type: 'tool_done', name: 'navigate', ok: true, summary: `이동함: http://localhost:${port}/doc?private=1` });
    ctx.emitAgent({ type: 'done' });
    const evs8 = fresh();
    const leaked = evs8.filter((e) => JSON.stringify(e).includes(`localhost:${port}`));
    step('events-exclude-domain', c.excludedUrl(`http://localhost:${port}/`) && !evs8.some((e) => e.kind === 'web.snapshot') && leaked.length === 0,
         { events: evs8.map((e) => e.kind), leaked: leaked.map((e) => e.kind) });
    c.reloadConfig();

    // 9) 팀 채팅 — 실제 엔진에서 받은 글·보낸 글(엔진이 없으면 건너뜀)
    try {
      const chans = await ctx.chatChannels();
      const ch = chans.find((x) => x.name === '영업') || chans[0];
      fresh = since();
      await ctx.chatMessages(ch.id);
      const body = `[시험] 수집 점검 ${new Date().toISOString()}`;
      const m = await ctx.chatPost(ch.id, body);
      await ctx.chatMessages(ch.id);                                  // 내가 보낸 글이 목록으로 다시 와도 한 번만
      const evs9 = fresh().filter((e) => e.kind === 'chat.message');
      const sent = evs9.filter((e) => e.data?.seq === m.seq);
      step('events-chat', sent.length === 1 && sent[0].data.direction === 'sent' && sent[0].actor_kind === 'human' &&
           sent[0].context?.thread?.channel === ch.name && blobText(root, sent[0].content_hash) === body,
           { channel: ch.name, recorded: evs9.length, sent: sent.map((e) => e.data) });
    } catch (e) {
      step('events-chat', null, `엔진 없음 — 건너뜀: ${e.message}`);
    }

    // 10) 어댑터 입구 — 에이전트 산출물은 derived_from 없이 못 들어온다
    const sink = createSink(c);
    const bad = sink.ingest({ kind: 'routine:dot.output', actor_kind: 'agent', platform: 'routine:dot', content: '주간 보고', content_origin: 'agent' });
    const good = sink.ingest({ kind: 'routine:dot.output', actor_kind: 'agent', platform: 'routine:dot', content: '주간 보고', content_origin: 'agent',
                               derived_from: [snap1.content_hash], task_id: 'run-1', context: { routine: 'weekly' } });
    step('events-adapter-contract', !bad.ok && good.ok && good.event.derived_from[0] === snap1.content_hash && good.event.schema_version === 1,
         { bad: bad.errors, good: good.ok });

    // 10b) 진입 라우터 — 등록된 플랫폼만, 토큰 있는 로컬 HTTP만
    const router = ctx.ingestRouter;
    router.register({ platform: 'routine:dot', kinds: ['routine:dot.output'] });
    const unreg = router.ingest({ kind: 'cube.doc', actor_kind: 'human', platform: 'cube', content: 'x', content_origin: 'external' });
    const wrongKind = router.ingest({ kind: 'routine:dot.secret', actor_kind: 'agent', platform: 'routine:dot' });
    const viaRouter = router.ingest({ kind: 'routine:dot.output', actor_kind: 'agent', platform: 'routine:dot', content: `주간 보고 ${RUN}`,
                                      content_origin: 'agent', derived_from: [snap1.content_hash] });
    const addr = await router.listen(path.dirname(root), 0);
    const post = (hdrs, body) => fetch(`http://127.0.0.1:${addr.port}/ingest`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...hdrs }, body: JSON.stringify(body) });
    const rec = { kind: 'routine:dot.output', actor_kind: 'agent', platform: 'routine:dot', content: `HTTP 보고 ${RUN}`, content_origin: 'agent', derived_from: [snap1.content_hash] };
    const noTok = await post({}, rec);
    const badTok = await post({ Authorization: 'Bearer nope' }, rec);
    const webOrigin = await post({ Authorization: `Bearer ${router.token()}`, Origin: 'http://evil.example' }, rec);
    const ok200 = await post({ Authorization: `Bearer ${router.token()}` }, rec);
    const noDerived = await post({ Authorization: `Bearer ${router.token()}` }, { ...rec, derived_from: [] });
    router.close();
    step('events-ingest-router', !unreg.ok && !wrongKind.ok && viaRouter.ok && noTok.status === 401 && badTok.status === 401 &&
         webOrigin.status === 403 && ok200.status === 200 && noDerived.status === 422 &&
         fs.statSync(path.join(root, 'ingest.token')).mode % 0o1000 === 0o600,
         { unreg: unreg.errors, wrongKind: wrongKind.errors, statuses: [noTok.status, badTok.status, webOrigin.status, ok200.status, noDerived.status] });

    // 11) 확인 도구 — CLI verify(해시·참조 무결성)
    let verify;
    try {
      const out = execFileSync(process.execPath, [path.join(__dirname, 'events-cli.js'), 'verify', '--dir', path.dirname(root)],
                               { env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' }, encoding: 'utf8' });
      verify = JSON.parse(out);
    } catch (e) { verify = { ok: false, error: String(e.stdout || e.message).slice(0, 600) }; }
    step('events-cli-verify', verify.ok === true && verify.events > 10, verify);
  } finally {
    srv.close();
  }
};

// 실제 에이전트가 돈 뒤(A2A_SELFTEST_AGENT=1): 진짜 궤적에 근거가 붙었는지
module.exports.afterAgent = async function afterAgent(ctx, step) {
  const root = ctx.collect.root;
  if (!root) return;
  await sleep(1500);
  const evs = readAll(root);
  const realTasks = new Set(evs.filter((e) => e.kind === 'agent.instruction' && /^t\d+$/.test(e.task_id || '') && !e.data?.dropped).map((e) => e.task_id));
  const answers = evs.filter((e) => e.kind === 'agent.text' && e.data?.final && realTasks.has(e.task_id));
  const hotelHashes = new Set(evs.filter((e) => e.kind === 'web.snapshot' && /오션뷰|OCEANVIEW/.test(e.title || '')).map((e) => e.content_hash));
  const grounded = answers.filter((a) => a.derived_from.some((h) => hotelHashes.has(h)));
  const posts = evs.filter((e) => e.kind === 'chat.message' && e.actor_kind === 'agent' && e.data?.direction === 'sent');
  const unbound = evs.filter((e) => e.kind.startsWith('agent.') && e.kind !== 'agent.note' && !e.task_bound);
  step('events-real-agent', answers.length >= 1 && grounded.length >= 1 && posts.every((p) => p.derived_from.length > 0) &&
       evs.some((e) => e.kind === 'agent.tool_call' && realTasks.has(e.task_id)) && unbound.length === 0,
       { tasks: realTasks.size, answers: answers.length, grounded: grounded.length, agentPosts: posts.length,
         approvals: evs.filter((e) => e.kind === 'approval.decision').length, unbound: unbound.map((e) => e.kind) });
};
