"use client";
/* 팀 채팅 — 채널·스레드·팀원. 서버 원본은 /saas/chat·/saas/team.
 *
 * AI 작업 대화(page.tsx의 msgs)는 여전히 브라우저 상태다. 여기서 다루는
 * 것은 **사람이 사람에게** 남긴 말과, 팀원이 봐야 할 작업 소식(activity)
 * 뿐이다 — 둘을 섞어 저장하면 후보 카드 같은 살아 있는 UI가 스냅샷으로
 * 굳어 버린다.
 *
 * 실시간은 폴링이다. 엔진 job도 폴링이고, 배포 대상(Vercel 함수)이 긴
 * 연결을 붙잡지 못한다 — 같은 규칙으로 맞춘다.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type Api = (path: string, body?: unknown,
                   method?: "GET" | "POST" | "DELETE") => Promise<any>;

export type TeamMsg = { id: string; channel: string; thread: string | null;
  text: string; kind: "text" | "activity"; ts: number; mentions: string[];
  author: { uid: string; email: string }; deleted?: boolean };
export type ThreadInfo = Record<string, { count: number; last_ts: number }>;
export type Member = { uid: string; email: string; role: "owner" | "member";
  joined?: number; last_seen?: number | null };
export type Team = { workspace_id: string;
  me: { uid: string; email: string; role: "owner" | "member" };
  members: Member[]; invites: { email: string; invited_by: string; at: number }[];
  pending_invite: { ws: string; invited_by: string } | null };
export type ChanState = Record<string, { last_ts: number; mine: boolean;
  read_ts: number; mentions: number }>;

export const GENERAL = "general";
const FEED_EVERY = 4000;
const SIDE_EVERY = 15000;
const ONLINE_WINDOW = 90;      // 초 — 접속 표시는 폴링 주기의 몇 배로 넉넉히

export function displayName(email: string): string {
  return (email || "?").split("@")[0];
}

/** 이메일마다 같은 색 — 아바타 색으로 누가 말했는지 위치보다 먼저 읽힌다. */
const AVA = ["#3B6FD8", "#2E9C6A", "#B5568F", "#C77A1E", "#6B5BD2", "#2A8C9C"];
export function avaColor(seed: string): string {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVA[h % AVA.length];
}

export function online(m: Member): boolean {
  return !!m.last_seen && Date.now() / 1000 - m.last_seen < ONLINE_WINDOW;
}

export function hhmm(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });
}

/** 스레드 키는 서버 정규식([A-Za-z0-9_.:-])을 통과해야 한다. */
export function threadKey(kind: "cand" | "m", id: string): string {
  const safe = id.replace(/[^A-Za-z0-9_.:-]/g, "_");
  return `${kind}:${safe}`.slice(0, 120);
}

/** 채널 메시지 — 처음 한 번 전부, 그다음은 since 이후만. */
export function useChannel(api: Api, ch: string | null, thread?: string | null) {
  const [msgs, setMsgs] = useState<TeamMsg[]>([]);
  const [threads, setThreads] = useState<ThreadInfo>({});
  const since = useRef(0);

  const pull = useCallback(async (full = false) => {
    if (!ch) return;
    const q = new URLSearchParams({ since: String(full ? 0 : since.current) });
    if (thread) q.set("thread", thread);
    try {
      const r = await api(`/chat/${ch}/messages?${q}`);
      const got = (r.messages ?? []) as TeamMsg[];
      if (got.length) since.current = Math.max(since.current, ...got.map((m) => m.ts));
      // 삭제 표시는 since 이후로 오지 않는다(ts가 그대로라). 전체 재조회 때
      // 갈아끼우고, 증분일 때는 id로 병합한다.
      setMsgs((prev) => {
        if (full) return got;
        const byId = new Map(prev.map((m) => [m.id, m]));
        for (const m of got) byId.set(m.id, m);
        return [...byId.values()].sort((a, b) => a.id.localeCompare(b.id));
      });
      if (r.threads) setThreads(r.threads);
    } catch { /* 채팅 폴링 실패가 작업을 막지 않는다 */ }
  }, [api, ch, thread]);

  useEffect(() => {
    since.current = 0;
    setMsgs([]); setThreads({});
    if (!ch) return;
    void pull(true);
    const id = setInterval(() => void pull(false), FEED_EVERY);
    return () => clearInterval(id);
  }, [ch, thread, pull]);

  const post = useCallback(async (text: string,
                                  kind: "text" | "activity" = "text") => {
    if (!ch) return;
    const m = await api(`/chat/${ch}/messages`,
      { text, kind, thread: thread ?? null }) as TeamMsg;
    since.current = Math.max(since.current, m.ts);
    setMsgs((prev) => [...prev, m]);
  }, [api, ch, thread]);

  const remove = useCallback(async (id: string) => {
    if (!ch) return;
    const m = await api(`/chat/${ch}/messages?id=${encodeURIComponent(id)}`,
      undefined, "DELETE") as TeamMsg;
    setMsgs((prev) => prev.map((x) => (x.id === id ? m : x)));
  }, [api, ch]);

  return { msgs, threads, post, remove, refresh: () => pull(true) };
}

