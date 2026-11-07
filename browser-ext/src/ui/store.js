// 셸의 상태 저장소 — 메인이 보내는 state, 에이전트 이벤트, 팀 채팅을 한곳에 모은다.
// React 밖에서 구독을 시작해야 첫 렌더 전에 온 이벤트도 놓치지 않는다.
//
// 채팅은 전부 "방(room)"이다:
//   { kind: 'ai', id: taskId | null }   — 나와 내 에이전트의 대화(id 없으면 새 대화)
//   { kind: 'channel', id: channelId }  — 팀 채널(사람 + 내가 부른 에이전트)
//   { kind: 'me' }                      — 마이페이지(같은 패널 자리)
import { useSyncExternalStore } from 'react';
import { pageMarkdown } from './format.js';

export const a2a = window.a2a;
const call = (name, ...args) => a2a.invoke('chat:call', name, ...args);

let snap = {
  S: null,              // 메인이 보낸 최신 상태(탭·즐겨찾기·작업…)
  logs: {},             // taskId → { title, items[] }
  custom: { img: null, tone: 'light' },
  mentions: [],         // 입력창 태그 { kind: 'tab'|'task'|'channel'|'person'|'agent'|'msg', id, label }
  room: { kind: 'ai', id: null },
  lastChannel: null,    // 마지막으로 연 채널 id(사람 태그를 보낼 기본 채널)
  me: null,             // { user_id, display_name, email, role, tenant }
  members: {},          // user_id → { display_name, email }
  channels: null,       // 서버 채널 목록(안 읽음 수 포함)
  msgs: {},             // channelId → { list[], hasMore, loading }
  chatStatus: 'connecting',
  drafts: {},           // 방 키 → 그 방에만 보이는 안내(사람 태그로 팀에 보냈다 등)
  pwOffer: null,        // 비밀번호 저장 제안 { id, tabId, origin, username, update }
  pwFill: {},           // tabId → { origin, accounts[{ id, username }] } — 이 페이지에서 채울 수 있는 계정
  pwNote: null,         // 잠깐 보여 줄 결과 한 줄
  draft: null,          // 입력창에 미리 채울 글(오른쪽 클릭 "AI에게 물어보기" 등)
};
const subs = new Set();
export function update(fn) {
  snap = { ...snap, ...fn(snap) };
  subs.forEach((f) => f());
}
export const get = () => snap;
export function useStore(sel) {
  return useSyncExternalStore((cb) => { subs.add(cb); return () => subs.delete(cb); }, () => sel(snap));
}
export const roomKey = (r) => `${r.kind}:${r.id ?? 'new'}`;

// ── 에이전트 이벤트 → 대화 항목(user / bot / steps / page / confirm / note / err) ──
function applyAgent(logs, ev) {
  const prev = logs[ev.taskId] || { title: '', items: [] };
  const L = { ...prev, items: [...prev.items] };
  const items = L.items;
  const last = items[items.length - 1];
  const ts = ev.ts || Date.now();
  switch (ev.type) {
    case 'user':
      if (ev.title && !L.title) L.title = ev.title;
      items.push({ k: 'user', text: ev.text, mentions: ev.mentions || [], ts });
      break;
    case 'text':
      if (last?.k === 'bot') items[items.length - 1] = { ...last, src: last.src + ev.delta };
      else items.push({ k: 'bot', src: ev.delta, ts });
      break;
    case 'tool': {
      const st = { name: ev.name, input: ev.input, state: 'pending' };
      if (last?.k === 'steps') items[items.length - 1] = { ...last, steps: [...last.steps, st] };
      else items.push({ k: 'steps', steps: [st], ts });
      break;
    }
    case 'tool_done':
      for (let i = items.length - 1; i >= 0; i--) {
        const g = items[i];
        if (g.k !== 'steps') continue;
        const j = g.steps.findLastIndex((s) => s.state === 'pending');
        if (j < 0) continue;
        const steps = [...g.steps];
        steps[j] = { ...steps[j], state: ev.ok ? 'ok' : 'bad', summary: ev.summary || '' };
        items[i] = { ...g, steps };
        break;
      }
      break;
    case 'page': items.push({ k: 'page', spec: ev.spec, tabId: ev.tabId, ts }); break;
    case 'confirm': items.push({ k: 'confirm', id: ev.id, text: ev.text, answer: null, ts }); break;
    // 알림의 [승인]/[거절]로 답했을 때도 화면의 승인 카드가 따라 바뀐다
    case 'answered': {
      const i = items.findIndex((it) => it.k === 'confirm' && it.id === ev.id);
      if (i < 0) return logs;
      items[i] = { ...items[i], answer: ev.ok };
      break;
    }
    case 'note': items.push({ k: 'note', text: ev.text, ts }); break;
    case 'error': items.push({ k: 'err', text: ev.message, ts }); break;
    default: return logs;
  }
  return { ...logs, [ev.taskId]: L };
}

