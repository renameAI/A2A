// 개발 창 — 확장을 실은 Chromium을 계속 띄워 두고(같이 보면서 개발), 빌드하면 확장만 다시 읽힌다.
//   node scripts/dev.mjs           창 띄우기(이미 떠 있으면 확장만 새로 읽기)
//   node scripts/dev.mjs reload    확장만 새로 읽기(열린 사이드 패널·새 탭도 다시 그려진다)
//   node scripts/dev.mjs shot      지금 앞에 있는 탭 화면을 test/out/dev-shot.png로
// 프로필은 ~/a2a-chromium/profile-ext(시험용 임시 프로필과 따로, 창을 닫아도 남는다).
// 원격 디버깅 포트(127.0.0.1, 빈 포트)를 켜고 띄우므로 이 창에선 구글 등 실제 계정 로그인을 하지 않는다.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIST = path.join(ROOT, 'dist');
const ID = fs.readFileSync(path.join(ROOT, 'extension-id.txt'), 'utf8').trim();
const PROFILE = path.join(os.homedir(), 'a2a-chromium', 'profile-ext');
const snap = path.join(os.homedir(), 'a2a-chromium', 'snapshot');
const rev = fs.readdirSync(snap).sort((a, b) => Number(b) - Number(a))[0];
const CHROMIUM = process.env.A2A_CHROMIUM || path.join(snap, rev, 'chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function browser() {
  try {
    const port = fs.readFileSync(path.join(PROFILE, 'DevToolsActivePort'), 'utf8').split('\n')[0].trim();
    const v = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
    return { port, ws: v.webSocketDebuggerUrl };
  } catch { return null; }
}
function cdp(ws) {
  const sock = new WebSocket(ws);
  let id = 0;
  const wait = new Map();
  sock.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const n = ++id;
    wait.set(n, (m) => (m.error ? reject(new Error(m.error.message)) : resolve(m.result)));
    sock.send(JSON.stringify({ id: n, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  return new Promise((r) => { sock.onopen = () => r({ send, close: () => sock.close() }); });
}
async function swEval(c, expr) {
  const { targetInfos } = await c.send('Target.getTargets');
  const sw = targetInfos.find((t) => t.type === 'service_worker' && t.url.startsWith(`chrome-extension://${ID}/`));
  if (!sw) throw new Error('서비스 워커가 없습니다');
  const { sessionId } = await c.send('Target.attachToTarget', { targetId: sw.targetId, flatten: true });
  const r = await c.send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }, sessionId);
  await c.send('Target.detachFromTarget', { sessionId }).catch(() => {});
  return r.result?.value;
}

const cmd = process.argv[2] || 'open';
let b = await browser();
if (!b && cmd === 'open') {
  fs.mkdirSync(PROFILE, { recursive: true });
  try { fs.rmSync(path.join(PROFILE, 'DevToolsActivePort')); } catch { /* 없음 */ }
  const p = spawn(CHROMIUM, [
    `--user-data-dir=${PROFILE}`, '--remote-debugging-port=0',
    `--load-extension=${DIST}`, '--disable-features=DisableLoadExtensionCommandLineSwitch',
    '--enable-unsafe-extension-debugging',        // CDP Extensions.loadUnpacked로 확장만 다시 읽기(runtime.reload는 명령줄로 실은 확장을 내려 버린다)
    '--silent-debugger-extension-api',            // 에이전트가 일할 때 "디버깅 중" 안내줄을 숨긴다(우리 빌드의 기본값 후보)
    '--no-first-run', '--no-default-browser-check', '--window-size=1500,950',
  ], { detached: true, stdio: ['ignore', 'ignore', fs.openSync(path.join(PROFILE, '..', 'profile-ext.log'), 'w')] });
  p.unref();
  for (let i = 0; i < 100 && !(b = await browser()); i++) await sleep(200);
  console.log(b ? `개발 창을 띄웠습니다(포트 ${b.port})` : 'Chromium이 뜨지 않았습니다');
  process.exit(b ? 0 : 1);
}
if (!b) { console.log('개발 창이 없습니다 — node scripts/dev.mjs'); process.exit(1); }
const c = await cdp(b.ws);
if (cmd === 'open' || cmd === 'reload') {
  // 같은 경로를 다시 실으면 같은 id(매니페스트 key)로 덮어써진다 — 저장소(키·작업)는 그대로
  const r = await c.send('Extensions.loadUnpacked', { path: DIST }).catch((e) => ({ error: e.message }));
  console.log(r.error ? `다시 읽기 실패: ${r.error}` : `확장을 새로 읽었습니다(${r.id})`);
} else if (cmd === 'shot') {
  const { targetInfos } = await c.send('Target.getTargets');
  const page = targetInfos.find((t) => t.type === 'page' && t.attached) || targetInfos.find((t) => t.type === 'page');
  const { sessionId } = await c.send('Target.attachToTarget', { targetId: page.targetId, flatten: true });
  const { data } = await c.send('Page.captureScreenshot', { format: 'png' }, sessionId);
  fs.mkdirSync(path.join(ROOT, 'test', 'out'), { recursive: true });
  fs.writeFileSync(path.join(ROOT, 'test', 'out', 'dev-shot.png'), Buffer.from(data, 'base64'));
  console.log(`test/out/dev-shot.png ← ${page.url}`);
} else if (cmd === 'eval') {
  console.log(JSON.stringify(await swEval(c, process.argv[3]), null, 2));
}
c.close();
