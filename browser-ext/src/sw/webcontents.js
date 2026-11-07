// Chromium 탭을 browser/events.js가 기대하는 Electron webContents 모양으로 — 수집기를 고치지 않고 쓰게.
// 쓰는 것만: id · getURL() · getTitle() · isDestroyed() · executeJavaScriptInIsolatedWorld() · on/once/off(did-finish-load·did-navigate-in-page·destroyed)
const wcs = new Map();            // tabId → wc

function make(tabId) {
  const ls = new Map();
  const wc = {
    id: tabId, url: '', title: '', destroyed: false,
    getURL: () => wc.url, getTitle: () => wc.title, isDestroyed: () => wc.destroyed,
    on(ev, fn) { (ls.get(ev) || ls.set(ev, new Set()).get(ev)).add(fn); return wc; },
    once(ev, fn) { const w = (...a) => { wc.off(ev, w); fn(...a); }; return wc.on(ev, w); },
    off(ev, fn) { ls.get(ev)?.delete(fn); return wc; },
    emit(ev, ...a) { for (const fn of [...(ls.get(ev) || [])]) { try { fn(...a); } catch (e) { console.warn('[wc]', ev, e); } } },
    // 수집기는 페이지 글만 읽는다 — 격리 월드(페이지 스크립트가 못 건드리는 자리)에서
    async executeJavaScriptInIsolatedWorld() {
      const [r] = await chrome.scripting.executeScript({
        target: { tabId }, world: 'ISOLATED',
        func: () => ({ url: location.href, title: document.title, text: (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n') }),
      });
      return r?.result ?? null;
    },
  };
  return wc;
}
const fresh = new Set();          // 새로 만든 wc — 처음 한 번은 watch 쪽에 알린다
export function wcOf(tabId, tab = null) {
  let wc = wcs.get(tabId);
  if (!wc) { wc = make(tabId); wcs.set(tabId, wc); fresh.add(tabId); }
  if (tab) { wc.url = tab.url || tab.pendingUrl || wc.url; wc.title = tab.title ?? wc.title; }
  return wc;
}
// 새 탭마다 한 번 불린다(수집기 watchTab을 붙이는 자리)
let onNew = () => {};
export const onNewTab = (fn) => { onNew = fn; };
function seen(tabId, tab) {
  const wc = wcOf(tabId, tab);
  if (fresh.delete(tabId)) onNew(wc, tabId);
  return wc;
}

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  const wc = seen(tabId, tab);
  if (info.status === 'complete') wc.emit('did-finish-load');
  else if (info.url && !info.status) wc.emit('did-navigate-in-page');   // 같은 문서 안 주소 바뀜(SPA)
});
chrome.tabs.onRemoved.addListener((tabId) => {
  const wc = wcs.get(tabId);
  if (!wc) return;
  wc.destroyed = true;
  wc.emit('destroyed');
  wcs.delete(tabId);
});
// 서비스 워커가 깨어났을 때 이미 열린 탭
export async function adoptOpenTabs() {
  for (const t of await chrome.tabs.query({}).catch(() => [])) seen(t.id, t);
}