a2a.on('state', (S) => update(() => ({ S })));
a2a.on('agent', (ev) => update((s) => {
  // 새 AI 대화를 보내면 그 방이 방금 생긴 작업으로 바뀐다(채널에서 부른 작업은 채널 방에 남는다)
  const follow = ev.type === 'user' && !ev.room && s.room.kind === 'ai';
  return { logs: applyAgent(s.logs, ev), ...(follow ? { room: { kind: 'ai', id: ev.taskId } } : {}) };
}));
a2a.on('wallpaper-image', async (img) => {
  const tone = img ? await toneOf(img) : 'light';
  update(() => ({ custom: { img, tone } }));
});

export function answerConfirm(taskId, id, ok) {
  a2a.send('agent:answer', id, ok);
  update((s) => {
    const L = s.logs[taskId];
    if (!L) return {};
    const items = L.items.map((it) => (it.k === 'confirm' && it.id === id ? { ...it, answer: ok } : it));
    return { logs: { ...s.logs, [taskId]: { ...L, items } } };
  });
}

// ── 방 열기 ─────────────────────────────────────────────────────────
export function openRoom(room) {
  // 방을 옮기면 붙여 둔 태그는 비운다 — 다른 방으로 따라가 엉뚱한 곳에 붙지 않게
  update((s) => ({ room, ...(s.room.kind !== room.kind || s.room.id !== room.id ? { mentions: [] } : {}),
                   ...(room.kind === 'channel' ? { lastChannel: room.id } : {}) }));
  a2a.send('panel:set', true);
  if (room.kind === 'ai' && room.id) a2a.send('task:open', room.id);
  if (room.kind === 'channel') loadChannel(room.id);
}
export const openTask = (id) => openRoom({ kind: 'ai', id });
export function newAiChat() {
  if (snap.S?.busy) return;
  a2a.send('agent:reset');
  openRoom({ kind: 'ai', id: null });
  setTimeout(() => document.getElementById('composer')?.focus(), 60);
}
export function openTeam() {
  const cid = snap.lastChannel ?? snap.channels?.find((c) => c.name === 'general')?.id ?? snap.channels?.[0]?.id;
  if (cid) openRoom({ kind: 'channel', id: cid });
}

// ── 팀 채팅 ─────────────────────────────────────────────────────────
const meOf = (m) => ({ user_id: m.user_id, display_name: m.display_name || m.user_id, email: m.email,
                       role: m.role, tenant: m.tenant?.name || m.tenant?.slug, avatar_hue: m.avatar_hue ?? null });

// 프로필 저장 — 서버(팀원에게 보이는 이름·색). 실패하면 이유를 돌려준다.
async function saveProfile(patch) {
  try {
    const m = await call('updateMe', patch);
    update((s) => ({ me: meOf(m), members: { ...s.members, [m.user_id]: { ...s.members[m.user_id],
      display_name: m.display_name, avatar_hue: m.avatar_hue ?? null } } }));
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e.message || e).replace(/^Error invoking remote method 'chat:call': (Error: )?/, '') };
  }
}
export const saveDisplayName = (name) => saveProfile({ display_name: name });
export const saveAvatarHue = (hue) => saveProfile({ avatar_hue: hue });
export const setPref = (key, value) => a2a.send('prefs:set', key, value);

export async function loadChat() {
  try {
    // 하나가 실패해도 나머지는 살린다(예: /me만 실패해도 채널 목록은 보이게)
    const [me, members, channels] = await Promise.allSettled([call('me'), call('members'), call('channels')]);
    const map = {};
    for (const m of members.status === 'fulfilled' ? members.value : []) {
      map[m.user_id] = { display_name: m.display_name || m.user_id, email: m.email, avatar_hue: m.avatar_hue ?? null };
    }
    update(() => ({ ...(me.status === 'fulfilled' ? { me: meOf(me.value) } : {}), members: map,
                    channels: channels.status === 'fulfilled' ? channels.value : [] }));
  } catch {
    update(() => ({ channels: [] }));
  }
}
let chTimer = null;
function refreshChannels() {
  clearTimeout(chTimer);
  chTimer = setTimeout(async () => {
    const channels = await call('channels').catch(() => null);
    if (channels) update(() => ({ channels }));
  }, 250);
}

