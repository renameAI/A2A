// 사이드 패널 — Electron 앱의 오른쪽 채팅방 + 왼쪽 사이드바의 "AI 채팅·팀 채널·마이페이지"를 한 칸에.
// 좁을 때: 위 워크스페이스 단추 · 방 제목(누르면 방 바꾸기 목록) / 아래 지금 방
// 넓을 때(패널을 끌어 넓히면): 왼쪽에 채팅 목록이 붙고 오른쪽이 지금 방 — Electron 때의 "목록 + 채팅방" 배치
import { useEffect, useState } from 'react';
import Icon from './Icon.jsx';
import RoomPanel from './Room.jsx';
import ProfilePicker from './ProfilePicker.jsx';
import { Avatar } from './Mentions.jsx';
import {
  a2a, useStore, openRoom, openTask, newAiChat, listProfiles, switchProfile, showPicker,
} from './store.js';
import { STATUS_DOT, statusText } from './format.js';

function Row({ on, onClick, children, title }) {
  return (
    <button onClick={onClick} title={title}
            className={`flex h-8 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-[13px] ${on ? 'bg-raise font-semibold' : 'hover:bg-raise'}`}>
      {children}
    </button>
  );
}

// 방 바꾸기 — AI 채팅(새로·최근) · 팀 채널(안 읽음) · 마이페이지
function RoomSwitcher({ onClose, docked = false }) {
  const S = useStore((s) => s.S);
  const room = useStore((s) => s.room);
  const channels = useStore((s) => s.channels);
  const go = (fn) => { fn(); onClose(); };
  const aiChats = S.tasks.filter((t) => !t.room).slice(0, 8);
  const working = new Set(S.tasks.filter((t) => t.room && t.status === 'working').map((t) => t.room.cid));
  return (
    <div id={docked ? 'room-list' : undefined}
         className={docked ? 'min-h-0 flex-1 overflow-y-auto px-2 pt-2 pb-4' : 'absolute inset-x-0 top-[52px] bottom-0 z-30 overflow-y-auto bg-card px-2 pt-2 pb-4'}>
      <div className="px-2.5 pt-1 pb-1 text-[11px] font-semibold text-card-faint">AI 채팅</div>
      <Row onClick={() => go(newAiChat)}><Icon name="compose" className="size-4 text-card-sub" />새 AI 채팅</Row>
      {aiChats.map((t) => (
        <Row key={t.id} on={room.kind === 'ai' && room.id === t.id} onClick={() => go(() => openTask(t.id))} title={statusText(t)}>
          <span className={`mx-1 size-[7px] shrink-0 rounded-full ${STATUS_DOT[t.status]}`} />
          <span className="min-w-0 flex-1 truncate">{t.title}</span>
        </Row>
      ))}
      <div className="px-2.5 pt-3 pb-1 text-[11px] font-semibold text-card-faint">팀 채널</div>
      {channels === null && <div className="px-2.5 py-1 text-xs text-card-faint">불러오는 중…</div>}
      {channels?.length === 0 && <div className="px-2.5 py-1 text-xs text-card-faint">채널을 불러오지 못했습니다 — 엔진(8423)을 확인하세요</div>}
      {channels?.map((c) => {
        const on = room.kind === 'channel' && room.id === c.id;
        return (
          <Row key={c.id} on={on} onClick={() => go(() => openRoom({ kind: 'channel', id: c.id }))}>
            <Icon name={c.visibility === 'private' ? 'lock' : 'hash'} className="size-4 text-card-sub" />
            <span className={`min-w-0 flex-1 truncate ${!on && c.unread > 0 ? 'font-semibold' : ''}`}>{c.name}</span>
            {working.has(c.id) && <Icon name="spark" className="size-3.5 animate-twinkle text-accent" />}
            {!on && c.unread > 0 && (
              <span className={`grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 text-[10.5px] font-bold ${c.mentions > 0 ? 'bg-accent text-white' : 'bg-raise'}`}>
                {c.mentions > 0 ? `@${c.mentions}` : c.unread > 99 ? '99+' : c.unread}
              </span>
            )}
          </Row>
        );
      })}
      <div className="my-2 h-px bg-card-line" />
      <Row onClick={() => a2a.send('map:open')}><Icon name="graph" className="size-4 text-card-sub" />업무 지도</Row>
      <Row on={room.kind === 'history'} onClick={() => go(() => openRoom({ kind: 'history' }))}><Icon name="clock" className="size-4 text-card-sub" />방문 기록</Row>
      <Row on={room.kind === 'downloads'} onClick={() => go(() => openRoom({ kind: 'downloads' }))}><Icon name="download" className="size-4 text-card-sub" />다운로드</Row>
      <Row on={room.kind === 'me'} onClick={() => go(() => openRoom({ kind: 'me' }))}><Icon name="person" className="size-4 text-card-sub" />마이페이지</Row>
    </div>
  );
}

