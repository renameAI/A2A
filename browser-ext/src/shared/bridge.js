// 확장 페이지(사이드 패널·새 탭)용 window.a2a — Electron preload와 같은 모양(send/on/invoke)을
// chrome.runtime 포트로 서비스 워커에 잇는다. 그래서 Electron 때 만든 React 화면을 거의 그대로 쓴다.
// 서비스 워커는 MV3에서 잠들었다 깨기도 하니, 끊기면 다시 붙고 화면은 상태를 다시 받는다.
const listeners = new Map();      // ch → Set(cb)
const pending = new Map();        // invoke id → { resolve, reject }
let port = null;
let seq = 0;

function connect() {
  port = chrome.runtime.connect({ name: `ui:${location.pathname.replace(/^\//, '')}` });
  port.onMessage.addListener((m) => {
    if (m.kind === 'event') for (const cb of listeners.get(m.ch) || []) cb(...m.args);
    if (m.kind === 'reply') {
      const p = pending.get(m.id);
      if (!p) return;
      pending.delete(m.id);
      if (m.ok) p.resolve(m.value); else p.reject(new Error(m.error));
    }
  });
  port.onDisconnect.addListener(() => {
    for (const p of pending.values()) p.reject(new Error('서비스 워커와 연결이 끊겼습니다'));
    pending.clear();
    port = null;
    setTimeout(() => { connect(); a2a.send('ui:hello'); }, 300);
  });
}
connect();
// 서비스 워커가 잠들지 않게 — 화면이 열려 있는 동안 25초마다 한 번
setInterval(() => port?.postMessage({ kind: 'send', ch: 'ui:ping', args: [] }), 25000);

const a2a = {
  send: (ch, ...args) => port?.postMessage({ kind: 'send', ch, args }),
  on: (ch, cb) => {
    if (!listeners.has(ch)) listeners.set(ch, new Set());
    listeners.get(ch).add(cb);
    return () => listeners.get(ch)?.delete(cb);
  },
  invoke: (ch, ...args) => new Promise((resolve, reject) => {
    if (!port) { reject(new Error('연결 안 됨')); return; }
    const id = ++seq;
    pending.set(id, { resolve, reject });
    port.postMessage({ kind: 'invoke', id, ch, args });
  }),
};
window.a2a = a2a;
a2a.send('ui:hello');
export default a2a;