const bySeq = (a, b) => a.seq - b.seq;
export async function loadChannel(cid) {
  update((s) => ({ msgs: { ...s.msgs, [cid]: { ...(s.msgs[cid] || { list: [] }), loading: true } } }));
  try {
    const r = await call('messages', cid, { limit: 80 });
    update((s) => ({ msgs: { ...s.msgs, [cid]: { list: r.messages.sort(bySeq), hasMore: r.has_more, loading: false } } }));
    markRead(cid);
  } catch {
    update((s) => ({ msgs: { ...s.msgs, [cid]: { list: [], loading: false, error: true } } }));
  }
}
export async function loadOlder(cid) {
  const cur = snap.msgs[cid];
  if (!cur?.list.length || cur.loading) return;
  const r = await call('messages', cid, { before: cur.list[0].seq, limit: 60 }).catch(() => null);
  if (!r) return;
  update((s) => {
    const seen = new Set(s.msgs[cid].list.map((m) => m.seq));
    const older = r.messages.filter((m) => !seen.has(m.seq));
    return { msgs: { ...s.msgs, [cid]: { ...s.msgs[cid], list: [...older, ...s.msgs[cid].list].sort(bySeq), hasMore: r.has_more } } };
  });
}
async function fetchNew(cid) {
  const cur = snap.msgs[cid];
  if (!cur || cur.loading) return;
  const after = cur.list.length ? cur.list[cur.list.length - 1].seq : 0;
  const r = await call('messages', cid, { after, limit: 100 }).catch(() => null);
  if (!r?.messages?.length) return;
  // 스트림 이벤트와 보낸 직후 새로고침이 동시에 올 수 있다 — 붙이는 순간의 목록으로 중복을 거른다
  let fresh = [];
  update((s) => {
    const seen = new Set(s.msgs[cid].list.map((m) => m.seq));
    fresh = r.messages.filter((m) => !seen.has(m.seq));
    return fresh.length ? { msgs: { ...s.msgs, [cid]: { ...s.msgs[cid], list: [...s.msgs[cid].list, ...fresh].sort(bySeq) } } } : {};
  });
  // 나를 부른 글이면, 창이 뒤에 있을 때 한 줄 알림
  const me = snap.me?.user_id;
  for (const m of fresh) {
    if (me && m.author !== me && new RegExp(`@${me}\\b`, 'i').test(m.body) && !document.hasFocus()) {
      const ch = snap.channels?.find((c) => c.id === cid);
      a2a.send('notify', `#${ch?.name || cid} · ${snap.members[m.author]?.display_name || m.author}`, m.body);
    }
  }
  if (snap.room.kind === 'channel' && snap.room.id === cid && snap.S?.panelOpen) markRead(cid);
}
async function reloadOne(cid, seq) {
  const cur = snap.msgs[cid];
  if (!cur) return;
  const r = await call('messages', cid, { after: seq - 1, limit: 1 }).catch(() => null);
  const m = r?.messages?.find((x) => x.seq === seq);
  if (!m) return;
  update((s) => ({ msgs: { ...s.msgs, [cid]: { ...s.msgs[cid], list: s.msgs[cid].list.map((x) => (x.seq === seq ? m : x)) } } }));
}
function markRead(cid) {
  const list = snap.msgs[cid]?.list;
  if (list?.length) call('read', cid, list[list.length - 1].seq).then(refreshChannels).catch(() => {});
}
export async function react(cid, seq, emoji, on) {
  await call('react', cid, seq, emoji, on).catch(() => {});
  reloadOne(cid, seq);
}

a2a.on('chat:status', (st) => update(() => ({ chatStatus: st })));

