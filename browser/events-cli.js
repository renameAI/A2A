#!/usr/bin/env node
// 쌓인 업무 이벤트 보기·내보내기 — 앱과 따로 node로 돈다(읽기만 한다).
//
//   node events-cli.js [명령] [--dir <userData 또는 events 폴더>] [--since 2026-10-28] [--kind web.] [--task t123] [-n 30]
//
//   tail     (기본) 최근 이벤트를 한 줄씩
//   stats    종류·관찰(first/revisit/echo)·증거 후보·마스킹 건수
//   export   이벤트 JSONL을 그대로 stdout으로(--with-content면 원문을 content 필드로 붙인다)
//   show <sha256:…|해시 앞부분>   원문 블롭과 메타
//   task <task_id>               한 작업의 궤적(지시 → 읽은 원문 → 도구 → 승인 → 산출물과 derived_from)
//   verify   블롭 해시가 맞는지, derived_from·first_event가 가리키는 것이 있는지
//
// --dir를 안 주면 실제 앱 프로필(macOS ~/Library/Application Support/a2a-browser). 자체 점검 결과는 --dir <A2A_SELFTEST_DIR>/profile.
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

function defaultUserData() {
  if (process.env.A2A_USER_DATA) return process.env.A2A_USER_DATA;
  if (process.platform === 'darwin') return path.join(os.homedir(), 'Library', 'Application Support', 'a2a-browser');
  if (process.platform === 'win32') return path.join(process.env.APPDATA || os.homedir(), 'a2a-browser');
  return path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'a2a-browser');
}

function parseArgs(argv) {
  const o = { cmd: 'tail', args: [], n: 30 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--dir') o.dir = argv[++i];
    else if (a === '--since') o.since = argv[++i];
    else if (a === '--kind') o.kind = argv[++i];
    else if (a === '--task') o.task = argv[++i];
    else if (a === '-n') o.n = Number(argv[++i]) || 30;
    else if (a === '--with-content') o.withContent = true;
    else if (a === '-h' || a === '--help') o.cmd = 'help';
    else if (!o._cmdSet && !a.startsWith('-')) { o.cmd = a; o._cmdSet = true; }
    else o.args.push(a);
  }
  return o;
}

function eventsRoot(dir) {
  const d = dir || defaultUserData();
  return fs.existsSync(path.join(d, 'log')) ? d : path.join(d, 'events');
}

function* readEvents(root, { since, kind, task } = {}) {
  const logDir = path.join(root, 'log');
  let files = [];
  try { files = fs.readdirSync(logDir).filter((f) => f.endsWith('.jsonl')).sort(); } catch { return; }
  for (const f of files) {
    if (since && f.slice(0, 10) < since.slice(0, 10)) continue;
    for (const line of fs.readFileSync(path.join(logDir, f), 'utf8').split('\n')) {
      if (!line) continue;
      let ev;
      try { ev = JSON.parse(line); } catch { continue; }            // 끝이 잘린 줄(강제 종료)은 건너뛴다
      if (since && ev.recorded_at < since) continue;
      if (kind && !ev.kind.startsWith(kind)) continue;
      if (task && ev.task_id !== task) continue;
      yield ev;
    }
  }
}

const blobFile = (root, h, ext) => { const x = h.replace(/^sha256:/, ''); return path.join(root, 'blobs', x.slice(0, 2), `${x}.${ext}`); };
const short = (h) => (h ? h.replace(/^sha256:/, '').slice(0, 10) : '-');
const clip = (s, n) => { s = String(s ?? '').replace(/\s+/g, ' '); return s.length > n ? `${s.slice(0, n - 1)}…` : s; };

function line(ev) {
  const t = ev.recorded_at.slice(5, 19).replace('T', ' ');
  const who = `${ev.actor_kind}${ev.actor ? `:${ev.actor}` : ''}`;
  const obs = ev.observation ? `${ev.observation}${ev.evidence_candidate ? '*' : ''}` : '';
  const what = ev.kind === 'agent.tool_call' ? `${ev.data?.tool} ${clip(JSON.stringify(ev.data?.input ?? {}), 60)} → ${ev.data?.ok ? 'ok' : 'fail'}`
    : ev.kind === 'approval.decision' ? `${ev.data?.decision}: ${clip(ev.data?.prompt, 60)}`
    : ev.kind === 'agent.note' ? clip(ev.data?.text, 80)
    : clip(ev.title || ev.source_url || '', 70);
  const df = ev.derived_from?.length ? ` ←${ev.derived_from.length}` : ev.derived_from_unknown ? ' ←?' : '';
  return `${t}  ${ev.kind.padEnd(17)} ${who.padEnd(20).slice(0, 20)} ${short(ev.content_hash).padEnd(10)} ${obs.padEnd(8)} ${ev.task_id || ''}${df}  ${what}`;
}

