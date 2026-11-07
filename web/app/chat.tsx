"use client";
/* 메시징 플랫폼 클라이언트 — /api/chat/v1 (엔진 app/chat/).
 *
 * 원칙 (서버와 같다):
 * - 순서는 seq가 정한다. 시계(created_at)는 표시용이다.
 * - 실시간 이벤트는 '무엇이 바뀌었나'({type, seq})만 온다. 본문은 조회로 가져온다 —
 *   이벤트가 유실·중복·역순이어도 after= 공백 보충으로 결국 DB와 같아진다.
 * - 이벤트를 놓쳤을 때를 위한 안전망: 30초마다 'tick'을 흘려 열린 채널이 공백을
 *   스스로 확인한다(서버 문서 §13 — 클라이언트 주기적 last_seq 확인).
 * - 게시는 client_msg_id를 붙인다. 실패 후 재시도해도 서버가 같은 seq를 돌려줘
 *   두 번 올라가지 않는다.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { authHeaders, supabase } from "./supabase";

export type Tenant = { slug: string; name: string; plan: string; status: string };
export type Ch = { id: number; name: string; topic: string; visibility: "public" | "private";
  kind: "channel" | "dm" | "announcements"; last_seq: number; last_top_seq: number;
  joined: boolean; last_read_seq: number; channel_role: string | null;
  muted: boolean; mentions: number; unread: boolean; last_message_at: string | null };
export type Reaction = { emoji: string; count: number; mine: boolean };
export type Msg = { channel_id: number; seq: number; parent_seq: number | null; author: string;
  body: string; kind: "text" | "activity" | "system"; created_at: string; edited_at: string | null;
  deleted: boolean; reply_count: number; last_reply_at: string | null; reactions: Reaction[];
  archived?: boolean;
  // 화면 전용 — 서버 확인 전 낙관적 표시
  pending?: boolean; failed?: string; client_msg_id?: string };
export type Member = { user_id: string; email: string; display_name: string; role: string };
export type Me = { tenant: { slug: string; name: string; plan: string }; user_id: string;
  email: string; display_name: string; role: string; attrs: Record<string, unknown>;
  can: Record<string, boolean> };
type Ev = { type: string; seq?: number; channel_id?: number; parent_seq?: number | null;
  tenant?: string };

export class ChatError extends Error {
  constructor(msg: string, public status: number, public code?: string) { super(msg); }
}

export async function capi(path: string, body?: unknown,
                           method: "GET" | "POST" | "PATCH" | "PUT" | "DELETE" = "GET") {
  const m = method !== "GET" ? method : body !== undefined ? "POST" : "GET";
  const r = await fetch(`/api/chat/v1${path}`, {
    method: m, headers: await authHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    throw new ChatError(j?.error?.message ?? (typeof j?.detail === "string" ? j.detail : null)
      ?? `요청 실패 (${r.status})`, r.status, j?.error?.code);
  }
  return j;
}

const tid = () => Array.from(crypto.getRandomValues(new Uint8Array(12)),
  (b) => b.toString(16).padStart(2, "0")).join("");

// ── 실시간 버스 ─────────────────────────────────────────────────────
const listeners = new Set<(e: Ev) => void>();
export function onChatEvent(fn: (e: Ev) => void) {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
function emit(e: Ev) { listeners.forEach((fn) => { try { fn(e); } catch { /* 구독자 오류 격리 */ } }); }

export type RtStatus = "off" | "connecting" | "live" | "retrying";

/** 테넌트 하나의 실시간 연결 — 볼 수 있는 모든 채널을 한 연결로 받는다.
 *  기본은 SSE(fetch 스트림 — EventSource는 인증 헤더를 못 붙인다). 운영에서
 *  NEXT_PUBLIC_CHAT_REALTIME=supabase면 Supabase Realtime 비공개 채널을 쓴다
 *  (서버가 판정한 토픽만 담긴 300초 토큰). */
