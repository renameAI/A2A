// 탭 안에서 돌리는 페이지 도구 — Electron page-tools.js의 확장판.
// 읽기는 chrome.scripting으로 확장의 격리 월드(ISOLATED)에서 돌린다: 같은 DOM을 보지만 JS 전역은 따로라,
// 페이지가 querySelectorAll 같은 걸 바꿔치기해도 우리 코드는 영향받지 않는다.
// 클릭·입력·스크린샷은 chrome.debugger(CDP)로 — 사람이 한 것과 같은 진짜 입력(isTrusted)이다.

// ── 격리 월드에서 도는 함수들(바깥 변수를 쓰면 안 된다 — 그대로 직렬화돼 탭에서 실행된다) ──
function SNAPSHOT() {
  const MAX_TEXT = 12000, MAX_ELEMS = 150;
  const sel = 'a[href],button,input,textarea,select,summary,[role=button],[role=link],' +
              '[role=tab],[role=menuitem],[role=checkbox],[contenteditable=""],[contenteditable=true]';
  document.querySelectorAll('[data-a2a-idx]').forEach((e) => e.removeAttribute('data-a2a-idx'));
  const visible = (e) => {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(e);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  // 비밀번호 칸의 값은 절대 읽지 않는다 — 에이전트(모델)에게 넘어가면 안 된다
  // 눈 버튼으로 type=text가 된 비밀번호 칸도 — 이름·아이디·autocomplete로 알아본다
  const secretField = (e) => (e.getAttribute('type') || '').toLowerCase() === 'password' ||
    /password|passwd|pwd|passcode|비밀번호|암호/i.test([e.getAttribute('autocomplete'), e.name, e.id, e.getAttribute('aria-label'), e.getAttribute('placeholder')].join(' '));
  const val = (e) => (secretField(e) ? '' : e.value);
  const label = (e) => (e.getAttribute('aria-label') || e.innerText || val(e) ||
                        e.getAttribute('placeholder') || e.getAttribute('title') ||
                        e.getAttribute('alt') || e.getAttribute('name') || '')
                        .replace(/\s+/g, ' ').trim().slice(0, 80);
  const out = [];
  let i = 0;
  for (const e of document.querySelectorAll(sel)) {
    if (!visible(e) || e.disabled) continue;
    i += 1;
    e.setAttribute('data-a2a-idx', String(i));
    const tag = e.tagName.toLowerCase();
    const type = e.getAttribute('type') || '';
    const kind = tag === 'a' ? 'link' : tag === 'input' ? 'input:' + (type || 'text') : tag;
    const href = tag === 'a' ? ' -> ' + (e.getAttribute('href') || '').slice(0, 100) : '';
    out.push('[' + i + '] ' + kind + ' "' + label(e) + '"' + href);
    if (out.length >= MAX_ELEMS) break;
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n');
  return { url: location.href, title: document.title, text: text.slice(0, MAX_TEXT), truncated: text.length > MAX_TEXT, elements: out };
}

// 대상 요소 정보 + 화면 좌표(중심, CSS 픽셀). 클릭은 이 좌표로 실제 마우스 이벤트를 보낸다.
function LOCATE(idx) {
  const e = document.querySelector(`[data-a2a-idx="${Number(idx)}"]`);
  if (!e) return null;
  e.scrollIntoView({ block: 'center', inline: 'center' });
  const r = e.getBoundingClientRect();
  const form = e.closest('form');
  const secret = /password|passwd|pwd|passcode|비밀번호|암호/i.test([e.getAttribute('type'), e.getAttribute('autocomplete'), e.name, e.id, e.getAttribute('placeholder')].join(' '));
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  return {
    x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
    tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(),
    visible: clean(e.innerText || (secret ? '' : e.value)),
    aria: clean(e.getAttribute('aria-label') || e.getAttribute('title')),
    href: e.tagName === 'A' ? (e.href || '') : '',
    text: clean(e.getAttribute('aria-label') || e.innerText || (secret ? '' : e.value)),
    secret,
    inForm: !!form,
  };
}

function FOCUS_AND_CLEAR(idx) {
  const e = document.querySelector(`[data-a2a-idx="${Number(idx)}"]`);
  if (!e) return false;
  e.scrollIntoView({ block: 'center' });
  e.focus();
  if ('select' in e && typeof e.select === 'function') e.select();
  else if (e.isContentEditable) document.execCommand('selectAll');
  return true;
}

function SELECT_OPTION(idx, want) {
  const e = document.querySelector(`[data-a2a-idx="${Number(idx)}"]`);
  if (!e || e.tagName !== 'SELECT') return 'not_select';
  const opt = [...e.options].find((o) => o.value === want || o.text.trim() === want);
  if (!opt) return 'no_option';
  e.value = opt.value;
  e.dispatchEvent(new Event('input', { bubbles: true }));
  e.dispatchEvent(new Event('change', { bubbles: true }));
  return 'ok';
}

function SCROLL(dy) {
  window.scrollBy(0, Math.round(innerHeight * dy));
  return [Math.round(scrollY), document.body ? document.body.scrollHeight : 0];
}

// ── 바깥(서비스 워커) ──
export const scriptable = (url) => /^(https?|file):/i.test(url || '');

async function run(tabId, func, args = []) {
  const [r] = await chrome.scripting.executeScript({ target: { tabId, frameIds: [0] }, world: 'ISOLATED', func, args });
  return r?.result;
}

// CDP — 작업하는 동안만 붙고 끝나면 뗀다(우리 Chromium 빌드에선 "디버깅 중" 안내줄을 숨긴다)
const attached = new Set();
chrome.debugger.onDetach.addListener(({ tabId }) => attached.delete(tabId));
chrome.tabs.onRemoved.addListener((tabId) => attached.delete(tabId));
async function cdp(tabId, method, params = {}) {
  if (!attached.has(tabId)) {
    await chrome.debugger.attach({ tabId }, '1.3');
    attached.add(tabId);
  }
  return chrome.debugger.sendCommand({ tabId }, method, params);
}
export async function detachAll() {
  for (const tabId of [...attached]) await chrome.debugger.detach({ tabId }).catch(() => {});
  attached.clear();
}

export const snapshot = (tabId) => run(tabId, SNAPSHOT);
export const describe = (tabId, idx) => run(tabId, LOCATE, [idx]);
export const choose = (tabId, idx, value) => run(tabId, SELECT_OPTION, [idx, String(value)]);
export const scroll = (tabId, direction) => run(tabId, SCROLL, [direction === 'up' ? -0.8 : 0.8]);

export async function click(tabId, idx) {
  if (!(await describe(tabId, idx))) return null;
  await new Promise((r) => setTimeout(r, 120));           // scrollIntoView 반영
  const p = await describe(tabId, idx);
  for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
    await cdp(tabId, 'Input.dispatchMouseEvent', { type, x: p.x, y: p.y, button: type === 'mouseMoved' ? 'none' : 'left', clickCount: 1 });
  }
  return p;
}

export async function typeText(tabId, idx, text, submit) {
  if (!(await run(tabId, FOCUS_AND_CLEAR, [idx]))) return false;
  await cdp(tabId, 'Input.insertText', { text });
  if (submit) {
    const k = { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 };
    await cdp(tabId, 'Input.dispatchKeyEvent', { type: 'rawKeyDown', ...k });
    await cdp(tabId, 'Input.dispatchKeyEvent', { type: 'char', ...k, text: '\r' });
    await cdp(tabId, 'Input.dispatchKeyEvent', { type: 'keyUp', ...k });
  }
  return true;
}

// 보이는 영역 JPEG(base64). 너비가 1280을 넘으면 줄인다.
export async function screenshot(tabId, { width = 1280, quality = 70 } = {}) {
  const { cssLayoutViewport: v } = await cdp(tabId, 'Page.getLayoutMetrics');
  const scale = Math.min(1, width / Math.max(1, v.clientWidth));
  const { data } = await cdp(tabId, 'Page.captureScreenshot', {
    format: 'jpeg', quality,
    clip: { x: v.pageX, y: v.pageY, width: v.clientWidth, height: v.clientHeight, scale },
  });
  return data;
}

// ── 이동 기다리기 ── 탭이 다 읽힐 때까지(안 바뀌면 settle만큼만)
export async function waitLoad(tabId, timeout = 15000) {
  const t0 = Date.now();
  for (;;) {
    const t = await chrome.tabs.get(tabId).catch(() => null);
    if (!t || t.status === 'complete' || Date.now() - t0 > timeout) break;
    await new Promise((r) => setTimeout(r, 150));
  }
  await new Promise((r) => setTimeout(r, 300));
}
export async function loadAndWait(tabId, act, settle = 300) {
  let started = false;
  const mark = (id, info) => { if (id === tabId && info.status === 'loading') started = true; };
  chrome.tabs.onUpdated.addListener(mark);
  let r;
  try { r = await act(); } finally {
    await new Promise((res) => setTimeout(res, settle));
    chrome.tabs.onUpdated.removeListener(mark);
  }
  const t = await chrome.tabs.get(tabId).catch(() => null);
  if (started || t?.status === 'loading') await waitLoad(tabId);
  return r;
}