// 워크스페이스 단추 — 크롬 프로필 메뉴처럼: 지금 계정의 워크스페이스 바꾸기 · 다른 프로필 · 마이페이지
function WorkspaceButton() {
  const S = useStore((s) => s.S);
  const me = useStore((s) => s.me);
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { if (open) listProfiles().then(setData); }, [open]);
  useEffect(() => {
    const close = () => setOpen(false);
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, []);
  const mine = data?.profiles.find((p) => p.user === S.user)?.tenants || [];
  const go = async (slug) => {
    if (slug === S.tenant) { setOpen(false); return; }
    const r = await switchProfile(S.user, slug);
    if (r?.ok) setOpen(false); else setErr(r?.error || '바꾸지 못했어요');
  };
  return (
    <div className="relative">
      <button id="ws-button" onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }} title={`${S.tenantName} · @${S.user}`}
              className="grid size-8 place-items-center rounded-lg hover:bg-raise">
        <Avatar id={me?.user_id || S.user} name={me?.display_name || S.user} size="sm" />
      </button>
      {open && (
        <div onClick={(e) => e.stopPropagation()}
             className="absolute top-full left-0 z-40 mt-1 w-64 rounded-[14px] bg-card p-1.5 text-card-ink shadow-[0_10px_40px_rgb(0_0_0/.22)] ring-1 ring-card-line">
          <div className="px-2.5 pt-1.5 pb-1 text-[11px] font-semibold text-card-faint">@{S.user}의 워크스페이스</div>
          {!data && <div className="px-2.5 py-1.5 text-xs text-card-faint">불러오는 중…</div>}
          {mine.map((t) => (
            <button key={t.slug} data-ws={t.slug} onClick={() => go(t.slug)}
                    className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-raise">
              <span className="grid size-6 shrink-0 place-items-center rounded-md bg-raise text-[11px] font-bold">{t.name.charAt(0).toUpperCase()}</span>
              <span className="min-w-0 flex-1 truncate">{t.name}</span>
              {t.slug === S.tenant && <Icon name="check" className="size-3.5 text-accent" />}
            </button>
          ))}
          {err && <div className="px-2.5 py-1 text-[11.5px] text-warn">{err}</div>}
          <div className="my-1 h-px bg-card-line" />
          <button onClick={() => { setOpen(false); showPicker(true); }} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-raise">
            <Icon name="people" className="size-4 text-card-sub" />다른 계정·워크스페이스…
          </button>
          <button onClick={() => { setOpen(false); openRoom({ kind: 'me' }); }} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-raise">
            <Icon name="person" className="size-4 text-card-sub" />마이페이지
          </button>
        </div>
      )}
    </div>
  );
}

const DOCK_WIDTH = 600;
function useWide() {
  const [wide, setWide] = useState(() => window.innerWidth >= DOCK_WIDTH);
  useEffect(() => {
    const on = () => setWide(window.innerWidth >= DOCK_WIDTH);
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return wide;
}

export default function SidePanel() {
  const S = useStore((s) => s.S);
  const room = useStore((s) => s.room);
  const [switcher, setSwitcher] = useState(false);
  const wide = useWide();
  useEffect(() => { setSwitcher(false); }, [room.kind, room.id, wide]);
  if (!S) return <div className="grid h-screen place-items-center text-xs text-card-faint">A2A 연결 중…</div>;
  if (wide) {
    return (
      <div className="relative flex h-screen bg-card text-card-ink">
        <nav className="flex w-[232px] shrink-0 flex-col border-r border-card-line">
          <div className="flex h-[52px] shrink-0 items-center gap-2 border-b border-card-line px-2">
            <WorkspaceButton />
            <div className="min-w-0 flex-1 leading-tight">
              <div className="truncate text-[13px] font-semibold">{S.tenantName}</div>
              <div className="truncate text-[11px] text-card-faint">@{S.user}</div>
            </div>
          </div>
          <RoomSwitcher docked onClose={() => {}} />
        </nav>
        <div className="min-w-0 flex-1"><RoomPanel /></div>
        {S.pickProfile && <ProfilePicker />}
      </div>
    );
  }
  return (
    <div className="relative h-screen">
      <RoomPanel headerLeft={<WorkspaceButton />} onTitle={() => setSwitcher((v) => !v)} switcherOpen={switcher} />
      {switcher && <RoomSwitcher onClose={() => setSwitcher(false)} />}
      {S.pickProfile && <ProfilePicker />}
    </div>
  );
}
