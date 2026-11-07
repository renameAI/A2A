// 로그인 감지·채우기 — 웹 탭마다 붙는다.
//
// 페이지 스크립트와 분리된 격리 월드(PW_WORLD)에 작은 감시 코드를 넣는다. 로그인 제출을 보면
// 값은 격리 월드 안에만 담아 두고, 콘솔로 "가져가라" 신호만 보낸다. 신호는 시작할 때 만든 비밀 값이라
// 페이지가 흉내 낼 수 없다(격리 월드의 변수는 페이지에서 안 보인다). 신호를 받은 메인이 값을 꺼내 간다.
// 비밀번호가 콘솔이나 로그에 찍히는 일은 없다.
//
// 에이전트 도구(page-tools.js, 다른 격리 월드)는 이 월드와 금고에 닿을 수 없다.
const crypto = require('crypto');
const vault = require('./vault');

const PW_WORLD = 1920;
const NONCE = `a2a-pw-${crypto.randomBytes(16).toString('hex')}`;

const HELPERS = `
  const visible = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const pwField = (root) => [...(root || document).querySelectorAll('input[type=password]')].find((i) => visible(i));
  const userField = (pw) => {
    const inputs = [...(pw.form || document).querySelectorAll('input')];
    const i = inputs.indexOf(pw);
    return inputs.slice(0, i).reverse().find((x) => /^(text|email|tel)?$/i.test(x.getAttribute('type') || '') && visible(x));
  };`;

const HOOK = `(() => {
  if (window.__a2aPwHook) return true;
  window.__a2aPwHook = true;
  ${HELPERS}
  let last = null;
  const grab = (root) => {
    const pw = [...(root || document).querySelectorAll('input[type=password]')].find((i) => i.value);
    if (!pw) return;
    const u = userField(pw);
    last = { username: u ? u.value : '', password: pw.value };
    console.debug(${JSON.stringify(NONCE)});
  };
  // 사람이 한 동작(isTrusted)만 — 페이지 스크립트가 값을 넣고 눌러서 "저장할까요?"를 꾸며 내지 못하게.
  // 클릭은 제출 버튼만 — "비밀번호 보기" 눈 버튼(type=button)을 누를 때 반쯤 친 값이 제안되지 않게.
  const submitBtn = (b) => {
    const t = (b.getAttribute('type') || '').toLowerCase();
    return t === 'submit' || t === 'image' || (b.tagName === 'BUTTON' && !t && !!b.form);
  };
  document.addEventListener('submit', (e) => { if (e.isTrusted) grab(e.target); }, true);
  document.addEventListener('keydown', (e) => {
    if (!e.isTrusted || e.key !== 'Enter' || !e.target || !e.target.getAttribute) return;
    if ((e.target.getAttribute('type') || '').toLowerCase() === 'password') grab(e.target.form || document);
  }, true);
  document.addEventListener('click', (e) => {
    const b = e.isTrusted && e.target && e.target.closest && e.target.closest('button, input[type=submit], input[type=image]');
    if (b && submitBtn(b)) grab(b.form || document);
  }, true);
  window.__a2aTake = () => { const v = last; last = null; return v; };
  window.__a2aHasLogin = () => !!pwField();
  return true;
})()`;

const fillCode = (username, password) => `(() => {
  ${HELPERS}
  const pw = pwField();
  if (!pw) return false;
  const set = (el, v) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const u = userField(pw);
  if (u && ${JSON.stringify(username)}) set(u, ${JSON.stringify(username)});
  set(pw, ${JSON.stringify(password)});
  return true;
})()`;

const run = (wc, code) => wc.executeJavaScriptInIsolatedWorld(PW_WORLD, [{ code }]);

const pending = new Map();      // 저장 제안 id → { origin, username, password, at }

// 웹 탭 하나에 붙인다. onOffer: "저장할까요?" 제안, onFillable: 이 페이지에서 채울 수 있는 계정 목록
function attach(tab, wc, { onOffer, onFillable }) {
  const check = async () => {
    try {
      if (wc.isDestroyed()) return;
      await run(wc, HOOK);
      const origin = vault.originOf(wc.getURL());
      const has = origin ? await run(wc, 'window.__a2aHasLogin && window.__a2aHasLogin()') : false;
      onFillable(tab.id, origin, has ? vault.forOrigin(origin).map(({ id, username }) => ({ id, username })) : []);
    } catch { /* 페이지가 바뀌는 중 */ }
  };
  wc.on('did-finish-load', () => { check(); setTimeout(check, 1500); });   // 늦게 그려지는 로그인 폼도
  wc.on('did-navigate-in-page', check);

  wc.on('console-message', async (...args) => {
    const msg = args[0] && typeof args[0] === 'object' && 'message' in args[0] ? args[0].message : args[2];
    if (msg !== NONCE) return;
    let c;
    try { c = await run(wc, 'window.__a2aTake && window.__a2aTake()'); } catch { return; }
    const origin = vault.originOf(wc.getURL());
    if (!c || !c.password || !origin || vault.isNever(origin)) return;
    const cmp = vault.compare(origin, c.username, c.password);
    if (cmp === 'same') return;
    const id = crypto.randomUUID();
    pending.set(id, { origin, username: c.username, password: c.password, at: Date.now() });
    for (const [k, v] of pending) if (Date.now() - v.at > 5 * 60e3) pending.delete(k);   // 오래된 제안은 버린다
    onOffer({ id, tabId: tab.id, origin, username: c.username, update: cmp === 'changed' });
  });
}

module.exports = {
  attach,
  status: vault.status,
  list: vault.list,
  // 제안 처리: save | never | dismiss
  resolve(id, action) {
    const p = pending.get(id);
    pending.delete(id);
    if (!p) return { ok: false };
    if (action === 'save') { vault.upsert(p.origin, p.username, p.password); return { ok: true }; }
    if (action === 'never') { vault.never(p.origin); return { ok: true }; }
    return { ok: true };
  },
  // 채우기 — 저장된 사이트와 지금 페이지 주소(origin)가 정확히 같을 때만
  async fill(wc, entryId) {
    const s = vault.secret(entryId);
    if (!s || s.origin !== vault.originOf(wc.getURL())) return false;
    const ok = await run(wc, fillCode(s.username, s.password));
    if (ok) vault.touch(entryId);
    return ok;
  },
  secret: vault.secret,
  remove: vault.remove,
};