export function useRealtime(slug: string | null): RtStatus {
  const [st, setSt] = useState<RtStatus>("off");
  useEffect(() => {
    if (!slug) { setSt("off"); return; }
    let alive = true;
    const ac = new AbortController();
    const tick = setInterval(() => emit({ type: "tick" }), 30_000);
    const mode = process.env.NEXT_PUBLIC_CHAT_REALTIME === "supabase" && supabase
      ? "supabase" : "sse";

    async function sse() {
      let delay = 1000;
      while (alive) {
        setSt("connecting");
        try {
          const r = await fetch(`/api/chat/v1/t/${slug}/stream`,
            { headers: await authHeaders(), signal: ac.signal });
          if (!r.ok || !r.body) throw new Error(`stream ${r.status}`);
          const reader = r.body.getReader();
          const dec = new TextDecoder();
          let buf = "", ev = "message";
          while (alive) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            let i;
            while ((i = buf.indexOf("\n")) >= 0) {
              const line = buf.slice(0, i); buf = buf.slice(i + 1);
              if (line.startsWith("event: ")) ev = line.slice(7);
              else if (line.startsWith("data: ")) {
                const d = JSON.parse(line.slice(6));
                if (ev === "ready") { setSt("live"); delay = 1000; emit({ type: "tick" }); }
                else if (ev !== "reconnect") emit({ ...d, type: ev });
              } else if (line === "") ev = "message";
            }
          }
        } catch (e) {
          if ((e as Error).name === "AbortError") return;
        }
        if (!alive) return;
        // 지터를 둔 지수 백오프 — 장애 뒤 모든 클라이언트가 같은 순간에 다시 붙으면
        // 그 자체가 연결 폭주다(실측: Realtime이 초당 연결 한도로 429를 돌려줬다)
        setSt("retrying");
        await new Promise((r) => setTimeout(r, delay * (0.5 + Math.random())));
        delay = Math.min(delay * 2, 30_000);
      }
    }

    async function supa() {
      const sb = supabase!;
      let chans: ReturnType<typeof sb.channel>[] = [];
      while (alive) {
        try {
          setSt("connecting");
          const tok = await capi(`/t/${slug}/realtime-token`);
          await sb.realtime.setAuth(tok.token);
          chans.forEach((c) => void sb.removeChannel(c));
          chans = (tok.topics as string[]).map((topic) => sb.channel(topic,
            { config: { private: true } })
            .on("broadcast", { event: "*" }, ({ event, payload }) =>
              emit({ ...(payload as Ev), type: String(event) }))
            .subscribe());
          setSt("live");
          emit({ type: "tick" });
          // 만료 1분 전에 다시 받는다 — 그때 가시성이 다시 판정된다(권한 회수 반영)
          const wait = Math.max(30, tok.expires_at - Date.now() / 1000 - 60) * 1000;
          await new Promise((r) => setTimeout(r, wait));
        } catch {
          setSt("retrying");
          await new Promise((r) => setTimeout(r, 5000 * (0.5 + Math.random())));
        }
      }
      chans.forEach((c) => void sb.removeChannel(c));
    }

    void (mode === "supabase" ? supa() : sse());
    return () => { alive = false; ac.abort(); clearInterval(tick); };
  }, [slug]);
  return st;
}

// ── 워크스페이스(테넌트) ─────────────────────────────────────────────

export function useWorkspace() {
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [slug, setSlugState] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [channels, setChannels] = useState<Ch[]>([]);
  const [error, setError] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    capi("/me/tenants").then((r) => {
      const ts: Tenant[] = r.tenants ?? [];
      setTenants(ts);
      let want: string | null = null;
      try { want = localStorage.getItem("a2a:tenant"); } catch { /* 사생활 모드 */ }
      setSlugState(ts.find((t) => t.slug === want)?.slug ?? ts[0]?.slug ?? null);
    }).catch((e) => { setTenants([]); setError((e as Error).message); });
  }, []);

  const setSlug = useCallback((s: string) => {
    try { localStorage.setItem("a2a:tenant", s); } catch { /* 무시 */ }
    setSlugState(s);
  }, []);

  const refresh = useCallback(async () => {
    if (!slug) return;
    try {
      const r = await capi(`/t/${slug}/channels`);
      setChannels(r.channels ?? []);
    } catch (e) { setError((e as Error).message); }
  }, [slug]);

  useEffect(() => {
    setMe(null); setMembers([]); setChannels([]);
    if (!slug) return;
    capi(`/t/${slug}/me`).then(setMe).catch((e) => setError((e as Error).message));
    capi(`/t/${slug}/members`).then((r) => setMembers(r.members ?? [])).catch(() => {});
    void refresh();
  }, [slug, refresh]);

  // 이벤트가 오면 채널 목록(안 읽음·멘션)을 다시 받되, 몰려오면 한 번으로 합친다
  useEffect(() => onChatEvent((e) => {
    if (e.type === "tick" || e.type === "message" || e.type === "membership") {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void refresh(), e.type === "tick" ? 0 : 800);
    }
  }), [refresh]);

  const markRead = useCallback(async (cid: number, seq: number) => {
    if (!slug) return;
    setChannels((cs) => cs.map((c) => c.id === cid
      ? { ...c, last_read_seq: Math.max(c.last_read_seq, seq), mentions: 0,
          unread: c.last_top_seq > Math.max(c.last_read_seq, seq) } : c));
    try { await capi(`/t/${slug}/channels/${cid}/read`, { seq }); } catch { /* 다음 tick이 바로잡는다 */ }
  }, [slug]);

  /** 이름으로 채널을 찾고 없으면 만든다(공개). 동시에 둘이 만들어도 409를 받아 다시 찾는다. */
  const ensureChannel = useCallback(async (name: string, topic: string): Promise<Ch | null> => {
    if (!slug) return null;
    const found = channels.find((c) => c.name === name);
    if (found) return found;
    try { await capi(`/t/${slug}/channels`, { name, topic }); }
    catch (e) { if ((e as ChatError).status !== 409) throw e; }
    const r = await capi(`/t/${slug}/channels`);
    setChannels(r.channels ?? []);
    return (r.channels as Ch[]).find((c) => c.name === name) ?? null;
  }, [slug, channels]);

  const reloadMembers = useCallback(async () => {
    if (!slug) return;
    const r = await capi(`/t/${slug}/members`);
    setMembers(r.members ?? []);
  }, [slug]);

  return { tenants, slug, setSlug, me, members, channels, refresh, markRead,
           ensureChannel, reloadMembers, error };
}

