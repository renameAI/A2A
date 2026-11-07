// 우리 UI(shell) 전용 다리 — 웹페이지 탭에는 붙지 않는다.
const { contextBridge, ipcRenderer } = require('electron');

const SEND = new Set(['agent:choose', 'ui:rects', 'ui:overlay', 'tab:new', 'tab:close', 'tab:reopen', 'tab:move', 'tab:switch', 'nav:go', 'nav:back', 'nav:forward',
  'nav:reload', 'nav:home', 'zoom', 'dl:action', 'history:remove', 'history:clear', 'find:go', 'find:stop', 'pw:resolve', 'pw:fill', 'pw:remove', 'sidebar:toggle', 'panel:set', 'bookmark:toggle', 'bookmark:remove', 'bookmark:open',
  'wallpaper:set', 'wallpaper:pick', 'task:open', 'page:open', 'web:open', 'notify',
  'prefs:set', 'ws:picker', 'ema:connect', 'ema:disconnect', 'map:open', 'agent:run', 'agent:stop', 'agent:reset', 'agent:answer']);
const ON = new Set(['ws:changed', 'ai:quote', 'pw:offer', 'pw:fillable', 'pw:resolved', 'pw:filled', 'find:result', 'state', 'agent', 'cmd', 'wallpaper-image', 'chat:event', 'chat:status', 'onto:changed', 'invest:changed', 'invest:progress', 'learn:changed', 'learn:progress']);
const INVOKE = new Set(['ws:profiles', 'ws:switch', 'bookmarks:import', 'history:query', 'history:suggest', 'chat:call', 'pw:list', 'pw:reveal', 'onto:call', 'invest:call', 'learn:call']);

contextBridge.exposeInMainWorld('a2a', {
  send: (ch, ...args) => { if (SEND.has(ch)) ipcRenderer.send(ch, ...args); },
  // 구독을 돌려준다 — 화면 조각이 사라질 때 끊을 수 있게
  on: (ch, cb) => {
    if (!ON.has(ch)) return () => {};
    const h = (_e, ...args) => cb(...args);
    ipcRenderer.on(ch, h);
    return () => ipcRenderer.removeListener(ch, h);
  },
  invoke: (ch, ...args) => (INVOKE.has(ch) ? ipcRenderer.invoke(ch, ...args) : Promise.reject(new Error('blocked'))),
});
