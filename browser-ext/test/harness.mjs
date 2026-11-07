// A2A 확장 시험 — 공식 Chromium(또는 우리 빌드)을 확장과 함께 별도 프로필로 띄우고 CDP로 화면을 확인한다.
//   node test/harness.mjs [--only 이름,이름] [--keep] [--show]
// 결과: test/out/report.json (+ 스크린샷). 원격 디버깅 포트를 켜고 띄우므로 이 창에선 구글 로그인을 하지 않는다.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const DIST = path.join(ROOT, 'dist');
const OUT = path.join(HERE, 'out');
const ID = fs.readFileSync(path.join(ROOT, 'extension-id.txt'), 'utf8').trim();
const EXT = `chrome-extension://${ID}`;
const CHROMIUM = process.env.A2A_CHROMIUM || findChromium();
const args = process.argv.slice(2);
const only = (args[args.indexOf('--only') + 1] || '').split(',').filter((x) => args.includes('--only') && x);

function findChromium() {
  const snap = path.join(os.homedir(), 'a2a-chromium', 'snapshot');
  const revs = fs.existsSync(snap) ? fs.readdirSync(snap).sort((a, b) => Number(b) - Number(a)) : [];
  for (const r of revs) {
    const p = path.join(snap, r, 'chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium');
    if (fs.existsSync(p)) return p;
  }
  throw new Error('Chromium을 찾지 못했습니다 — A2A_CHROMIUM으로 실행 파일 경로를 주세요');
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── 아주 작은 CDP 클라이언트(브라우저 연결 하나 + flatten 세션) ──
class CDP {
  constructor(url) { this.url = url; this.id = 0; this.waiting = new Map(); this.events = []; this.listeners = new Set(); }
  open() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url);
      this.ws.onopen = () => resolve();
      this.ws.onerror = (e) => reject(e);
      this.ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.id && this.waiting.has(m.id)) {
          const { resolve: ok, reject: no } = this.waiting.get(m.id);
          this.waiting.delete(m.id);
          if (m.error) no(new Error(`${m.error.message}`)); else ok(m.result);
        } else if (m.method) { for (const l of this.listeners) l(m); }
      };
    });
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    return new Promise((resolve, reject) => {
      this.waiting.set(id, { resolve, reject });
      setTimeout(() => { if (this.waiting.has(id)) { this.waiting.delete(id); reject(new Error(`CDP 시간 초과: ${method}`)); } }, 30000);
    });
  }
  close() { try { this.ws.close(); } catch { /* 그만 */ } }
}

// 페이지(탭) 하나 — 열고, 붙고, 평가하고, 캡처
export class Page {
  constructor(cdp, targetId, sessionId) { this.cdp = cdp; this.targetId = targetId; this.sessionId = sessionId; }
  static async open(cdp, url) {
    const { targetId } = await cdp.send('Target.createTarget', { url });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    const p = new Page(cdp, targetId, sessionId);
    await p.cdp.send('Runtime.enable', {}, sessionId);
    return p;
  }
  async eval(expr) {
    const r = await this.cdp.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, this.sessionId);
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  }
  async waitFor(expr, ms = 10000) {
    const t0 = Date.now();
    for (;;) {
      try { const v = await this.eval(expr); if (v) return v; } catch { /* 아직 */ }
      if (Date.now() - t0 > ms) return null;
      await sleep(200);
    }
  }
  async shot(file, { width = 1280, height = 800 } = {}) {
    await this.cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }, this.sessionId).catch(() => {});
    const { data } = await this.cdp.send('Page.captureScreenshot', { format: 'png' }, this.sessionId);
    fs.writeFileSync(path.join(OUT, file), Buffer.from(data, 'base64'));
  }
  // 사람처럼 입력 — 포커스된 칸에 글자 넣기 / 키 누르기(React 입력 이벤트가 그대로 돈다)
  async focus(sel) { await this.eval(`document.querySelector(${JSON.stringify(sel)}).focus()`); }
  async type(text) { await this.cdp.send('Input.insertText', { text }, this.sessionId); await sleep(120); }
  async key(name) {
    const codes = { Enter: 13, Escape: 27, Tab: 9, ArrowDown: 40 };
    const base = { key: name, code: name, windowsVirtualKeyCode: codes[name], nativeVirtualKeyCode: codes[name] };
    await this.cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...base, ...(name === 'Enter' ? { text: '\r' } : {}) }, this.sessionId);
    await this.cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...base }, this.sessionId);
    await sleep(150);
  }
  async clickText(text) {
    return this.eval(`(() => { const b = [...document.querySelectorAll('button')].find((x) => x.innerText.trim() === ${JSON.stringify(text)}); b?.click(); return !!b; })()`);
  }
  close() { return this.cdp.send('Target.closeTarget', { targetId: this.targetId }).catch(() => {}); }
}