// ── 채널 피드 ───────────────────────────────────────────────────────

export function useFeed(slug: string | null, cid: number | null, thread: number | null = null) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [root, setRoot] = useState<Msg | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const live = useRef({ slug, cid, thread });
  live.current = { slug, cid, thread };

  const q = useCallback((extra: Record<string, string | number>) => {
    const p = new URLSearchParams(Object.entries(extra).map(([k, v]) => [k, String(v)]));
    if (thread !== null) p.set("thread", String(thread));
    return `/t/${slug}/channels/${cid}/messages?${p}`;
  }, [slug, cid, thread]);

  const merge = useCallback((incoming: Msg[]) => {
    setMsgs((cur) => {
      const by = new Map<number, Msg>();
      for (const m of cur) if (!m.pending && !m.failed) by.set(m.seq, m);
      for (const m of incoming) by.set(m.seq, m);
      // 서버가 확인한 메시지와 같은 client_msg_id의 낙관적 표시는 걷어낸다
      const done = new Set(incoming.map((m) => m.client_msg_id).filter(Boolean));
      const local = cur.filter((m) => (m.pending || m.failed) && !done.has(m.client_msg_id));
      return [...[...by.values()].sort((a, b) => a.seq - b.seq), ...local];
    });
  }, []);

  useEffect(() => {
    setMsgs([]); setRoot(null); setHasMore(false); setErr("");
    if (!slug || cid === null) return;
    let alive = true;
    setLoading(true);
    capi(q({ limit: 50 })).then((r) => {
      if (!alive) return;
      setMsgs(r.messages ?? []); setHasMore(!!r.has_more); setRoot(r.root ?? null);
    }).catch((e) => alive && setErr((e as Error).message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [slug, cid, thread, q]);

  // 최신 목록은 ref로 읽는다 — setState 갱신 함수로 상태를 '읽으면' 엄격 모드에서
  // 두 번 실행될 수 있고, 읽기에 쓰는 것 자체가 갱신 예약이다.
  const msgsRef = useRef(msgs);
  msgsRef.current = msgs;
  const maxSeq = () => msgsRef.current.reduce(
    (m, x) => (x.pending || x.failed ? m : Math.max(m, x.seq)), 0);

  const gapFill = useCallback(async () => {
    if (!live.current.slug || live.current.cid === null) return;
    const after = maxSeq();
    try {
      const r = await capi(q({ after, limit: 200 }));
      if (r.messages?.length) merge(r.messages);
      if (r.root) setRoot(r.root);
    } catch { /* 다음 이벤트·tick이 다시 시도한다 */ }
  }, [q, merge]);

  const refetchOne = useCallback(async (seq: number) => {
    try {
      const r = await capi(q({ after: seq - 1, limit: 1 }));
      const m: Msg | undefined = r.messages?.[0];
      if (m && m.seq === seq) merge([m]);
      if (r.root) setRoot(r.root);
    } catch { /* 무시 */ }
  }, [q, merge]);

  useEffect(() => onChatEvent((e) => {
    const { cid: c, thread: th } = live.current;
    if (e.type === "tick") { void gapFill(); return; }
    if (e.channel_id !== c) return;
    if (e.type === "message") {
      if (th === null && e.parent_seq) void refetchOne(e.parent_seq);   // 답글 수 갱신
      else if (th === null || e.parent_seq === th) void gapFill();
    } else if (e.seq) void refetchOne(e.seq);                            // 수정·삭제·리액션
  }), [gapFill, refetchOne]);

  const loadOlder = useCallback(async () => {
    const first = msgs.find((m) => !m.pending && !m.failed);
    if (!first) return;
    const r = await capi(q({ before: first.seq, limit: 50 }));
    merge(r.messages ?? []);
    setHasMore(!!r.has_more);
  }, [msgs, q, merge]);

  const send = useCallback(async (body: string, opts: { clientId?: string; kind?: string } = {}) => {
    if (!slug || cid === null) return null;
    const client_msg_id = opts.clientId ?? tid();
    const temp: Msg = { channel_id: cid, seq: Number.MAX_SAFE_INTEGER, parent_seq: thread,
      author: "", body, kind: (opts.kind as Msg["kind"]) ?? "text",
      created_at: new Date().toISOString(), edited_at: null, deleted: false, reply_count: 0,
      last_reply_at: null, reactions: [], pending: true, client_msg_id };
    if (!opts.clientId) setMsgs((cur) => [...cur, temp]);
    else setMsgs((cur) => cur.map((m) => m.client_msg_id === client_msg_id
      ? { ...m, pending: true, failed: undefined } : m));
    try {
      const r = await capi(`/t/${slug}/channels/${cid}/messages`,
        { body, parent_seq: thread, client_msg_id, kind: opts.kind ?? "text" });
      merge([{ ...temp, ...r, pending: false, deleted: false, reply_count: 0,
               reactions: [], edited_at: null, last_reply_at: null }]);
      return r as { seq: number };
    } catch (e) {
      setMsgs((cur) => cur.map((m) => m.client_msg_id === client_msg_id
        ? { ...m, pending: false, failed: (e as Error).message } : m));
      return null;
    }
  }, [slug, cid, thread, merge]);

  const retry = useCallback((m: Msg) => send(m.body, { clientId: m.client_msg_id }), [send]);
  const edit = useCallback(async (seq: number, body: string) => {
    await capi(`/t/${slug}/channels/${cid}/messages/${seq}`, { body }, "PATCH");
    await refetchOne(seq);
  }, [slug, cid, refetchOne]);
  const remove = useCallback(async (seq: number) => {
    await capi(`/t/${slug}/channels/${cid}/messages/${seq}`, undefined, "DELETE");
    await refetchOne(seq);
  }, [slug, cid, refetchOne]);
  const react = useCallback(async (seq: number, emoji: string, on: boolean) => {
    await capi(`/t/${slug}/channels/${cid}/messages/${seq}/reactions`, { emoji, on });
    await refetchOne(seq);
  }, [slug, cid, refetchOne]);

  return { msgs, root, hasMore, loading, err, loadOlder, send, retry, edit, remove, react };
}

// ── 표시 ────────────────────────────────────────────────────────────

const AVA = ["#3B6FD8", "#2E9C6A", "#B5568F", "#C77A1E", "#6B5BD2", "#2A8C9C"];
export function avaColor(seed: string): string {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVA[h % AVA.length];
}
export function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
}
export const nameOf = (members: Member[], uid: string) =>
  uid === "platform" ? "플랫폼 공지"
    : members.find((m) => m.user_id === uid)?.display_name || uid;

function Rich({ text, me }: { text: string; me: Me | null }) {
  const mine = new Set([me?.user_id, me?.display_name, me?.email, me?.email?.split("@")[0]]
    .filter(Boolean).map((x) => String(x).toLowerCase()));
  return <>{text.split(/(@[A-Za-z0-9._%+\-]+(?:@[A-Za-z0-9.\-]+)?)/g).map((p, i) =>
    p.startsWith("@")
      ? <span key={i} className={`mention ${mine.has(p.slice(1).toLowerCase()) ? "me" : ""}`}>{p}</span>
      : <span key={i}>{p}</span>)}</>;
}

const QUICK = ["👍", "✅", "👀", "🙏"];

export function MessageRow({ m, prev, me, members, onReply, onEdit, onDelete, onReact, onRetry }: {
  m: Msg; prev?: Msg | null; me: Me | null; members: Member[];
  onReply?: () => void; onEdit?: (body: string) => void; onDelete?: () => void;
  onReact?: (emoji: string, on: boolean) => void; onRetry?: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const author = m.pending || m.failed ? (me?.user_id ?? "") : m.author;
  const mine = author === me?.user_id;
  const cont = !!prev && prev.author === m.author && prev.kind === m.kind
    && !m.pending && Math.abs(Date.parse(m.created_at) - Date.parse(prev.created_at)) < 300_000;
  const name = nameOf(members, author);
  const esc = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const mentioned = !mine && !m.deleted && !!me
    && new RegExp(`@(${[me.user_id, me.display_name, me.email.split("@")[0]].filter(Boolean)
      .map(esc).join("|")})(?![A-Za-z0-9._-])`, "i").test(m.body);
  if (m.kind === "activity" || m.kind === "system") {
    return (
      <div className={`trow act ${m.kind === "system" ? "sys" : ""}`}>
        <span className="act-dot" />
        <span className="act-txt"><b>{name}</b> {m.body}</span>
        <span className="t">{hhmm(m.created_at)}</span>
        {onReply && m.reply_count > 0 && (
          <button className="reply-link" onClick={onReply}>답글 {m.reply_count}개</button>)}
      </div>);
  }
  const canModerate = me?.role === "owner" || me?.role === "admin";
  return (
    <div className={`trow ${cont ? "cont" : ""} ${mentioned ? "has-mention" : ""} ${
      m.pending ? "pending" : ""} ${m.failed ? "failed" : ""}`}>
      {cont ? <span className="t gutter">{hhmm(m.created_at)}</span>
        : <div className="ava" style={{ background: avaColor(author) }}>{name[0]?.toUpperCase()}</div>}
      <div className="tbody">
        {!cont && <div className="who">{name}<span className="t">{hhmm(m.created_at)}</span>
          {m.archived && <span className="arch" title="12개월 지난 메시지 — 아카이브에서 읽음">보관</span>}</div>}
        {editing !== null ? (
          <div className="edit-box">
            <textarea value={editing} autoFocus onChange={(e) => setEditing(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault(); if (editing.trim()) { onEdit?.(editing.trim()); setEditing(null); }
                }
                if (e.key === "Escape") setEditing(null);
              }} />
            <div className="edit-hint">Enter 저장 · Esc 취소</div>
          </div>
        ) : (
          <div className="ttext">
            {m.deleted ? <i className="gone">삭제된 메시지</i> : <Rich text={m.body} me={me} />}
            {m.edited_at && !m.deleted && <span className="edited"> (수정됨)</span>}
          </div>)}
        {m.failed && (
          <div className="send-fail">보내지 못했어요 — {m.failed}
            <button onClick={onRetry}>다시 보내기</button></div>)}
        {m.pending && <div className="sending">보내는 중…</div>}
        {(m.reactions ?? []).length > 0 && (
          <div className="rx">
            {(m.reactions ?? []).map((r) => (
              <button key={r.emoji} className={`rx-chip ${r.mine ? "on" : ""}`}
                onClick={() => onReact?.(r.emoji, !r.mine)}>{r.emoji} {r.count}</button>))}
          </div>)}
        {onReply && m.reply_count > 0 && (
          <button className="reply-link" onClick={onReply}>
            답글 {m.reply_count}개{m.last_reply_at && <span className="t">마지막 {hhmm(m.last_reply_at)}</span>}
          </button>)}
      </div>
      {!m.deleted && !m.pending && !m.failed && !m.archived && (
        <div className="hover-bar">
          {onReact && QUICK.map((e) => (
            <button key={e} title={`${e} 반응`} onClick={() => onReact(e, true)}>{e}</button>))}
          {onReply && <button title="스레드에서 답글" onClick={onReply}>💬</button>}
          {mine && onEdit && <button title="수정" onClick={() => setEditing(m.body)}>✏️</button>}
          {(mine || canModerate) && onDelete && (
            <button title="삭제" onClick={() => {
              if (confirm(mine ? "이 메시지를 삭제할까요?"
                : "다른 사람의 메시지를 삭제합니다. 감사 기록에 남아요. 계속할까요?")) onDelete();
            }}>🗑</button>)}
        </div>)}
    </div>);
}

export function ThreadPane({ slug, cid, rootSeq, title, me, members, onClose }: {
  slug: string; cid: number; rootSeq: number; title: string; me: Me | null;
  members: Member[]; onClose: () => void;
}) {
  const f = useFeed(slug, cid, rootSeq);
  const [v, setV] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [f.msgs.length]);
  async function go() {
    const t = v.trim();
    if (!t) return;
    setV("");
    await f.send(t);
  }
  return (
    <aside className="thread-pane" aria-label="스레드">
      <header className="tp-head">
        <div><b>스레드</b><span className="tp-sub">{title}</span></div>
        <button className="icon-btn" aria-label="스레드 닫기" onClick={onClose}>✕</button>
      </header>
      <div className="tp-body">
        {f.root && <div className="tp-root">
          <MessageRow m={f.root} me={me} members={members}
            onReact={(e, on) => void capi(`/t/${slug}/channels/${cid}/messages/${f.root!.seq}/reactions`,
              { emoji: e, on })} /></div>}
        {f.err && <div className="tp-err">{f.err}</div>}
        <div className="tp-count">{f.msgs.length ? `답글 ${f.msgs.length}개` : "아직 답글이 없어요"}</div>
        {f.msgs.map((m, i) => (
          <MessageRow key={m.client_msg_id ?? m.seq} m={m} prev={f.msgs[i - 1]} me={me}
            members={members} onEdit={(b) => void f.edit(m.seq, b)}
            onDelete={() => void f.remove(m.seq)} onReact={(e, on) => void f.react(m.seq, e, on)}
            onRetry={() => void f.retry(m)} />))}
        <div ref={end} />
      </div>
      <div className="tp-comp">
        <div className="comp-box">
          <textarea rows={2} value={v} placeholder="답글 남기기… (@이름으로 팀원 호출)"
            onChange={(e) => setV(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault(); void go();
              }
            }} />
          <div className="comp-bar">
            <button className="send" disabled={!v.trim()} onClick={() => void go()} title="보내기">➤</button>
          </div>
        </div>
      </div>
    </aside>);
}