/** 팀 명부·채널 안 읽음 — 사이드바가 쓴다. */
export function useTeam(api: Api) {
  const [team, setTeam] = useState<Team | null>(null);
  const [chans, setChans] = useState<ChanState>({});
  const load = useCallback(async () => {
    try {
      const [t, c] = await Promise.all([api("/team"), api("/chat/channels")]);
      setTeam(t); setChans(c.channels ?? {});
    } catch { /* 팀 기능 실패가 혼자 쓰기를 막지 않는다 */ }
  }, [api]);
  useEffect(() => {
    void load();
    const id = setInterval(load, SIDE_EVERY);
    return () => clearInterval(id);
  }, [load]);
  const markRead = useCallback(async (ch: string, ts: number) => {
    setChans((s) => s[ch] ? { ...s, [ch]: { ...s[ch], read_ts: ts, mentions: 0 } } : s);
    try { await api(`/chat/${ch}/read`, { ts }); } catch { /* 다음 폴링이 바로잡는다 */ }
  }, [api]);
  return { team, chans, reload: load, markRead };
}

export function unread(s: ChanState[string] | undefined): boolean {
  return !!s && !s.mine && s.last_ts > s.read_ts;
}

/** @이름 강조 — 본문은 그대로 두고 표시만 바꾼다. */
function Rich({ text, me }: { text: string; me: string }) {
  const parts = text.split(/(@[^\s@,]+(?:@[^\s@,]+)?)/g);
  return <>{parts.map((p, i) => p.startsWith("@") ? (
    <span key={i} className={`mention ${
      p.slice(1).toLowerCase() === me.toLowerCase()
      || p.slice(1).toLowerCase() === displayName(me).toLowerCase() ? "me" : ""}`}>
      {p}</span>) : <span key={i}>{p}</span>)}</>;
}

export function TeamRow({ m, me, prev, replies, onReply, onDelete }: {
  m: TeamMsg; me: string; prev?: TeamMsg | null;
  replies?: { count: number; last_ts: number }; onReply?: () => void;
  onDelete?: () => void;
}) {
  // 같은 사람이 5분 안에 이어 쓰면 머리(아바타·이름)를 접는다 — 슬랙 규칙
  const cont = !!prev && prev.author.uid === m.author.uid
    && prev.kind === m.kind && m.ts - prev.ts < 300;
  const name = displayName(m.author.email);
  if (m.kind === "activity") {
    return (
      <div className="trow act">
        <span className="act-dot" />
        <span className="act-txt"><b>{name}</b> {m.text}</span>
        <span className="t">{hhmm(m.ts)}</span>
      </div>);
  }
  const mine = m.author.email === me;
  return (
    <div className={`trow ${cont ? "cont" : ""} ${
      m.mentions?.length && !mine ? "has-mention" : ""}`}>
      {cont
        ? <span className="t gutter">{hhmm(m.ts)}</span>
        : <div className="ava" style={{ background: avaColor(m.author.email) }}>
            {name[0]?.toUpperCase()}</div>}
      <div className="tbody">
        {!cont && (
          <div className="who">{name}<span className="t">{hhmm(m.ts)}</span></div>)}
        <div className="ttext">
          {m.deleted ? <i className="gone">삭제된 메시지</i>
            : <Rich text={m.text} me={me} />}
        </div>
        {replies && replies.count > 0 && onReply && (
          <button className="reply-link" onClick={onReply}>
            답글 {replies.count}개 <span className="t">마지막 {hhmm(replies.last_ts)}</span>
          </button>)}
      </div>
      {!m.deleted && (onReply || (mine && onDelete)) && (
        <div className="hover-bar">
          {onReply && <button title="스레드에서 답글" onClick={onReply}>💬</button>}
          {mine && onDelete && (
            <button title="삭제" onClick={() => {
              if (confirm("이 메시지를 삭제할까요?")) onDelete();
            }}>🗑</button>)}
        </div>)}
    </div>);
}

/** 오른쪽 스레드 패널 — 메시지 답글이든 후보 토론이든 같은 모양이다. */
export function ThreadPane({ api, ch, thread, title, root, me, onClose }: {
  api: Api; ch: string; thread: string; title: string;
  root?: React.ReactNode; me: string; onClose: () => void;
}) {
  const { msgs, post, remove } = useChannel(api, ch, thread);
  const [v, setV] = useState("");
  const [err, setErr] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.length]);
  async function send() {
    const t = v.trim();
    if (!t) return;
    setV(""); setErr("");
    try { await post(t); } catch (e) { setErr((e as Error).message); setV(t); }
  }
  return (
    <aside className="thread-pane" aria-label="스레드">
      <header className="tp-head">
        <div><b>스레드</b><span className="tp-sub">{title}</span></div>
        <button className="icon-btn" aria-label="스레드 닫기" onClick={onClose}>✕</button>
      </header>
      <div className="tp-body">
        {root && <div className="tp-root">{root}</div>}
        <div className="tp-count">{msgs.length ? `답글 ${msgs.length}개` : "아직 답글이 없어요"}</div>
        {msgs.map((m, i) => (
          <TeamRow key={m.id} m={m} me={me} prev={msgs[i - 1]}
            onDelete={() => void remove(m.id)} />))}
        <div ref={end} />
      </div>
      <div className="tp-comp">
        <div className="comp-box">
          <textarea rows={2} value={v} placeholder="답글 남기기… (@이름으로 팀원 호출)"
            onChange={(e) => setV(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault(); void send();
              }
            }} />
          <div className="comp-bar">
            <button className="send" disabled={!v.trim()} onClick={() => void send()}
              title="보내기">➤</button>
          </div>
        </div>
        {err && <div className="tp-err">{err}</div>}
      </div>
    </aside>);
}