// 서비스 워커에서 평가 — chrome.* API를 직접 부를 때
export async function swEval(cdp, expr) {
  const { targetInfos } = await cdp.send('Target.getTargets');
  const sw = targetInfos.find((t) => t.type === 'service_worker' && t.url.startsWith(EXT));
  if (!sw) throw new Error('확장 서비스 워커가 없습니다');
  const { sessionId } = await cdp.send('Target.attachToTarget', { targetId: sw.targetId, flatten: true });
  // 막 깨어난 서비스 워커는 잠깐 chrome.*가 없는 문맥으로 붙는다 — 그때만 몇 번 다시 묻는다
  let r;
  for (let i = 0; i < 20; i++) {
    r = await cdp.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId);
    if (!/chrome is not defined/.test(r.exceptionDetails?.exception?.description || '')) break;
    await sleep(250);
  }
  await cdp.send('Target.detachFromTarget', { sessionId }).catch(() => {});
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}

async function launch() {
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'a2a-ext-'));
  const proc = spawn(CHROMIUM, [
    // 포트 0: 비어 있는 포트를 OS가 고른다 — 다른 프로그램이 띄운 크롬(같은 포트)에 잘못 붙지 않게
    `--user-data-dir=${profile}`, '--remote-debugging-port=0',
    // A2A_BUILTIN=1: 우리 Chromium 빌드에 내장된 확장(앱 번들 Resources/a2a)을 그대로 시험한다 — 바깥에서 싣지 않는다
    ...(process.env.A2A_BUILTIN ? [] : [`--load-extension=${DIST}`, `--disable-extensions-except=${DIST}`,
      '--disable-features=DisableLoadExtensionCommandLineSwitch']),
    '--no-first-run', '--no-default-browser-check', '--window-size=1400,900',
    // 시험 창은 화면에 띄우지 않는다(같이 보는 개발 창과 헷갈리지 않게) — 보고 싶으면 --show
    ...(args.includes('--show') ? [] : ['--headless=new']),
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  let err = '';
  proc.stderr.on('data', (d) => { err += d; if (err.length > 20000) err = err.slice(-20000); });
  const portFile = path.join(profile, 'DevToolsActivePort');
  for (let i = 0; i < 100; i++) {
    try {
      // 우리가 띄운 Chromium이 자기 프로필에 적어 둔 포트만 쓴다
      const port = fs.readFileSync(portFile, 'utf8').split('\n')[0].trim();
      const v = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
      const cdp = new CDP(v.webSocketDebuggerUrl);
      await cdp.open();
      return { proc, cdp, profile, stderr: () => err, version: v.Browser };
    } catch { await sleep(200); }
  }
  proc.kill();
  throw new Error(`Chromium이 뜨지 않았습니다\n${err.slice(-2000)}`);
}

// ── 시험 목록 — 단계(M1…)마다 여기에 더한다 ──
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
(await import('./tests.mjs')).default(test);

const env = await launch();
const report = { chromium: env.version, extensionId: ID, steps: [] };
console.log(`Chromium ${env.version} · 확장 ${ID}`);
// 서비스 워커가 뜰 때까지
for (let i = 0; i < 50; i++) {
  const { targetInfos } = await env.cdp.send('Target.getTargets');
  if (targetInfos.some((t) => t.type === 'service_worker' && t.url.startsWith(EXT))) break;
  await sleep(200);
}
for (const t of tests) {
  if (only.length && !only.includes(t.name)) continue;
  const t0 = Date.now();
  try {
    const detail = await t.fn({ cdp: env.cdp, EXT, ID, Page, swEval, sleep, OUT });
    const ok = detail?.ok !== false;
    report.steps.push({ name: t.name, ok, ms: Date.now() - t0, detail });
  } catch (e) {
    report.steps.push({ name: t.name, ok: false, ms: Date.now() - t0, detail: String(e?.stack || e) });
  }
  const s = report.steps[report.steps.length - 1];
  console.log(`${s.ok ? '✓' : '✗'} ${s.name}${s.ok ? '' : ` — ${JSON.stringify(s.detail).slice(0, 300)}`}`);
}
fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2));
const passed = report.steps.filter((s) => s.ok).length;
console.log(`\n${passed}/${report.steps.length} 통과`);
env.cdp.close();
if (!args.includes('--keep')) {
  env.proc.kill();
  await sleep(800);                      // Chromium이 프로필 파일을 놓을 때까지
  try { fs.rmSync(env.profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }); } catch { /* 임시 폴더 — 남아도 그만 */ }
}
process.exit(passed === report.steps.length ? 0 : 1);
