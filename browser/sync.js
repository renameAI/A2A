// 업무 기록 올리기(L1) — 로컬 수집 로그(events.js)를 A2A 엔진의 워크스페이스로. 기본은 꺼짐이고, 사용자가 범위를 고른다.
//   signals  행동 신호만(종류·도구 이름·승인/거절·하네스 버전·작업 id·시각) — 원문·주소·입력 없음
//   hashes   + 원문 해시·문서 종류·주소의 호스트
//   full     + 마스킹한 원문(블롭) — 서버에서 재실행 비교를 하려면 필요
// 걸러 내기는 엔진도 다시 한다(app/chat/collect.py). 지금 프로필(계정×워크스페이스)의 이벤트만, 줄 단위 커서로 이어서 올린다.
const fs = require('fs');
const path = require('path');

const MODES = ['off', 'signals', 'hashes', 'full'];
const BATCH = 300;
const BLOBS_PER_CALL = 80;
const EVERY = 60 * 1000;

function createSync({ root, engine, profile, mode, onChange = () => {} }) {
  const stateFile = () => root() && path.join(root(), 'sync-state.json');
  let state = null;
  let status = { last: null, sent: 0, blobs: 0, error: '', running: false, server: null };
  const load = () => { try { state = JSON.parse(fs.readFileSync(stateFile(), 'utf8')); } catch { state = {}; } return state; };
  const save = () => { const f = stateFile(); fs.writeFileSync(`${f}.tmp`, JSON.stringify(state)); fs.renameSync(`${f}.tmp`, f); };
  const key = () => `${profile().user}@${profile().tenant}`;

  async function post(p, body) {
    const r = await fetch(`${engine()}/chat/v1/t/${encodeURIComponent(profile().tenant)}${p}`, {
      method: body ? 'POST' : 'GET', headers: { 'X-Dev-User': profile().user, 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error?.message || `엔진 ${r.status}`);
    return j;
  }
  const blobText = (h) => { try { return fs.readFileSync(path.join(root(), 'blobs', h.slice(0, 2), `${h}.txt`), 'utf8'); } catch { return null; } };

  async function run() {
    const m = mode();
    if (m === 'off' || !root() || status.running) return status;
    status = { ...status, running: true, error: '' };
    onChange();
    try {
      if (!state) load();
      const cur = state[key()] || (state[key()] = { offsets: {}, blobs: [] });
      const sentBlobs = new Set(cur.blobs || []);
      const dir = path.join(root(), 'log');
      const { user, tenant } = profile();
      for (const f of fs.existsSync(dir) ? fs.readdirSync(dir).filter((x) => x.endsWith('.jsonl')).sort() : []) {
        const file = path.join(dir, f);
        const size = fs.statSync(file).size;
        let from = cur.offsets[f] || 0;
        while (from < size) {
          const fd = fs.openSync(file, 'r');
          const len = Math.min(size - from, 2 * 1024 * 1024);
          const buf = Buffer.alloc(len);
          fs.readSync(fd, buf, 0, len, from);
          fs.closeSync(fd);
          const text = buf.toString('utf8');
          const end = text.lastIndexOf('\n');
          if (end < 0) break;
          const lines = text.slice(0, end).split('\n');
          let consumed = 0;
          for (let i = 0; i < lines.length; i += BATCH) {
            const chunk = lines.slice(i, i + BATCH);
            const evs = [];
            for (const l of chunk) { try { const e = JSON.parse(l); if (e.user === user && e.tenant === tenant) evs.push(e); } catch { /* 잘린 줄 */ } }
            const blobs = {};
            if (m === 'full') {
              for (const e of evs) {
                const h = String(e.content_hash || '').replace(/^sha256:/, '');
                if (!h || sentBlobs.has(h) || Object.keys(blobs).length >= BLOBS_PER_CALL) continue;
                const t = blobText(h);
                if (t != null) blobs[h] = t;
              }
            }
            if (evs.length || Object.keys(blobs).length) {
              const r = await post('/collect/events', { mode: m, events: evs, blobs });
              status.sent += r.accepted || 0;
              status.blobs += r.blobs || 0;
              for (const h of Object.keys(blobs)) sentBlobs.add(h);
            }
            consumed += Buffer.byteLength(chunk.join('\n')) + chunk.length;   // 줄바꿈까지
            cur.offsets[f] = from + consumed;
            cur.blobs = [...sentBlobs].slice(-20000);
            save();
          }
          from = cur.offsets[f];
        }
      }
      status.server = await post('/collect/summary').catch(() => null);
      status.last = new Date().toISOString();
    } catch (e) {
      status.error = String(e.message || e);
    } finally {
      status.running = false;
      onChange();
    }
    return status;
  }

  let timer = null;
  function start() { clearInterval(timer); timer = setInterval(() => { run(); }, EVERY); }
  function stop() { clearInterval(timer); timer = null; }

  return { run, start, stop, MODES, snapshot: () => ({ mode: mode(), ...status }) };
}

module.exports = { createSync, MODES };
