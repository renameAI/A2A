// EMA Station 도구(MCP) — 에이전트가 EMA 등록(FRN)·오피스아워 데이터를 화면을 긁지 않고 직접 조회한다.
//
// 로그인은 표준 MCP OAuth(동적 등록 + PKCE): EMA 로그인 화면을 A2A 탭에 열고, 사용자가 직접 로그인하면
// EMA가 127.0.0.1 콜백으로 일회용 코드를 돌려준다. 앱은 그 코드로 토큰을 받는다 — 비밀번호는 앱·에이전트를
// 거치지 않는다. 토큰은 safeStorage(맥: 키체인이 지키는 키)로 암호화해 둔다. 암호화를 못 쓰면 저장하지 않는다.
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { app, safeStorage } = require('electron');
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StreamableHTTPClientTransport } = require('@modelcontextprotocol/sdk/client/streamableHttp.js');
const { UnauthorizedError } = require('@modelcontextprotocol/sdk/client/auth.js');

const DEFAULT_URL = 'https://ema-mcp.myscguard.app/mcp';
const PORT = 47823;                       // 콜백 포트 — 등록한 반환 주소와 같아야 해서 고정(바쁘면 빈 포트로 다시 등록)
const LOGIN_TIMEOUT = 5 * 60 * 1000;
const UA = 'A2A-Browser/0.1 (desktop; +ema-tools)';
const MAX_RESULT = 20000;

// 읽기만 하는 도구 — 이름이 이렇게 끝나거나 서버가 readOnlyHint를 준 것. 나머지(만들기·지우기·등록·저장·링크 발급)는 승인을 받는다.
const READ_ONLY = /_(get|list|search|status|summary|guide|template|compose|preview|export)$/;