export function NewChannelModal({ slug, me, onClose, onCreated }: {
  slug: string; me: Me | null; onClose: () => void; onCreated: (c: { id: number }) => void;
}) {
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [priv, setPriv] = useState(false);
  const [err, setErr] = useState("");
  const canPriv = !!me?.can["channel.create_private"];
  async function go() {
    setErr("");
    try {
      const c = await capi(`/t/${slug}/channels`,
        { name: name.trim(), topic, visibility: priv ? "private" : "public" });
      onCreated(c);
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>채널 만들기</h2>
        <p className="modal-sub">소문자·숫자·한글·-·_ 80자 이내</p>
        <input placeholder="예: 동남아-영업" value={name} autoFocus
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && name.trim() && void go()} />
        <input placeholder="주제 (선택)" value={topic} style={{ marginTop: 8 }}
          onChange={(e) => setTopic(e.target.value)} />
        <label className="chk" title={canPriv ? "" : "비공개 채널을 만들 권한이 없어요"}>
          <input type="checkbox" checked={priv} disabled={!canPriv}
            onChange={(e) => setPriv(e.target.checked)} />
          🔒 비공개 — 초대받은 사람만 보고 검색됩니다
        </label>
        {err && <div className="team-note">{err}</div>}
        <div className="modal-actions">
          <button className="btn ghost" onClick={onClose}>취소</button>
          <button className="btn pri" disabled={!name.trim()} onClick={() => void go()}>만들기</button>
        </div>
      </div>
    </div>);
}