// ── 비밀번호 안내 줄 ──
let noteTimer = null;
const flash = (text, tone = 'ok') => {
  clearTimeout(noteTimer);
  update(() => ({ pwNote: { text, tone } }));
  noteTimer = setTimeout(() => update(() => ({ pwNote: null })), 2500);
};
a2a.on('pw:offer', (o) => update(() => ({ pwOffer: o })));
a2a.on('pw:fillable', ({ tabId, origin, accounts }) => update((s) => ({ pwFill: { ...s.pwFill, [tabId]: { origin, accounts } } })));
a2a.on('pw:resolved', (r) => {
  update((s) => (s.pwOffer?.id === r.id ? { pwOffer: null } : {}));
  if (r.action === 'save') flash(r.ok ? '저장했어요 — 이 컴퓨터에 암호화해 두었습니다' : `저장하지 못했어요: ${r.error || '알 수 없는 오류'}`, r.ok ? 'ok' : 'warn');
});
a2a.on('pw:filled', ({ ok }) => flash(ok ? '채웠어요' : '이 페이지에선 채울 수 없어요', ok ? 'ok' : 'warn'));
// 페이지 오른쪽 클릭 → 새 AI 채팅에 선택 글·링크·이 탭을 붙여 둔다(보내기는 사용자가)
a2a.on('ai:quote', ({ text, kind }) => {
  if (snap.S?.busy) return;
  a2a.send('agent:reset');
  const tab = snap.S?.tabs.find((t) => t.active);
  const tabChip = tab && !tab.newtab ? [{ kind: 'tab', id: tab.id, label: tab.title }] : [];
  const draft = kind === 'selection' ? `"${text}"\n\n` : kind === 'link' ? `${text} ` : '';
  update(() => ({ room: { kind: 'ai', id: null }, mentions: kind === 'link' ? [] : tabChip, draft }));
  a2a.send('panel:set', true);
});
// ── 프로필(계정 × 워크스페이스) ── 바뀌면 채팅 상태를 비우고 새로 불러온다
a2a.on('ws:changed', () => {
  update(() => ({ me: null, members: {}, channels: null, msgs: {}, drafts: {}, mentions: [], room: { kind: 'ai', id: null } }));
  loadChat();
});
export const listProfiles = () => a2a.invoke('ws:profiles').catch(() => null);
export const switchProfile = (user, tenant) => a2a.invoke('ws:switch', user, tenant).catch((e) => ({ ok: false, error: String(e) }));
export const showPicker = (open) => a2a.send('ws:picker', open);
export const takeDraft = () => { const d = snap.draft; if (d !== null) update(() => ({ draft: null })); return d; };
export const resolvePw = (id, action) => a2a.send('pw:resolve', id, action);
export const fillPw = (tabId, entryId) => a2a.send('pw:fill', tabId, entryId);
export const hideFill = (tabId) => update((s) => ({ pwFill: { ...s.pwFill, [tabId]: { ...s.pwFill[tabId], accounts: [] } } }));
a2a.on('chat:event', (type, d) => {
  const cid = d?.channel_id;
  if (type === 'ready') {
    update(() => ({ chatStatus: 'live' }));
    // 다시 붙었을 때 끊긴 사이의 글·수정·삭제·반응을 바로 채운다
    for (const c of Object.keys(snap.msgs)) {
      const id = Number(c);
      if (snap.room.kind === 'channel' && snap.room.id === id) loadChannel(id); else fetchNew(id);
    }
    refreshChannels();
    return;
  }
  if (!Number.isInteger(cid)) { refreshChannels(); return; }
  if (type === 'message' && !d.parent_seq) fetchNew(cid);
  if ((type === 'edit' || type === 'delete' || type === 'reaction') && d.seq) reloadOne(cid, d.seq);
  if (type === 'message' && d.parent_seq) reloadOne(cid, d.parent_seq);   // 답글 수
  refreshChannels();
});
// 스트림이 놓친 것을 메우는 30초 안전망(웹앱과 같은 방식)
setInterval(() => { for (const cid of Object.keys(snap.msgs)) fetchNew(Number(cid)); }, 30000);

// ── 태그 ─────────────────────────────────────────────────────────────
export function addMention(m) {
  update((s) => (s.mentions.some((x) => x.kind === m.kind && x.id === m.id)
    ? {} : { mentions: [...s.mentions, m].slice(-10) }));
  setTimeout(() => (document.getElementById('ask') || document.getElementById('composer'))?.focus(), 30);
}
export function removeMention(m) {
  update((s) => ({ mentions: s.mentions.filter((x) => !(x.kind === m.kind && x.id === m.id)) }));
}

