// 탭 안에서 돌리는 페이지 도구 — 페이지 자신의 스크립트와 분리된 격리 월드에서 실행한다.
// 같은 DOM을 보지만 JS 전역은 따로라, 페이지가 querySelectorAll 같은 걸 바꿔치기해도
// 우리 코드는 영향받지 않는다.
const WORLD = 1919;

const SNAPSHOT = `(() => {
  const MAX_TEXT = 12000, MAX_ELEMS = 150;
  const sel = 'a[href],button,input,textarea,select,summary,[role=button],[role=link],' +
              '[role=tab],[role=menuitem],[role=checkbox],[contenteditable=""],[contenteditable=true]';
  document.querySelectorAll('[data-a2a-idx]').forEach(e => e.removeAttribute('data-a2a-idx'));
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
                        .replace(/\\s+/g, ' ').trim().slice(0, 80);
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
  const text = (document.body ? document.body.innerText : '').replace(/\\n{3,}/g, '\\n\\n');
  return {
    url: location.href, title: document.title,
    text: text.slice(0, MAX_TEXT), truncated: text.length > MAX_TEXT,
    elements: out,
  };
})()`;

// 대상 요소 정보 + 화면 좌표(중심). 클릭은 실제 마우스 이벤트로 보낸다.
const locate = (idx) => `(() => {
  const e = document.querySelector('[data-a2a-idx="${Number(idx)}"]');
  if (!e) return null;
  e.scrollIntoView({block: 'center', inline: 'center'});
  const r = e.getBoundingClientRect();
  const form = e.closest('form');
  return {
    x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
    tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(),
    visible: (e.innerText || (/password|passwd|pwd|passcode|비밀번호|암호/i.test([e.getAttribute('type'), e.getAttribute('autocomplete'), e.name, e.id, e.getAttribute('placeholder')].join(' ')) ? '' : e.value) || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    aria: (e.getAttribute('aria-label') || e.getAttribute('title') || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    href: e.tagName === 'A' ? (e.href || '') : '',
    text: (e.getAttribute('aria-label') || e.innerText ||
           (/password|passwd|pwd|passcode|비밀번호|암호/i.test([e.getAttribute('type'), e.getAttribute('autocomplete'), e.name, e.id, e.getAttribute('placeholder')].join(' ')) ? '' : e.value) || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    inForm: !!form,
  };
})()`;

const focusAndClear = (idx) => `(() => {
  const e = document.querySelector('[data-a2a-idx="${Number(idx)}"]');
  if (!e) return false;
  e.scrollIntoView({block: 'center'});
  e.focus();
  if ('select' in e && typeof e.select === 'function') e.select();
  else if (e.isContentEditable) document.execCommand('selectAll');
  return true;
})()`;

const selectOption = (idx, value) => `(() => {
  const e = document.querySelector('[data-a2a-idx="${Number(idx)}"]');
  if (!e || e.tagName !== 'SELECT') return 'not_select';
  const want = ${JSON.stringify(String(value))};
  const opt = [...e.options].find(o => o.value === want || o.text.trim() === want);
  if (!opt) return 'no_option';
  e.value = opt.value;
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  return 'ok';
})()`;

function run(wc, code) {
  return wc.executeJavaScriptInIsolatedWorld(WORLD, [{ code }]);
}

async function snapshot(wc) {
  return run(wc, SNAPSHOT);
}

async function describe(wc, idx) {
  return run(wc, locate(idx));
}

async function click(wc, idx) {
  const t = await run(wc, locate(idx));
  if (!t) return null;
  await new Promise((r) => setTimeout(r, 120)); // scrollIntoView 반영
  const p = await run(wc, locate(idx));
  for (const type of ['mouseMove', 'mouseDown', 'mouseUp']) {
    wc.sendInputEvent({ type, x: p.x, y: p.y, button: 'left', clickCount: 1 });
  }
  return p;
}

async function typeText(wc, idx, text, submit) {
  if (!(await run(wc, focusAndClear(idx)))) return false;
  await wc.insertText(text);
  if (submit) {
    wc.sendInputEvent({ type: 'keyDown', keyCode: 'Return' });
    wc.sendInputEvent({ type: 'char', keyCode: '\r' });
    wc.sendInputEvent({ type: 'keyUp', keyCode: 'Return' });
  }
  return true;
}

async function choose(wc, idx, value) {
  return run(wc, selectOption(idx, value));
}

async function scroll(wc, direction) {
  const dy = direction === 'up' ? -0.8 : 0.8;
  return run(wc, `window.scrollBy(0, Math.round(innerHeight * ${dy})); [scrollY, document.body.scrollHeight]`);
}

module.exports = { snapshot, describe, click, typeText, choose, scroll };
