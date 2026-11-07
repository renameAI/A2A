// 수집 진입 라우터 — 브라우저 밖의 업무(사내 플랫폼 MCP, 루틴 에이전트)가 들어오는 단일 입구.
//
//   어댑터/외부 프로세스 → router.ingest(record) → 플랫폼 등록 확인 → validateRecord(순환·사칭 검사) → collector.external → JSONL/블롭
//
// 두 가지 입구:
//   1) 같은 프로세스: router.register({platform, kinds?}) 로 플랫폼을 등록하고 router.ingest(record).
//   2) 로컬 HTTP(기본 꺼짐): A2A_COLLECT_INGEST_PORT=<포트>(0이면 임의 포트)로 켜면 127.0.0.1에서만 POST /ingest.
//      Authorization: Bearer <토큰> 필수 — 토큰은 userData/events/ingest.token(0600)에 앱이 만든다.
//      웹페이지가 localhost로 쏘는 것을 막으려고 CORS를 열지 않고, Origin 헤더가 있으면 거절하고, 본문은 1MB까지만 받는다.
//      (회사 PC의 다른 프로세스가 "증거"를 심을 수 있는 입구이므로 토큰 없이는 열지 않는다.)
//
// 등록되지 않은 platform은 받지 않는다 — 어떤 플랫폼이 이 로그에 쓸 수 있는지가 곧 증거의 출처 목록이다.
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { validateRecord, createSink } = require('./collect-adapters');

const MAX_BODY = 1024 * 1024;

function createRouter(collector) {
  const sink = createSink(collector);
  const platforms = new Map();          // platform → { kinds:Set|null, accepted, rejected }
  let server = null;
  let token = null;

  const router = {
    // 플랫폼 등록. kinds를 주면 그 kind만 받는다(예: ['routine:dot.output', 'routine:dot.run_start']).
    register({ platform, kinds = null }) {
      if (typeof platform !== 'string' || !platform || /^(web|a2a-chat|a2a-browser)$/.test(platform)) {
        throw new Error('등록할 수 없는 platform (브라우저 안쪽 이름은 쓸 수 없음)');
      }
      platforms.set(platform, { kinds: kinds ? new Set(kinds) : null, accepted: 0, rejected: 0 });
      return router;
    },
    unregister(platform) { platforms.delete(platform); },
    platforms: () => [...platforms].map(([name, p]) => ({ platform: name, kinds: p.kinds ? [...p.kinds] : null, accepted: p.accepted, rejected: p.rejected })),

    ingest(r) {
      const p = r && platforms.get(r.platform);
      if (!p) return { ok: false, errors: [`등록되지 않은 platform: ${r && r.platform}`] };
      if (p.kinds && !p.kinds.has(r.kind)) { p.rejected++; return { ok: false, errors: [`이 platform이 쓸 수 없는 kind: ${r.kind}`] }; }
      const res = sink.ingest(r);
      if (res.ok) p.accepted++; else p.rejected++;
      return res;
    },
    ingestMany(list) {
      if (!Array.isArray(list) || list.length > 500) return [{ ok: false, errors: ['records는 500건 이하 배열'] }];
      return list.map((r) => router.ingest(r));
    },
    validate: validateRecord,

    // 로컬 HTTP 입구. 켜지 못해도 앱은 계속 돈다.
    async listen(userData, port) {
      if (server) return router.address();
      token = crypto.randomBytes(24).toString('hex');
      const file = path.join(userData, 'events', 'ingest.token');
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, token, { mode: 0o600 });
      server = http.createServer((req, res) => {
        const reply = (code, body) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
        if (req.method !== 'POST' || req.url !== '/ingest') return reply(404, { ok: false });
        if (req.headers.origin) return reply(403, { ok: false, errors: ['브라우저 요청은 받지 않음'] });
        const given = String(req.headers.authorization || '').replace(/^Bearer /, '');
        const a = Buffer.from(given), b = Buffer.from(token);
        if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return reply(401, { ok: false, errors: ['토큰이 맞지 않음'] });
        let size = 0;
        const chunks = [];
        req.on('data', (c) => {
          size += c.length;
          if (size > MAX_BODY) { reply(413, { ok: false, errors: ['본문이 너무 큼'] }); req.destroy(); } else chunks.push(c);
        });
        req.on('end', () => {
          if (size > MAX_BODY) return;
          let body;
          try { body = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch { return reply(400, { ok: false, errors: ['JSON이 아님'] }); }
          const results = Array.isArray(body.records) ? router.ingestMany(body.records) : [router.ingest(body)];
          reply(results.every((x) => x.ok) ? 200 : 422, { results: results.map((x) => (x.ok ? { ok: true, event_id: x.event.event_id, observation: x.event.observation } : x)) });
        });
      });
      await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '127.0.0.1', resolve); });
      return router.address();
    },
    address() { return server ? { port: server.address().port, tokenFile: undefined } : null; },
    token: () => token,
    close() { if (server) { server.close(); server = null; token = null; } },
  };
  return router;
}

module.exports = { createRouter };