// ── 보내기: 방과 태그로 받는 이를 정한다 ────────────────────────────
// 규칙
//  · 채널 방: 기본은 사람에게. @에이전트를 태그하면 내 에이전트가 일하고 답을 이 채널에 올린다.
//    단, 사람도 태그했으면 AI는 답하지 않는다(사람에게 한 말이다).
//  · AI 방: 기본은 에이전트에게. 사람을 태그하면 AI는 답하지 않고, 메시지(+붙인 결과)를 팀 채널로 보낸다.
export function route(room, mentions) {
  const people = mentions.filter((m) => m.kind === 'person');
  const agentTag = mentions.some((m) => m.kind === 'agent');
  if (room.kind === 'channel') {
    if (people.length) return { to: 'people', ai: false, why: agentTag ? 'person-wins' : null };
    return { to: agentTag ? 'agent-in-channel' : 'channel', ai: agentTag };
  }
  if (people.length) return { to: 'people', ai: false };
  return { to: 'agent', ai: true };
}
export function destChannel(mentions) {
  const tagged = mentions.find((m) => m.kind === 'channel');
  const chs = snap.channels || [];
  return chs.find((c) => c.name === tagged?.id) || chs.find((c) => c.id === snap.lastChannel) ||
         chs.find((c) => c.name === 'general') || chs[0] || null;
}

function attachText(ctx) {
  const S = snap.S;
  const out = [];
  for (const m of ctx) {
    if (m.kind === 'task') {
      const t = S.tasks.find((x) => x.id === m.id);
      if (!t) continue;
      const page = t.pages?.[t.pages.length - 1];
      out.push(`📎 ${t.title}\n${(page ? pageMarkdown(page) : t.summary || '').slice(0, 3000)}`);
    }
    if (m.kind === 'tab') {
      const t = S.tabs.find((x) => x.id === m.id);
      if (t && !t.newtab) out.push(`🔗 ${t.title} ${t.url}`);
    }
  }
  return out.length ? `\n\n${out.join('\n\n')}` : '';
}

export async function send(text, { fresh } = {}) {
  text = String(text || '').trim();
  const { room, mentions, S } = snap;
  if (!text || !S) return false;
  const r = route(room, mentions);
  const people = mentions.filter((m) => m.kind === 'person');
  const ctx = mentions.filter((m) => ['tab', 'task', 'channel', 'msg'].includes(m.kind));
  const agentCtx = ctx.map(({ kind, id }) => ({ kind, id }));
  const tags = people.map((p) => `@${p.id}`).join(' ');

  if (r.to === 'agent') {
    if (S.busy) return false;
    update(() => ({ mentions: [] }));
    const cont = !fresh && room.id && room.id === S.currentTaskId;
    a2a.send('agent:run', text, !cont, agentCtx, null);
    return true;
  }

  const cid = room.kind === 'channel' ? room.id : destChannel(mentions)?.id;
  if (!cid) return false;
  if (r.to === 'agent-in-channel' && S.busy) return false;
  const body = `${tags ? tags + ' ' : ''}${r.to === 'agent-in-channel' ? '@에이전트 ' : ''}${text}${attachText(ctx)}`;
  const kept = mentions;
  update(() => ({ mentions: [] }));
  try {
    await call('post', cid, body.slice(0, 8000));
  } catch (e) {
    update(() => ({ mentions: kept }));      // 실패하면 태그도 되돌린다 — 다시 보낼 때 받는 사람이 바뀌지 않게
    return false;
  }
  if (room.kind === 'channel') {
    if (!snap.msgs[cid]) loadChannel(cid); else fetchNew(cid);
  } else {
    // AI 방에 남기는 안내 — AI가 왜 답하지 않는지 보이게
    const ch = snap.channels?.find((c) => c.id === cid);
    const key = roomKey(room);
    const who = people.map((p) => p.label).join(', ');
    update((s) => ({ drafts: { ...s.drafts, [key]: [...(s.drafts[key] || []),
      { text, who, channel: ch?.name || '', cid, ts: Date.now() }] } }));
  }
  if (r.to === 'agent-in-channel') {
    const ch = snap.channels?.find((c) => c.id === cid);
    a2a.send('agent:run', text, true, agentCtx, { cid, name: ch?.name || String(cid) });
  }
  return true;
}

// 새 탭의 "AI에게" — 항상 새 AI 대화
export function runAgentFresh(text) {
  if (snap.S?.busy) return;
  update(() => ({ room: { kind: 'ai', id: null } }));
  return send(text, { fresh: true });
}

// 내 이미지 배경의 밝기로 글자 톤을 정한다
function toneOf(src) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas');
      c.width = c.height = 16;
      const g = c.getContext('2d');
      g.drawImage(img, 0, 0, 16, 16);
      const d = g.getImageData(0, 0, 16, 16).data;
      let sum = 0;
      for (let i = 0; i < d.length; i += 4) sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
      resolve(sum / (d.length / 4) < 128 ? 'dark' : 'light');
    };
    img.onerror = () => resolve('light');
    img.src = src;
  });
}

loadChat();
