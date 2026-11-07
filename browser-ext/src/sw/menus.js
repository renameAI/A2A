// 오른쪽 클릭 "AI에게 …" — Electron contextTemplate의 AI 항목들. 나머지(복사·검색·인쇄·검사)는 Chromium 메뉴 그대로.
// 누르면 사이드 패널을 열고, 지금 탭을 태그로 붙인 새 AI 채팅에 고른 글·링크를 미리 채운다(보내기는 사용자가).
import { on, broadcast, sendTo } from './hub.js';
import { collect } from '../../../browser/events.js';
import { wcOf } from './webcontents.js';

const MENUS = [
  { id: 'a2a-selection', title: 'AI에게 물어보기', contexts: ['selection'] },
  { id: 'a2a-link', title: 'AI에게 이 링크 맡기기', contexts: ['link'] },
  { id: 'a2a-page', title: 'AI에게 이 페이지 맡기기', contexts: ['page'] },
];
function install() {
  chrome.contextMenus.removeAll(() => {
    for (const m of MENUS) chrome.contextMenus.create({ ...m, documentUrlPatterns: ['http://*/*', 'https://*/*', 'file:///*'] });
  });
}
chrome.runtime.onInstalled.addListener(install);
chrome.runtime.onStartup.addListener(install);

// 패널이 막 열리는 중이면 아직 연결 전이다 — 잠깐 들고 있다가 새로 붙는 화면에 건넨다
let pendingQuote = null;
on('ui:hello', (port) => {
  if (pendingQuote && Date.now() - pendingQuote.at < 10000) sendTo(port, 'ai:quote', pendingQuote.q);
  pendingQuote = null;
});

export function onMenu(info, tab) {
  // 사이드 패널은 사용자 동작 안에서만 열린다 — await 전에 바로 부른다
  if (tab?.windowId != null) chrome.sidePanel.open({ windowId: tab.windowId }).catch(() => {});
  let q = null;
  if (info.menuItemId === 'a2a-selection' && info.selectionText?.trim()) {
    q = { text: info.selectionText.trim().slice(0, 2000), kind: 'selection' };
    if (tab?.id != null) collect.selection(wcOf(tab.id, tab), info.selectionText);
  }
  if (info.menuItemId === 'a2a-link' && /^https?:/i.test(info.linkUrl || '')) q = { text: info.linkUrl.slice(0, 2000), kind: 'link' };
  if (info.menuItemId === 'a2a-page') q = { kind: 'page' };
  if (!q) return;
  pendingQuote = { q, at: Date.now() };
  broadcast('ai:quote', q);
}
chrome.contextMenus.onClicked.addListener(onMenu);
globalThis.a2a = { ...(globalThis.a2a || {}), onMenu };
