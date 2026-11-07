import '../shared/bridge.js';
const el = document.getElementById('root');
window.a2a.invoke('sys:ping').then((r) => { el.textContent = 'A2A newtab 연결됨 ' + JSON.stringify(r); el.dataset.ok = '1'; })
  .catch((e) => { el.textContent = '연결 실패 ' + e.message; });
