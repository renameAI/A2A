// A2A 팀 채팅(/chat/v1) — Electron chat-bridge.js + main.js의 chatCall·프로필 바꾸기를 서비스 워커로.
//
// 인증 헤더는 서비스 워커만 붙인다(화면은 chat:call로 부탁만 한다). 실시간은 SSE: 서버는 "무엇이 바뀌었는지"만 보내고
// 화면이 messages?after=로 채운다. MV3 서비스 워커는 오래 못 사니, 화면(사이드 패널·새 탭)이 열려 있는 동안만 스트림을 잡는다 —
// 화면이 25초마다 핑을 보내 워커를 깨워 둔다. 화면이 다 닫히면 1분마다 안 읽음만 확인해 툴바 배지에 띄운다.
import { on, handle, broadcast, sendTo, onPorts, uiCount } from './hub.js';
import { CFG } from './config.js';
import { collect } from '../../../browser/events.js';
import { store, runtime, loadStore, saveStore, pushState, profile } from './state.js';

function createChat({ engine, tenant, user, onEvent, onStatus }) {
  const base = `${engine}/chat/v1/t/${encodeURIComponent(tenant)}`;
  const headers = { 'X-Dev-User': user, 'Content-Type': 'application/json' };

  async function req(p, init = {}) {
    const r = await fetch(`${base}${p}`, { ...init, headers: { ...headers, ...(init.headers || {}) } });
    if (!r.ok) {
      let msg = '';
      try { const j = await r.json(); msg = j.error?.message || j.detail || ''; } catch { /* 본문 없음 */ }
      throw new Error(`채팅 API ${r.status}${msg ? ': ' + String(msg).slice(0, 200) : ''}`);
    }
    return r.json();
  }

  const api = {
    me: () => req('/me'),
    updateMe: (patch) => req('/me', { method: 'PATCH', body: JSON.stringify(patch) }),
    members: async () => (await req('/members')).members,
    channels: async () => (await req('/channels')).channels,
    messages: (cid, { after, before, thread, limit = 60 } = {}) => {
      const q = new URLSearchParams({ limit: String(Math.min(200, limit)) });
      if (after != null) q.set('after', String(after));
      if (before != null) q.set('before', String(before));
      if (thread != null) q.set('thread', String(thread));
      return req(`/channels/${cid}/messages?${q}`);
    },
    post: (cid, body, { parentSeq, kind = 'text' } = {}) => req(`/channels/${cid}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        body, kind, client_msg_id: `a2ab-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        ...(parentSeq ? { parent_seq: parentSeq } : {}),
      }),
    }),
    react: (cid, seq, emoji, on) => req(`/channels/${cid}/messages/${seq}/reactions`, {
      method: 'POST', body: JSON.stringify({ emoji, on }),
    }),
    read: (cid, seq) => req(`/channels/${cid}/read`, { method: 'POST', body: JSON.stringify({ seq }) }),
  };

  // ── SSE ──
  let stopped = true;
  let backoff = 1000;
  let ctrl = null;
  async function stream() {
    while (!stopped) {
      ctrl = new AbortController();
      const mine = ctrl;                           // 이 연결의 것 — 타이머가 다음 연결을 끊지 않게
      let idle = null;
      try {
        const r = await fetch(`${base}/stream`, { headers, signal: ctrl.signal });
        if (!r.ok || !r.body) throw new Error(`stream ${r.status}`);
        onStatus('live');
        backoff = 1000;
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        const arm = () => { clearTimeout(idle); idle = setTimeout(() => mine.abort(), 45000); };
        arm();
        for (;;) {
          const { value, done } = await reader.read();
          arm();                                   // 서버 keepalive가 15초마다 온다 — 45초 조용하면 반쯤 끊긴 것
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const frame = buf.slice(0, i);
            buf = buf.slice(i + 2);
            let type = 'message';
            const data = [];
            for (const line of frame.split('\n')) {
              if (line.startsWith('event:')) type = line.slice(6).trim();
              else if (line.startsWith('data:')) data.push(line.slice(5).trim());
            }
            if (!data.length) continue;            // keepalive 주석
            try { onEvent(type, JSON.parse(data.join('\n'))); } catch { /* 깨진 프레임은 버린다 */ }
          }
        }
      } catch {
        if (stopped) return;
        onStatus('offline');
      }
      clearTimeout(idle);
      if (stopped) return;
      await new Promise((r) => setTimeout(r, backoff * (0.7 + Math.random() * 0.6)));
      backoff = Math.min(30000, backoff * 2);
    }
  }

  return {
    api,
    get running() { return !stopped; },
    start() { if (!stopped) return; stopped = false; stream(); },
    stop() { stopped = true; ctrl?.abort(); },
  };
}

