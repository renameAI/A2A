// 다운로드·방문 기록 — Chromium이 실제로 가진 기록을 화면이 Electron 때와 같은 모양으로 쓰게 옮긴다.
import { on, handle } from './hub.js';
import { pushState } from './state.js';

// 바로 열면 실행·설치·설정 변경이 되는 종류 — "열기" 대신 Finder에서만(Electron 때와 같은 목록)
const RISKY = /\.(app|pkg|mpkg|dmg|command|sh|bash|zsh|csh|py|rb|pl|exe|msi|bat|cmd|jar|jnlp|scpt|scptd|applescript|terminal|workflow|action|prefpane|tool|fileloc|inetloc|webloc|url|mobileconfig|kext|plugin|htm|html|xhtml|svg|shtml)$/i;
const base = (p) => String(p || '').split('/').pop();

export async function downloadsList() {
  const items = await chrome.downloads.search({ orderBy: ['-startTime'], limit: 50 }).catch(() => []);
  return items.map((d) => ({
    id: String(d.id), filename: base(d.filename) || base(d.url), url: d.finalUrl || d.url, path: d.filename,
    received: d.bytesReceived, total: d.totalBytes,
    state: d.state === 'in_progress' ? (d.paused ? 'paused' : 'progressing') : d.state === 'complete' ? 'completed' : d.error === 'USER_CANCELED' ? 'cancelled' : 'interrupted',
    started: Date.parse(d.startTime), ended: d.endTime ? Date.parse(d.endTime) : null,
    exists: d.exists !== false, risky: RISKY.test(d.filename || ''),
  }));
}
chrome.downloads.onCreated.addListener(pushState);
chrome.downloads.onChanged.addListener(pushState);
chrome.downloads.onErased.addListener(pushState);

on('dl:action', async (id, what) => {
  const n = Number(id);
  if (what === 'clear') { await chrome.downloads.erase({ state: 'complete' }); await chrome.downloads.erase({ state: 'interrupted' }); return pushState(); }
  if (!Number.isInteger(n)) return;
  const [d] = await chrome.downloads.search({ id: n });
  if (!d) return;
  if (what === 'pause') await chrome.downloads.pause(n);
  else if (what === 'resume' && d.canResume) await chrome.downloads.resume(n);
  else if (what === 'cancel') await chrome.downloads.cancel(n);
  else if (what === 'reveal') chrome.downloads.show(n);
  else if (what === 'open' && d.state === 'complete' && !RISKY.test(d.filename || '')) chrome.downloads.open(n);
  else if (what === 'remove' && d.state !== 'in_progress') await chrome.downloads.erase({ id: n });
  pushState();
});

// 방문 기록 — 에이전트가 연 주소는 따로 적어 두었다가 "AI" 표시를 붙인다
const AGENT_URLS = 'agentUrls';
export async function markAgentVisit(url) {
  const { [AGENT_URLS]: list = [] } = await chrome.storage.local.get(AGENT_URLS);
  if (!list.includes(url)) chrome.storage.local.set({ [AGENT_URLS]: [url, ...list].slice(0, 2000) });
}
handle('history:query', async (q) => {
  const items = await chrome.history.search({ text: typeof q === 'string' ? q.slice(0, 200) : '', startTime: 0, maxResults: 300 });
  const { [AGENT_URLS]: agent = [] } = await chrome.storage.local.get(AGENT_URLS);
  const a = new Set(agent);
  return items.filter((h) => /^https?:/.test(h.url)).map((h) => ({ url: h.url, title: h.title || '', at: h.lastVisitTime, count: h.visitCount, agent: a.has(h.url) }));
});
on('history:remove', (url) => { if (typeof url === 'string') chrome.history.deleteUrl({ url }); });
on('history:clear', (range) => {
  const since = { hour: 3600e3, day: 864e5 }[range];
  if (since) chrome.history.deleteRange({ startTime: Date.now() - since, endTime: Date.now() });
  else if (range === 'all') chrome.history.deleteAll();
});
