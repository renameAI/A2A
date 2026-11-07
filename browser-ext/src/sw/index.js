// A2A 서비스 워커 — 상태·탭·작업·에이전트·팀 채팅 다리(Electron main.js의 확장판).
import '../shim/globals.js';
import { on, handle, broadcast } from './hub.js';
import { store, runtime, loadStore, saveStore, pushState, profile } from './state.js';
import { CFG } from './config.js';
import './browserdata.js';
import './chat.js';
import './tasks.js';
import './menus.js';

// 툴바 버튼을 누르면 사이드 패널이 열리게
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});
loadStore().then(pushState);

on('ui:ping', () => {});
on('ui:hello', () => { pushState(); broadcast('hello', { at: Date.now() }); });
handle('sys:ping', () => ({ pong: true, id: chrome.runtime.id, version: chrome.runtime.getManifest().version }));

// ── 탭 ── (Electron의 tab:* — 실제 탭은 Chromium이 맡고 우리는 고르기·열기만)
on('tab:switch', async (id) => {
  if (!Number.isInteger(id)) return;
  const t = await chrome.tabs.update(id, { active: true }).catch(() => null);
  if (t) chrome.windows.update(t.windowId, { focused: true }).catch(() => {});
});
on('tab:new', (url) => chrome.tabs.create(typeof url === 'string' && /^https?:\/\//.test(url) ? { url } : {}));

// ── 설정 ──
on('prefs:set', async (key, value) => {
  await loadStore();
  if (key === 'notify' && typeof value === 'boolean') store.prefs.notify = value;
  else if (key === 'askProfile' && typeof value === 'boolean') store.prefs.askProfile = value;
  else return;
  saveStore();
  pushState();
});
on('wallpaper:set', async (name) => { await loadStore(); store.wallpaper = String(name).slice(0, 32); saveStore(); pushState(); });
// 개발용 API 키 — 마이페이지에서 넣는다(운영에선 엔진 프록시로 바꾼다)
handle('key:set', async (k) => {
  await loadStore();
  if (typeof k !== 'string' || (k && !/^sk-ant-[\w-]{20,}$/.test(k.trim()))) throw new Error('Anthropic API 키 모양이 아닙니다');
  store.apiKey = k.trim();
  saveStore();
  pushState();
  return { ok: true };
});

// 작업 열기 — 그 작업이 일하던 탭으로
on('task:open', async (id) => {
  await loadStore();
  const t = store.tasks.find((x) => x.id === id);
  if (t?.tabId) chrome.tabs.update(t.tabId, { active: true }).catch(() => {});
});

// A2A 웹앱에서 열기(채널 머리의 ↗)
on('web:open', () => chrome.tabs.create({ url: `${CFG.web}/?as=${encodeURIComponent(profile().user)}` }));