export function SearchPanel({ slug, channels, members, onOpen }: {
  slug: string; channels: Ch[]; members: Member[]; onOpen: (cid: number) => void;
}) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Msg[] | null>(null);
  const [err, setErr] = useState("");
  async function go() {
    setErr("");
    try { setRes((await capi(`/t/${slug}/search?q=${encodeURIComponent(q)}`)).results); }
    catch (e) { setErr((e as Error).message); }
  }
  const chName = (id: number) => channels.find((c) => c.id === id)?.name ?? `#${id}`;
  return (
    <div className="team-panel">
      <h2>검색</h2>
      <p className="team-sub">볼 수 있는 채널에서만 찾습니다(비공개 채널은 멤버일 때만).
        12개월 지난 아카이브는 검색되지 않아요.</p>
      <div className="invite-row">
        <input placeholder="검색어 (2자 이상)" value={q} autoFocus
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && q.trim().length >= 2 && void go()} />
        <button className="btn pri" disabled={q.trim().length < 2} onClick={() => void go()}>검색</button>
      </div>
      {err && <div className="team-note">{err}</div>}
      {res && <h3>결과 {res.length}건</h3>}
      {res?.map((m) => (
        <button key={`${m.channel_id}-${m.seq}`} className="search-hit" onClick={() => onOpen(m.channel_id)}>
          <span className="sh-ch"># {chName(m.channel_id)}</span>
          <span className="sh-who">{nameOf(members, m.author)} · {hhmm(m.created_at)}</span>
          <span className="sh-body">{m.body}</span>
        </button>))}
    </div>);
}