function createEma({ url, openLogin, closeLogin, onChange }) {
  url ||= process.env.A2A_EMA_MCP_URL || DEFAULT_URL;
  const FILE = () => path.join(app.getPath('userData'), 'ema-oauth.bin');
  let saved = load();                     // { client, tokens, verifier, redirect }
  let client = null;
  let tools = [];                         // MCP tools/list 결과
  let instructions = '';
  let status = saved.tokens ? 'idle' : 'off';   // off | idle | connecting | login | on | error
  let error = '';
  let waiting = null;                     // 로그인 콜백 대기 { state, resolve, reject }
  let server = null;
  let redirect = saved.redirect || `http://127.0.0.1:${PORT}/callback`;

  function load() {
    try {
      if (!safeStorage.isEncryptionAvailable()) return {};
      return JSON.parse(safeStorage.decryptString(fs.readFileSync(FILE())));
    } catch { return {}; }
  }
  function save() {
    if (!safeStorage.isEncryptionAvailable()) return;   // 평문으로는 남기지 않는다
    const tmp = `${FILE()}.tmp`;
    fs.writeFileSync(tmp, safeStorage.encryptString(JSON.stringify(saved)), { mode: 0o600 });
    fs.renameSync(tmp, FILE());
  }
  const set = (s, e = '') => { status = s; error = e; onChange?.(); };

  // SDK가 부르는 OAuth 저장소
  let interactive = false;                // 사용자가 "연결"을 눌렀을 때만 로그인 화면을 연다(시작할 때 조용히 다시 붙기)
  let loginState = '';
  const provider = {
    get redirectUrl() { return redirect; },
    get clientMetadata() {
      return { client_name: 'A2A Browser', redirect_uris: [redirect], grant_types: ['authorization_code', 'refresh_token'],
               response_types: ['code'], token_endpoint_auth_method: 'none' };
    },
    state: () => { loginState = crypto.randomBytes(16).toString('hex'); return loginState; },
    clientInformation: () => (saved.client?.redirect_uris?.includes(redirect) ? saved.client : undefined),
    saveClientInformation: (c) => { saved.client = c; saved.redirect = redirect; save(); },
    tokens: () => saved.tokens,
    saveTokens: (t) => { saved.tokens = t; save(); },
    saveCodeVerifier: (v) => { saved.verifier = v; save(); },
    codeVerifier: () => saved.verifier,
    redirectToAuthorization: (authUrl) => {
      if (!interactive) throw new Error('다시 로그인이 필요해요');
      set('login');
      openLogin(authUrl.toString());
    },
    invalidateCredentials: (scope) => {
      if (scope === 'all' || scope === 'client') delete saved.client;
      if (scope === 'all' || scope === 'tokens') delete saved.tokens;
      if (scope === 'all' || scope === 'verifier') delete saved.verifier;
      save();
    },
  };

  // 콜백 받는 곳 — 127.0.0.1에서만, /callback만, 우리가 보낸 state만
  async function listen() {
    if (server) return;
    const handler = (req, res) => {
      const u = new URL(req.url, 'http://127.0.0.1');
      if (u.pathname !== '/callback') { res.writeHead(404); res.end(); return; }
      const ok = waiting && u.searchParams.get('state') === waiting.state && u.searchParams.get('code');
      res.writeHead(ok ? 200 : 400, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(`<!doctype html><meta charset="utf-8"><title>EMA 연결</title><body style="font:15px -apple-system,sans-serif;display:grid;place-items:center;height:90vh;color:#334">
        <div style="text-align:center"><h2>${ok ? 'EMA Station에 연결했어요' : '연결 요청이 맞지 않아요'}</h2><p>${ok ? '이 탭은 곧 닫힙니다.' : 'A2A에서 다시 "연결"을 눌러 주세요.'}</p></div>`);
      if (ok) { const w = waiting; waiting = null; w.resolve(u.searchParams.get('code')); }
      else if (waiting && u.searchParams.get('error')) { const w = waiting; waiting = null; w.reject(new Error(u.searchParams.get('error_description') || u.searchParams.get('error'))); }
    };
    const tryListen = (port) => new Promise((resolve, reject) => {
      const s = http.createServer(handler);
      s.once('error', reject);
      s.listen(port, '127.0.0.1', () => resolve(s));
    });
    try { server = await tryListen(PORT); }
    catch { server = await tryListen(0); }               // 포트가 바쁘면 빈 포트 — 반환 주소가 바뀌니 클라이언트를 다시 등록한다
    redirect = `http://127.0.0.1:${server.address().port}/callback`;
  }
  function stopListen() { try { server?.close(); } catch { /* 그만 */ } server = null; }

  function transport() {
    return new StreamableHTTPClientTransport(new URL(url), {
      authProvider: provider,
      requestInit: { headers: { 'User-Agent': UA } },
    });
  }
  async function open() {
    const c = new Client({ name: 'a2a-browser', version: '0.1.0' });
    await c.connect(transport());
    const list = await c.listTools();
    client = c;
    tools = list.tools || [];
    instructions = String(c.getInstructions?.() || '').slice(0, 4000);
    set('on');
  }

  // 연결 — interactive면 필요할 때 로그인 화면을 연다
  async function connect({ interactive: ask = false } = {}) {
    if (status === 'connecting' || status === 'login') return status;
    if (!ask && !saved.tokens) { set('off'); return 'off'; }   // 저장된 로그인이 없으면 조용히 붙을 것도 없다(쓸데없이 등록하지 않게)
    interactive = ask;
    set('connecting');
    try {
      await listen();
      try {
        await open();
      } catch (e) {
        if (!(e instanceof UnauthorizedError) || !ask || status !== 'login') throw e;
        // 로그인 탭에서 사용자가 로그인하면 콜백으로 코드가 온다
        const code = await new Promise((resolve, reject) => {
          waiting = { state: loginState, resolve, reject };
          setTimeout(() => { if (waiting) { waiting = null; reject(new Error('로그인 시간이 지났어요(5분)')); } }, LOGIN_TIMEOUT);
        });
        closeLogin?.();
        const t = transport();
        await t.finishAuth(code);
        await open();
      }
      return 'on';
    } catch (e) {
      client = null; tools = [];
      set(saved.tokens ? 'error' : 'off', e.message || String(e));
      return status;
    } finally {
      interactive = false;
      if (status !== 'login') stopListen();
    }
  }

  async function disconnect() {
    try { await client?.close(); } catch { /* 그만 */ }
    client = null; tools = []; instructions = '';
    saved = {};
    try { fs.rmSync(FILE(), { force: true }); } catch { /* 없음 */ }
    set('off');
  }

  const readOnly = (t) => t.annotations?.readOnlyHint === true || READ_ONLY.test(t.name);

  // 에이전트에게 보여 줄 도구 — 이름 앞에 ema__를 붙여 브라우저 도구와 섞이지 않게
  function agentTools() {
    if (status !== 'on') return [];
    return tools.map((t) => ({
      name: `ema__${t.name}`.slice(0, 64),
      description: `[EMA Station${readOnly(t) ? '' : ' · 실행 전 사용자 승인'}] ${String(t.description || '').slice(0, 900)}`,
      input_schema: t.inputSchema?.type === 'object' ? t.inputSchema : { type: 'object', properties: {} },
      eager_input_streaming: true,
    }));
  }
  const find = (agentName) => tools.find((t) => `ema__${t.name}`.slice(0, 64) === agentName);

  async function call(agentName, input) {
    const t = find(agentName);
    if (!t || !client) throw new Error('EMA 도구에 연결돼 있지 않아요');
    const r = await client.callTool({ name: t.name, arguments: input || {} });
    const text = (r.content || []).map((c) => (c.type === 'text' ? c.text : c.type === 'resource' ? (c.resource?.text || '') : `[${c.type}]`)).join('\n');
    const body = text || (r.structuredContent ? JSON.stringify(r.structuredContent) : '');
    return { text: body.length > MAX_RESULT ? `${body.slice(0, MAX_RESULT)}\n…(뒤 생략)` : body, isError: !!r.isError };
  }

  return {
    connect, disconnect, agentTools, call,
    needsConfirm: (agentName) => { const t = find(agentName); return !t || !readOnly(t); },
    instructions: () => (status === 'on' ? instructions : ''),
    snapshot: () => ({ status, error, tools: tools.length, url: new URL(url).host }),
  };
}

module.exports = { createEma, READ_ONLY };
