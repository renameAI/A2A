// 서비스 워커의 메시지 허브 — Electron main.js의 ipcMain.on / ipcMain.handle / webContents.send 자리.
// 우리 확장 페이지에서 온 포트만 받는다(다른 확장·웹페이지는 runtime.connect로 여기 못 붙는다 —
// externally_connectable을 두지 않았다). 그래도 sender의 origin을 한 번 더 본다.
const ports = new Set();
const onHandlers = new Map();
const invokeHandlers = new Map();
const SELF = `chrome-extension://${chrome.runtime.id}`;

// 같은 신호를 여러 모듈이 들을 수 있다(예: ui:hello — 상태 보내기 + 채팅 연결 상태 보내기)
export const on = (ch, fn) => onHandlers.set(ch, [...(onHandlers.get(ch) || []), fn]);
export const handle = (ch, fn) => invokeHandlers.set(ch, fn);
export function sendTo(port, ch, ...args) { try { port.postMessage({ kind: 'event', ch, args }); } catch { /* 닫힌 화면 */ } }
export function broadcast(ch, ...args) {
  for (const p of ports) { try { p.postMessage({ kind: 'event', ch, args }); } catch { /* 닫힌 화면 */ } }
}
export const uiCount = () => ports.size;
// 화면이 하나라도 열렸다/모두 닫혔다 — 팀 채팅 실시간 연결을 켜고 끄는 데 쓴다
const portWatchers = new Set();
export const onPorts = (fn) => portWatchers.add(fn);
const portsChanged = () => { for (const fn of portWatchers) { try { fn(ports.size); } catch (e) { console.error('[hub]', e); } } };

chrome.runtime.onConnect.addListener((port) => {
  if (!port.name.startsWith('ui:') || port.sender?.id !== chrome.runtime.id || !String(port.sender?.url || '').startsWith(SELF)) {
    port.disconnect();
    return;
  }
  ports.add(port);
  portsChanged();
  port.onDisconnect.addListener(() => { ports.delete(port); portsChanged(); });
  port.onMessage.addListener(async (m) => {
    if (m.kind === 'send') {
      for (const fn of onHandlers.get(m.ch) || []) {
        try { await fn(...(m.args || []), port); } catch (e) { console.error('[hub]', m.ch, e); }
      }
    }
    if (m.kind === 'invoke') {
      const fn = invokeHandlers.get(m.ch);
      try {
        if (!fn) throw new Error(`알 수 없는 요청: ${m.ch}`);
        port.postMessage({ kind: 'reply', id: m.id, ok: true, value: await fn(...(m.args || []), port) });
      } catch (e) {
        port.postMessage({ kind: 'reply', id: m.id, ok: false, error: String(e?.message || e) });
      }
    }
  });
});