/** 워크스페이스 사람·기록 — 초대·역할 변경·감사·열람 기록. 버튼은 권한(me.can)에
 *  맞춰 보이지만, 판정은 서버가 한다(거부되면 서버 메시지를 그대로 보여준다). */
export function PeoplePanel({ slug, me, members, onChanged, extra }: {
  slug: string; me: Me | null; members: Member[]; onChanged: () => void;
  extra?: React.ReactNode;
}) {
  const [tab, setTab] = useState<"people" | "logs">("people");
  const [uid, setUid] = useState(""); const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [note, setNote] = useState("");
  const [logs, setLogs] = useState<{ which: string; rows: Record<string, unknown>[] } | null>(null);
  const can = me?.can ?? {};
  async function run(fn: () => Promise<unknown>, ok?: string) {
    setNote("");
    try { await fn(); if (ok) setNote(ok); onChanged(); } catch (e) { setNote((e as Error).message); }
  }
  async function loadLogs(which: "access_log" | "audit_log") {
    try { setLogs({ which, rows: (await capi(`/t/${slug}/logs/${which}?limit=100`)).rows }); }
    catch (e) { setNote((e as Error).message); }
  }
  return (
    <div className="team-panel">
      <h2>{me?.tenant.name ?? "워크스페이스"}</h2>
      <p className="team-sub">내 역할: <b>{me?.role}</b></p>
      <div className="seg-tabs">
        <button className={tab === "people" ? "on" : ""} onClick={() => setTab("people")}>멤버</button>
        {can["audit.read"] && <button className={tab === "logs" ? "on" : ""}
          onClick={() => { setTab("logs"); void loadLogs("access_log"); }}>기록</button>}
      </div>
      {tab === "people" && (<>
        <h3>멤버 {members.length}명</h3>
        <div className="members">
          {members.map((m) => (
            <div className="member" key={m.user_id}>
              <div className="ava sm" style={{ background: avaColor(m.user_id) }}>
                {m.display_name[0]?.toUpperCase()}</div>
              <div className="m-name">{m.display_name}{m.user_id === me?.user_id && " (나)"}
                <span className="m-mail">{m.email}</span></div>
              {can["member.update"] && m.user_id !== me?.user_id && m.role !== "owner" ? (
                <select className="role-sel" value={m.role} onChange={(e) =>
                  void run(() => capi(`/t/${slug}/members/${m.user_id}`, { role: e.target.value }, "PATCH"),
                           "역할을 바꿨어요")}>
                  {["admin", "member", "guest"].map((r) => <option key={r} value={r}>{r}</option>)}
                </select>) : <span className="m-role">{m.role}</span>}
              {can["member.update"] && m.user_id !== me?.user_id && m.role !== "owner" && (
                <button className="mini" onClick={() => {
                  if (confirm(`${m.display_name} 님을 비활성화할까요? 모든 채널에서 빠지고 접근이 끊깁니다.`))
                    void run(() => capi(`/t/${slug}/members/${m.user_id}`, { active: false }, "PATCH"));
                }}>내보내기</button>)}
            </div>))}
        </div>
        {can["member.invite"] && (<>
          <h3>멤버 추가</h3>
          <div className="invite-grid">
            <input placeholder="사용자 ID" value={uid} onChange={(e) => setUid(e.target.value)} />
            <input placeholder="이메일" value={email} onChange={(e) => setEmail(e.target.value)} />
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="member">멤버</option><option value="admin">관리자</option>
              <option value="guest">게스트 (초대받은 채널만)</option>
            </select>
            <button className="btn pri" disabled={!uid.trim() || !email.includes("@")}
              onClick={() => void run(async () => {
                await capi(`/t/${slug}/members`, { user_id: uid.trim(), email: email.trim(), role });
                setUid(""); setEmail("");
              }, "추가했어요")}>추가</button>
          </div>
        </>)}
        {extra}
      </>)}
      {tab === "logs" && (<>
        <div className="seg-tabs sm">
          <button className={logs?.which === "access_log" ? "on" : ""}
            onClick={() => void loadLogs("access_log")}>플랫폼 열람 기록</button>
          <button className={logs?.which === "audit_log" ? "on" : ""}
            onClick={() => void loadLogs("audit_log")}>관리 감사 기록</button>
        </div>
        {logs?.which === "access_log" && (
          <p className="team-sub">플랫폼 관리자가 이 워크스페이스의 메시지를 열람하면 여기에 남습니다.</p>)}
        <div className="log-list">
          {logs?.rows.length === 0 && <div className="empty">기록이 없어요</div>}
          {logs?.rows.map((r) => (
            <div className="log-row" key={String(r.id)}>
              <span className="lr-at">{new Date(String(r.at)).toLocaleString("ko-KR")}</span>
              <span className="lr-act">{String(r.action)}</span>
              <span className="lr-who">{String(r.actor)}</span>
              <span className="lr-tgt">{String(r.target ?? r.channel_id ?? "")}
                {r.reason ? ` — 사유: ${String(r.reason)}` : ""}</span>
            </div>))}
        </div>
      </>)}
      {note && <div className="team-note">{note}</div>}
    </div>);
}

