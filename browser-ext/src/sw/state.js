// 서비스 워커의 상태 — Electron main.js의 store + pushState 자리.
// 오래 남길 것(작업·설정·프로필·배경)은 chrome.storage.local, 탭은 chrome.tabs에서 그때그때 읽는다.
import { broadcast } from './hub.js';
import { downloadsList } from './browserdata.js';
import { CFG } from './config.js';

const NEWTAB = `chrome-extension://${chrome.runtime.id}/newtab.html`;
const DEFAULTS = {
  tasks: [],                      // { id, title, status, started, ended, steps[], summary, thumb, tabId, room?, pages? }
  prefs: { notify: true, askProfile: false },
  profile: null,                  // { user, tenant }
  wallpaper: 'sky',
  apiKey: '',                     // 개발용 — 운영에선 엔진 프록시
};
export const store = { ...DEFAULTS };
export const runtime = { busy: false, currentTaskId: null, tenantName: null, pickProfile: false };
let loaded = null;

export function loadStore() {
  loaded ??= chrome.storage.local.get(Object.keys(DEFAULTS)).then((v) => {
    Object.assign(store, DEFAULTS, v);
    store.prefs = { ...DEFAULTS.prefs, ...(v.prefs || {}) };
    for (const t of store.tasks) if (t.status === 'working') t.status = 'stopped';   // 서비스 워커가 잠들었다 깨면
  });
  return loaded;
}
let saveTimer = null;
export function saveStore() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    store.tasks = store.tasks.slice(0, 12);
    chrome.storage.local.set({ tasks: store.tasks, prefs: store.prefs, profile: store.profile, wallpaper: store.wallpaper, apiKey: store.apiKey });
  }, 200);
}

export const profile = () => ({ user: store.profile?.user || CFG.user, tenant: store.profile?.tenant || CFG.tenant });

// 화면이 쓰는 탭 정보 — Electron tabInfo()와 같은 모양
const isNewtab = (u) => !u || u === NEWTAB || u.startsWith('chrome://newtab') || u.startsWith('chrome://new-tab-page');
export async function tabInfo() {
  const tabs = await chrome.tabs.query({ currentWindow: true }).catch(() => []);
  return tabs.map((t) => ({
    id: t.id, active: t.active, newtab: isNewtab(t.url || t.pendingUrl),
    title: t.title || '새 탭', url: t.url || t.pendingUrl || '', favicon: t.favIconUrl || '',
    loading: t.status === 'loading',
  }));
}

let pushTimer = null;
export function pushState() {
  if (pushTimer) return;
  pushTimer = setTimeout(async () => {
    pushTimer = null;
    await loadStore();
    const p = profile();
    broadcast('state', {
      tabs: await tabInfo(),
      tasks: store.tasks.map(({ _text, ...t }) => t),
      currentTaskId: runtime.currentTaskId,
      busy: runtime.busy,
      user: p.user, tenant: p.tenant, tenantName: runtime.tenantName || p.tenant,
      prefs: store.prefs, hasKey: !!store.apiKey, wallpaper: store.wallpaper,
      pickProfile: runtime.pickProfile,
      panelOpen: true,              // 사이드 패널은 열려 있을 때만 화면이 있다
      bookmarks: [],                 // 즐겨찾기는 Chromium이 맡는다
      downloads: await downloadsList(),
    });
  }, 30);
}

// 탭이 바뀌면 화면에 알린다
for (const ev of ['onActivated', 'onUpdated', 'onRemoved', 'onCreated', 'onMoved']) chrome.tabs[ev].addListener(pushState);
chrome.windows.onFocusChanged.addListener(pushState);