// 이 사용자가 들어갈 수 있는 워크스페이스(테넌트) — 테넌트와 상관없는 조회
async function tenantsFor({ engine, user }) {
  const r = await fetch(`${engine}/chat/v1/me/tenants`, { headers: { 'X-Dev-User': user } });
  if (!r.ok) throw new Error(`워크스페이스 목록 ${r.status}`);
  return (await r.json()).tenants.filter((t) => t.status === 'active');
}

// ── 지금 프로필의 연결 ── 서비스 워커가 깨어날 때마다 저장된 프로필로 다시 만든다
let status = 'connecting';
let chat = null;
function makeChat() {
  const p = profile();
  return createChat({
    engine: CFG.engine, tenant: p.tenant, user: p.user,
    onEvent: (type, data) => broadcast('chat:event', type, data),
    onStatus: (st) => { status = st; broadcast('chat:status', st); },
  });
}
export async function chatApi() {
  await loadStore();
  chat ??= makeChat();
  return chat.api;
}
// 화면이 열려 있는 동안만 실시간 연결
onPorts(async (n) => {
  await loadStore();
  chat ??= makeChat();
  if (n > 0) chat.start();
  else { chat.stop(); status = 'connecting'; }
});
on('ui:hello', (port) => sendTo(port, 'chat:status', status));

// 워크스페이스 이름 — 상태 줄의 워크스페이스 이름 자리
async function tenantName() {
  const p = profile();
  if (runtime.tenantName && runtime.tenantFor === `${p.user}/${p.tenant}`) return;
  try {
    const t = (await tenantsFor({ engine: CFG.engine, user: p.user })).find((x) => x.slug === p.tenant);
    runtime.tenantName = t?.name || p.tenant;
    runtime.tenantFor = `${p.user}/${p.tenant}`;
    pushState();
  } catch { /* 엔진이 꺼져 있으면 슬러그를 그대로 보인다 */ }
}
loadStore().then(tenantName);

// 화면이 쓰는 팀 채팅 API — 인자 모양을 여기서 확인한다(Electron chatCall과 같다)
const cid = (v) => (Number.isInteger(v) && v > 0 ? v : null);
const chatCall = {
  me: (a) => a.me(),
  // 프로필 — 표시 이름(1~40자)·아바타 색(0~359 또는 null=기본)만 보낸다
  updateMe: (a, p = {}) => {
    const out = {};
    if ('display_name' in p) {
      if (typeof p.display_name !== 'string' || !p.display_name.trim() || p.display_name.trim().length > 40) return false;
      out.display_name = p.display_name;
    }
    if ('avatar_hue' in p) {
      if (!(p.avatar_hue === null || (Number.isInteger(p.avatar_hue) && p.avatar_hue >= 0 && p.avatar_hue < 360))) return false;
      out.avatar_hue = p.avatar_hue;
    }
    return Object.keys(out).length > 0 && a.updateMe(out);
  },
  members: (a) => a.members(),
  channels: (a) => a.channels().then(collect.chatChannels),
  messages: (a, c, o = {}) => cid(c) && a.messages(c, {
    after: Number.isInteger(o.after) ? o.after : undefined,
    before: Number.isInteger(o.before) ? o.before : undefined,
    thread: Number.isInteger(o.thread) ? o.thread : undefined,
    limit: Number.isInteger(o.limit) ? o.limit : 60,
  }).then((r) => collect.chatMessages(c, r)),
  post: (a, c, body, o = {}) => cid(c) && typeof body === 'string' && body.trim() && body.length <= 8000 &&
    a.post(c, body, { parentSeq: Number.isInteger(o.parentSeq) ? o.parentSeq : undefined })
      .then((m) => { collect.chatMessage(c, { ...m, body: m?.body ?? body }, { direction: 'sent' }); return m; }),
  react: (a, c, seq, emoji, onOff) => cid(c) && Number.isInteger(seq) && typeof emoji === 'string' && emoji.length <= 32 &&
    a.react(c, seq, emoji, !!onOff),
  read: (a, c, seq) => cid(c) && Number.isInteger(seq) && a.read(c, seq).then((r) => { badge(); return r; }),
};
handle('chat:call', async (name, ...args) => {
  args = args.filter((x) => !(x && typeof x.postMessage === 'function'));   // 허브가 붙인 포트는 빼고
  if (typeof name !== 'string' || !Object.hasOwn(chatCall, name)) throw new Error('blocked');
  const r = await chatCall[name](await chatApi(), ...args);
  if (r === false || r === null) throw new Error('잘못된 요청');
  return r;
});