/** 비공개 채널 멤버 관리 — 채널 관리자(만든 사람)·관리자만 추가할 수 있다. */
export function ChannelMembers({ slug, ch, members, onClose }: {
  slug: string; ch: Ch; members: Member[]; onClose: () => void;
}) {
  const [pick, setPick] = useState("");
  const [note, setNote] = useState("");
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{ch.visibility === "private" ? "🔒" : "#"} {ch.name} 멤버 추가</h2>
        <p className="modal-sub">추가된 사람은 이 채널의 지난 메시지도 볼 수 있어요.</p>
        <select value={pick} onChange={(e) => setPick(e.target.value)} className="role-sel wide">
          <option value="">사람 선택…</option>
          {members.map((m) => <option key={m.user_id} value={m.user_id}>{m.display_name} ({m.role})</option>)}
        </select>
        {note && <div className="team-note">{note}</div>}
        <div className="modal-actions">
          <button className="btn ghost" onClick={onClose}>닫기</button>
          <button className="btn pri" disabled={!pick} onClick={async () => {
            try {
              await capi(`/t/${slug}/channels/${ch.id}/members`, { user_id: pick });
              setNote("추가했어요"); setPick("");
            } catch (e) { setNote((e as Error).message); }
          }}>추가</button>
        </div>
      </div>
    </div>);
}

