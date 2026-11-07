// A2A 팀 채팅(/chat/v1) 다리 — 셸의 네이티브 채팅 화면이 쓰는 API 호출과 실시간 스트림.
//
// 인증 헤더는 메인 프로세스만 붙인다(셸은 IPC로 부탁만 한다). 실시간은 SSE: 서버는 "무엇이 바뀌었는지"
// ({type, channel_id, seq})만 보내고 본문은 안 보낸다 — 셸이 받으면 messages?after=로 채운다.
// 서버는 ~280초마다 스트림을 끊으니(reconnect) 지수 백오프로 다시 붙는다.

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
  let stopped = false;
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
      } catch (e) {
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
    start() { stopped = false; stream(); },
    stop() { stopped = true; ctrl?.abort(); },
  };
}

// 이 사용자가 들어갈 수 있는 워크스페이스(테넌트) — 테넌트와 상관없는 조회
async function tenantsFor({ engine, user }) {
  const r = await fetch(`${engine}/chat/v1/me/tenants`, { headers: { 'X-Dev-User': user } });
  if (!r.ok) throw new Error(`워크스페이스 목록 ${r.status}`);
  return (await r.json()).tenants.filter((t) => t.status === 'active');
}

module.exports = { createChat, tenantsFor };