/** 팀원·초대 — 소유자만 초대하고 내보낸다. */
export function TeamPanel({ api, team, onChanged }: {
  api: Api; team: Team | null; onChanged: () => void;
}) {
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  if (!team) return <div className="team-panel"><div className="empty">불러오는 중…</div></div>;
  const owner = team.me.role === "owner";
  async function run(fn: () => Promise<unknown>, ok?: string) {
    setNote("");
    try { await fn(); if (ok) setNote(ok); onChanged(); }
    catch (e) { setNote((e as Error).message); }
  }
  return (
    <div className="team-panel">
      <h2>팀</h2>
      <p className="team-sub">
        같은 워크스페이스의 팀원은 Lead Request·후보·초안·발송 기록을 함께 보고,
        채널과 스레드에서 이야기합니다.</p>

      {team.pending_invite && (
        <div className="card invite-card">
          <div className="card-head">초대가 와 있어요</div>
          <div className="card-body">
            <p><b>{team.pending_invite.invited_by}</b> 님이 팀 워크스페이스로 초대했어요.
              수락하면 그 팀의 채널과 요청이 보이고, <b>지금 내 워크스페이스의
              자료는 보이지 않게 됩니다</b>(지워지지는 않아요 — 나가면 돌아옵니다).</p>
            <div className="row-btns">
              <button className="btn pri" onClick={() => run(async () => {
                await api("/team/accept", undefined, "POST");
                location.reload();        // 모든 상태가 다른 워크스페이스 것이 된다
              })}>수락</button>
              <button className="btn ghost"
                onClick={() => run(() => api("/team/decline", undefined, "POST"), "거절했어요")}>
                거절</button>
            </div>
          </div>
        </div>)}

      <h3>팀원 {team.members.length}명</h3>
      <div className="members">
        {team.members.map((m) => (
          <div className="member" key={m.uid}>
            <div className="ava sm" style={{ background: avaColor(m.email) }}>
              {displayName(m.email)[0]?.toUpperCase()}
              <span className={`presence ${online(m) ? "on" : ""}`} />
            </div>
            <div className="m-name">{displayName(m.email)}
              <span className="m-mail">{m.email}</span></div>
            <span className="m-role">{m.role === "owner" ? "소유자" : "멤버"}</span>
            {owner && m.uid !== team.me.uid && (
              <button className="mini" onClick={() => {
                if (confirm(`${m.email} 님을 내보낼까요?`))
                  void run(() => api(`/team/members/${m.uid}`, undefined, "DELETE"));
              }}>내보내기</button>)}
          </div>))}
      </div>

      {owner ? (
        <>
          <h3>초대</h3>
          <div className="invite-row">
            <input type="email" placeholder="동료 이메일" value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && email.trim() && document
                .getElementById("invite-btn")?.click()} />
            <button id="invite-btn" className="btn pri" disabled={!email.trim()}
              onClick={() => run(async () => {
                const r = await api("/team/invites", { email: email.trim() });
                setEmail("");
                // 서버는 이메일로만 대조할 수 있다(초대 시점엔 상대 uid를 모른다).
                // 그래서 '막힌다'고 단정하지 않고 확인할 것을 말한다.
                if (!r.allowed) throw new Error(
                  `${r.email} 님을 초대했어요. 다만 접근 허용 목록(SAAS_ALLOWED_USERS)에서 `
                  + "이 이메일을 찾지 못했어요 — uid로 등록돼 있지 않다면 로그인이 막히니 "
                  + "관리자에게 추가를 요청하세요.");
              }, "초대했어요. 상대가 로그인하면 팀 패널에서 수락할 수 있어요.")}>
              초대</button>
          </div>
          {team.invites.map((i) => (
            <div className="member pending" key={i.email}>
              <div className="m-name">{i.email}<span className="m-mail">수락 대기</span></div>
              <button className="mini" onClick={() =>
                run(() => api(`/team/invites/${encodeURIComponent(i.email)}`, undefined, "DELETE"))}>
                취소</button>
            </div>))}
        </>
      ) : (
        <button className="btn ghost leave" onClick={() => {
          if (confirm("이 팀 워크스페이스에서 나갈까요? 내 개인 워크스페이스로 돌아갑니다."))
            void run(async () => {
              await api("/team/leave", undefined, "POST"); location.reload();
            });
        }}>팀에서 나가기</button>
      )}
      {note && <div className="team-note">{note}</div>}
    </div>);
}