export const isLead = (name: string) => name.startsWith("lead-");
export const leadName = (rid: string) => `lead-${rid}`.toLowerCase().replace(/[^a-z0-9가-힣_-]/g, "-").slice(0, 80);
/** 후보 토론 스레드의 루트 — 같은 client_msg_id로 게시하면 서버가 늘 같은 seq를 돌려준다
 *  (멱등). 그래서 '후보 → 스레드' 대응표를 따로 저장하지 않아도 된다. */
export const candRootId = (cid: string) =>
  `cand-${cid}`.replace(/[^A-Za-z0-9_-]/g, "_").padEnd(8, "_").slice(0, 64);

// ── 리드 데이터 워크스페이스(멤버십 통합) ─────────────────────────────
// 테넌트 멤버는 리드 요청·후보·초안을 함께 본다(서버 app/saas/workspace.py).
// 예전 혼자/예전 팀 데이터가 남아 있으면 서버는 '사라져 보이지 않게' 예전
// 워크스페이스에 머물고, 여기서 옮기기를 안내한다.

type LeadWs = { workspace_id: string; mode: "tenant" | "legacy";
  tenant: { slug: string; name: string } | null;
  migration: { pending: boolean; legacy_ws: string; can_migrate: boolean;
    counts: Record<string, number> } };

async function sapi(path: string, body?: unknown) {
  const r = await fetch(`/api/saas${path}`, { method: body === undefined ? "GET" : "POST",
    headers: await authHeaders(), body: body === undefined ? undefined : JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j?.error?.message ?? (typeof j?.detail === "string" ? j.detail : null)
    ?? `요청 실패 (${r.status})`);
  return j;
}

function useLeadWs(slug: string) {
  const [st, setSt] = useState<LeadWs | null>(null);
  useEffect(() => { sapi("/workspace").then(setSt).catch(() => setSt(null)); }, [slug]);
  return st;
}

export function LeadDataBanner({ slug, onOpen }: { slug: string; onOpen: () => void }) {
  const st = useLeadWs(slug);
  if (!st?.migration.pending) return null;
  return (
    <div className="lead-banner" role="status">
      예전 리드 데이터를 아직 <b>{st.tenant?.name}</b>(으)로 옮기지 않아 지금은 예전 공간을 보고 있어요.
      <button className="mini" onClick={onOpen}>자세히</button>
    </div>);
}

export function LeadDataCard({ slug }: { slug: string }) {
  const st = useLeadWs(slug);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  if (!st) return null;
  const m = st.migration;
  const n = (k: string) => m.counts[k] ?? 0;
  async function go() {
    if (!st?.tenant) return;
    if (!confirm(`예전 리드 데이터를 ${st.tenant.name}(으)로 옮길까요?\n`
      + "복사로 옮기며 원본은 지우지 않습니다. 예전 팀원은 이 워크스페이스의 멤버가 됩니다.")) return;
    setBusy(true); setNote("");
    try {
      const r = await sapi("/workspace/migrate", { tenant_slug: st.tenant.slug });
      const c = Object.values(r.copied as Record<string, number>).reduce((a, b) => a + b, 0);
      setNote(`옮겼어요 — 문서 ${c}건${r.members_added.length ? `, 멤버 ${r.members_added.length}명 추가` : ""}. 새로 불러옵니다…`);
      setTimeout(() => window.location.reload(), 900);
    } catch (e) { setNote((e as Error).message); setBusy(false); }
  }
  return (
    <section className="lead-card">
      <h3>리드 데이터</h3>
      {!m.pending ? (
        <p className="team-sub">이 워크스페이스 멤버 모두가 리드 요청·후보·초안을 함께 봅니다.</p>
      ) : (<>
        <p className="team-sub">
          예전 공간에 리드 요청 {n("lead_request")}건·온보딩 {n("onboarding")}건이 남아 있어
          지금은 예전 공간을 보고 있어요. 옮기면 <b>{st.tenant?.name}</b> 멤버 모두가 함께 봅니다.
        </p>
        {m.can_migrate
          ? <button className="btn pri" disabled={busy} onClick={() => void go()}>
              {busy ? "옮기는 중…" : `${st.tenant?.name}(으)로 옮기기`}</button>
          : <p className="team-sub">옮기려면 예전 공간의 소유자이면서 이 워크스페이스에서 멤버를
              들일 권한(관리자)이 있어야 해요 — 관리자에게 요청하세요.</p>}
      </>)}
      {note && <p className="team-note">{note}</p>}
    </section>);
}