// 에이전트가 채널에 쓸 때(M4) — 에이전트 글은 🤖로 시작해 사람 글과 구분한다(서버엔 봇 계정이 없다)
export const agentChat = {
  channels: async () => (await chatApi()).channels().then(collect.chatChannels),
  post: async (c, text, opts) => {
    const m = await (await chatApi()).post(c, `🤖 ${text}`, opts);
    collect.chatMessage(c, { ...m, body: m?.body ?? `🤖 ${text}` }, { direction: 'sent', byAgent: true, taskId: opts?.taskId });
    return m;
  },
  messages: async (c, o) => (await chatApi()).messages(c, o),
};

// ── 계정 × 워크스페이스 ──
async function listProfiles() {
  const out = await Promise.all(CFG.devUsers.map(async (user) => {
    try { return { user, tenants: await tenantsFor({ engine: CFG.engine, user }) }; }
    catch { return { user, tenants: [] }; }
  }));
  return out.filter((x) => x.tenants.length);
}
handle('ws:profiles', async () => ({ current: profile(), profiles: await listProfiles() }));
handle('ws:switch', async (user, tenant) => {
  user = String(user); tenant = String(tenant);
  if (!CFG.devUsers.includes(user)) return { ok: false, error: '알 수 없는 계정' };
  if (runtime.busy) return { ok: false, error: '에이전트가 일하는 중이라 지금은 바꿀 수 없어요' };
  let tenants;
  try { tenants = await tenantsFor({ engine: CFG.engine, user }); }
  catch (e) { return { ok: false, error: `엔진에 연결하지 못했어요: ${e.message}` }; }
  const t = tenants.find((x) => x.slug === tenant);
  if (!t) return { ok: false, error: '이 계정은 그 워크스페이스에 들어갈 수 없어요' };
  await loadStore();
  chat?.stop();
  store.profile = { user, tenant: t.slug };
  runtime.tenantName = t.name;
  runtime.tenantFor = `${user}/${t.slug}`;
  runtime.currentTaskId = null;
  runtime.pickProfile = false;
  saveStore();
  chat = makeChat();
  if (uiCount() > 0) chat.start();
  pushState();
  broadcast('ws:changed', { user, tenant: t.slug, tenantName: t.name });
  badge();
  return { ok: true };
});
on('ws:picker', (open) => { runtime.pickProfile = !!open; pushState(); });

// ── 알림 · 안 읽음 배지 ──
on('notify', async (title, body) => {
  await loadStore();
  if (!store.prefs.notify) return;
  chrome.notifications.create({
    type: 'basic', iconUrl: 'icons/icon-128.png',
    title: String(title).slice(0, 120), message: String(body).slice(0, 300),
  }).catch(() => {});
});
// 툴바 배지 — 나를 부른 글이 있으면 "@n", 아니면 안 읽은 채널 수
export async function badge() {
  try {
    const list = await (await chatApi()).channels();
    const mentions = list.reduce((n, c) => n + (c.muted ? 0 : c.mentions || 0), 0);
    const unread = list.filter((c) => !c.muted && c.unread).length;
    await chrome.action.setBadgeBackgroundColor({ color: mentions ? '#e5484d' : '#8b8d98' });
    await chrome.action.setBadgeText({ text: mentions ? `@${mentions}` : unread ? String(unread) : '' });
  } catch {
    chrome.action.setBadgeText({ text: '' }).catch(() => {});
  }
}
// 시험·디버깅용(서비스 워커 콘솔에서 a2a.badge())
globalThis.a2a = { ...(globalThis.a2a || {}), badge };
chrome.alarms.create('chat-unread', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'chat-unread') badge(); });
loadStore().then(badge);
