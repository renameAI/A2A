// 다운로드 — 받은 파일은 다운로드 폴더에, 진행·완료·실패를 셸에 알린다.
// 받은 파일은 믿을 수 없는 데이터다: 자동으로 열지 않고, 실행 파일 종류는 "열기" 대신 Finder에서만 보여 준다.
const { app, shell: osShell } = require('electron');
const path = require('path');
const fs = require('fs');

// 바로 열면 실행·설치·설정 변경이 되거나 브라우저로 로컬 파일을 여는 종류 — "열기" 대신 Finder에서만
const RISKY = /\.(app|pkg|mpkg|dmg|command|sh|bash|zsh|csh|py|rb|pl|exe|msi|bat|cmd|jar|jnlp|scpt|scptd|applescript|terminal|workflow|action|prefpane|tool|fileloc|inetloc|webloc|url|mobileconfig|kext|plugin|htm|html|xhtml|svg|shtml)$/i;
const reserved = new Set();   // 받는 중인 파일 이름 — 같은 이름을 동시에 받아도 서로 덮어쓰지 않게
const list = [];            // 최신이 앞 { id, filename, url, path, received, total, state, started, ended }
const items = new Map();    // id → DownloadItem(진행 중인 것만)
let seq = 0;

// 같은 이름이 있으면 "이름 (1).확장자"
function uniquePath(dir, name) {
  const safe = path.basename(name).replace(/[\x00-\x1f]/g, '_') || 'download';
  const ext = path.extname(safe);
  const stem = safe.slice(0, safe.length - ext.length);
  let p = path.join(dir, safe);
  for (let i = 1; fs.existsSync(p) || reserved.has(p); i++) p = path.join(dir, `${stem} (${i})${ext}`);
  reserved.add(p);
  return p;
}

function attach(ses, { onChange, onDone }) {
  ses.on('will-download', (_e, item) => {
    const id = `d${++seq}`;
    let p;
    try {
      p = uniquePath(app.getPath('downloads'), item.getFilename());
      item.setSavePath(p);           // 저장 경로를 바로 정해야 "다른 이름으로 저장" 창이 안 뜬다
    } catch (err) {
      console.error('[downloads] 저장 경로를 정하지 못함:', err);
      item.cancel();
      return;
    }
    const d = { id, filename: path.basename(p), url: item.getURL(), path: p, received: 0, total: item.getTotalBytes(),
                state: 'progressing', started: Date.now(), ended: null };
    list.unshift(d);
    list.splice(50);
    items.set(id, item);
    onChange();
    item.on('updated', (_ev, state) => {
      d.received = item.getReceivedBytes();
      d.total = item.getTotalBytes();
      d.state = state === 'interrupted' ? 'interrupted' : item.isPaused() ? 'paused' : 'progressing';
      onChange();
    });
    item.once('done', (_ev, state) => {
      d.received = item.getReceivedBytes();
      d.state = state;            // completed | cancelled | interrupted
      d.ended = Date.now();
      items.delete(id);
      reserved.delete(p);
      onChange();
      onDone(d);
    });
  });
}

function action(id, what) {
  if (what === 'clear') {
    for (let i = list.length - 1; i >= 0; i--) if (list[i].state !== 'progressing' && list[i].state !== 'paused') list.splice(i, 1);
    return;
  }
  const d = list.find((x) => x.id === id);
  if (!d) return;
  const item = items.get(id);
  if (what === 'cancel' && item) item.cancel();
  else if (what === 'pause' && item) item.pause();
  else if (what === 'resume' && item?.canResume()) item.resume();
  else if (what === 'reveal' && fs.existsSync(d.path)) osShell.showItemInFolder(d.path);
  else if (what === 'open' && d.state === 'completed' && !RISKY.test(d.path) && fs.existsSync(d.path)) osShell.openPath(d.path);
  else if (what === 'remove' && !item) list.splice(list.indexOf(d), 1);
}

const snapshot = () => list.map((d) => ({ ...d, risky: RISKY.test(d.filename), exists: d.state === 'completed' ? fs.existsSync(d.path) : true }));

module.exports = { attach, action, snapshot };