function main() {
  const o = parseArgs(process.argv.slice(2));
  const root = eventsRoot(o.dir);
  const filt = { since: o.since, kind: o.kind, task: o.task };
  switch (o.cmd) {
    case 'help':
      console.log(fs.readFileSync(__filename, 'utf8').split('\n').slice(1, 14).map((l) => l.replace(/^\/\/ ?/, '')).join('\n'));
      return;
    case 'tail': {
      const all = [...readEvents(root, filt)];
      console.log(`# ${root} — 이벤트 ${all.length}건 중 마지막 ${Math.min(o.n, all.length)}건 (* = 증거 후보, ←n = derived_from 수)`);
      for (const ev of all.slice(-o.n)) console.log(line(ev));
      return;
    }
    case 'stats': {
      const s = { total: 0, kinds: {}, observation: {}, evidence_candidates: 0, masked: {}, task_unbound_agent: 0, agent_outputs_without_derived_from: 0 };
      for (const ev of readEvents(root, filt)) {
        s.total++;
        s.kinds[ev.kind] = (s.kinds[ev.kind] || 0) + 1;
        if (ev.observation) s.observation[ev.observation] = (s.observation[ev.observation] || 0) + 1;
        if (ev.evidence_candidate) s.evidence_candidates++;
        for (const [k, n] of Object.entries(ev.masked || {})) s.masked[k] = (s.masked[k] || 0) + n;
        if (ev.actor_kind === 'agent' && ev.kind.startsWith('agent.') && !ev.task_bound) s.task_unbound_agent++;
        if (ev.content_origin === 'agent' && !ev.derived_from?.length && !ev.derived_from_unknown) s.agent_outputs_without_derived_from++;
      }
      let blobs = 0, bytes = 0;
      try {
        for (const d of fs.readdirSync(path.join(root, 'blobs'))) {
          for (const f of fs.readdirSync(path.join(root, 'blobs', d))) if (f.endsWith('.txt')) { blobs++; bytes += fs.statSync(path.join(root, 'blobs', d, f)).size; }
        }
      } catch { /* 아직 없음 */ }
      console.log(JSON.stringify({ root, ...s, blobs, blob_bytes: bytes }, null, 2));
      return;
    }
    case 'export':
      for (const ev of readEvents(root, filt)) {
        if (o.withContent && ev.content_hash) {
          try { ev.content = fs.readFileSync(blobFile(root, ev.content_hash, 'txt'), 'utf8'); } catch { ev.content = null; }
        }
        process.stdout.write(`${JSON.stringify(ev)}\n`);
      }
      return;
    case 'show': {
      const want = (o.args[0] || '').replace(/^sha256:/, '');
      if (want.length < 6) { console.error('해시(앞 6자 이상)를 주세요'); process.exitCode = 2; return; }
      const dir = path.join(root, 'blobs', want.slice(0, 2));
      const f = (fs.existsSync(dir) ? fs.readdirSync(dir) : []).find((x) => x.startsWith(want) && x.endsWith('.txt'));
      if (!f) { console.error('그런 원문이 없습니다'); process.exitCode = 1; return; }
      console.log(fs.readFileSync(path.join(dir, f.replace(/\.txt$/, '.json')), 'utf8'));
      console.log('──');
      console.log(fs.readFileSync(path.join(dir, f), 'utf8'));
      return;
    }
    case 'task': {
      const id = o.args[0] || o.task;
      if (!id) { console.error('task_id를 주세요'); process.exitCode = 2; return; }
      for (const ev of readEvents(root, { ...filt, task: id })) {
        console.log(line(ev));
        if (ev.derived_from?.length) console.log(`${' '.repeat(16)}derived_from: ${ev.derived_from.map(short).join(' ')}`);
      }
      return;
    }
    case 'verify': {
      const hashes = new Set();
      const ids = new Set();
      const problems = [];
      const evs = [...readEvents(root, filt)];
      for (const ev of evs) {
        ids.add(ev.event_id);
        if (!ev.schema_version || !ev.actor_kind || !ev.platform || !Array.isArray(ev.derived_from) || !('task_id' in ev) || !('content_hash' in ev) || !('source_url' in ev)) {
          problems.push(`필수 필드 없음: ${ev.event_id}`);
        }
        if (!ev.content_hash) continue;
        hashes.add(ev.content_hash);
      }
      for (const h of hashes) {
        try {
          const text = fs.readFileSync(blobFile(root, h, 'txt'), 'utf8');
          if (`sha256:${crypto.createHash('sha256').update(text, 'utf8').digest('hex')}` !== h) problems.push(`해시 불일치: ${h}`);
        } catch { problems.push(`원문 없음: ${h}`); }
      }
      for (const ev of evs) {
        for (const d of ev.derived_from || []) if (!fs.existsSync(blobFile(root, d, 'txt'))) problems.push(`derived_from이 가리키는 원문 없음: ${ev.event_id} → ${short(d)}`);
        if (ev.first_event && !ids.has(ev.first_event) && !o.since) problems.push(`first_event 없음: ${ev.event_id} → ${ev.first_event}`);
      }
      console.log(JSON.stringify({ root, events: evs.length, blobs_checked: hashes.size, ok: problems.length === 0, problems: problems.slice(0, 50) }, null, 2));
      if (problems.length) process.exitCode = 1;
      return;
    }
    default:
      console.error(`모르는 명령: ${o.cmd} (help 참고)`);
      process.exitCode = 2;
  }
}

if (require.main === module) main();
module.exports = { readEvents, eventsRoot, defaultUserData };
