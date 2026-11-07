// 왼쪽 사이드바 — 워크스페이스 · 즐겨찾기 · 팀 채널 · 세로 탭 · 에이전트 작업 · 배경화면
import { useEffect, useState } from 'react';
import Icon from './Icon.jsx';
import { a2a, useStore, openTask, openRoom, newAiChat, addMention, listProfiles, switchProfile, showPicker } from './store.js';
import { Avatar } from './Mentions.jsx';
import { WALLS, hostOf, hue, statusText, STATUS_DOT } from './format.js';

const ROW = 'group relative flex h-8 w-full items-center gap-2.5 rounded-[9px] pr-2 pl-2.5 text-left text-[13px] text-ink transition-colors';
const rowCls = (on) => `${ROW} ${on ? 'bg-sel shadow-sel' : 'hover:bg-hover'}`;

export function IconButton({ name, title, onClick, small, className = '', disabled }) {
  return (
    <button title={title} onClick={onClick} disabled={disabled}
            className={`no-drag grid shrink-0 place-items-center text-sub hover:bg-hover hover:text-ink disabled:opacity-35 disabled:hover:bg-transparent
                        ${small ? 'size-[22px] rounded-md' : 'size-7 rounded-lg'} ${className}`}>
      <Icon name={name} className={small ? 'size-3.5' : 'size-4'} />
    </button>
  );
}

export function Favicon({ url, favicon, title, loading, newtab }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [favicon]);
  if (loading) return <span className="grid size-4 place-items-center"><span className="size-3.5 animate-spin rounded-full border-2 border-sub border-r-transparent opacity-60" /></span>;
  if (newtab) return <Icon name="spark" className="size-4 text-sub" />;
  if (favicon && !broken) return <img src={favicon} onError={() => setBroken(true)} className="size-4 shrink-0 rounded-[4px]" alt="" />;
  const label = (title || hostOf(url) || '?').trim().charAt(0).toUpperCase();
  return (
    <span className="grid size-4 shrink-0 place-items-center rounded-[5px] text-[10px] font-bold text-white"
          style={{ background: hue(hostOf(url) || title) }}>{label}</span>
  );
}

function CloseX({ title, onClick }) {
  return (
    <span role="button" title={title} onClick={(e) => { e.stopPropagation(); onClick(); }}
          className="hidden size-5 shrink-0 place-items-center rounded-md text-sub group-hover:grid hover:bg-hover hover:text-ink">
      <Icon name="x" className="size-[13px]" />
    </span>
  );
}

// 사이드바 항목을 지금 입력창에 맥락으로 붙인다(@ 태그)
function AtButton({ m }) {
  return (
    <span role="button" title="입력창에 @로 붙이기" onClick={(e) => { e.stopPropagation(); addMention(m); }}
          className="hidden h-5 shrink-0 place-items-center rounded-md px-1 text-[11px] font-bold text-sub group-hover:grid hover:bg-hover hover:text-accent">@</span>
  );
}

// 워크스페이스 메뉴 — 크롬의 프로필 메뉴처럼: 지금 계정의 워크스페이스 바꾸기 · 다른 프로필 · 마이페이지
function WorkspaceMenu({ S, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { listProfiles().then(setData); }, []);
  const mine = data?.profiles.find((p) => p.user === S.user)?.tenants || [];
  const go = async (slug) => {
    if (slug === S.tenant) { onClose(); return; }
    const r = await switchProfile(S.user, slug);
    if (r?.ok) onClose(); else setErr(r?.error || '바꾸지 못했어요');
  };
  return (
    <div onClick={(e) => e.stopPropagation()}
         className="absolute top-full right-2 left-2 z-20 mt-1 rounded-[14px] bg-card p-1.5 text-card-ink shadow-[0_10px_40px_rgb(0_0_0/.22)] ring-1 ring-card-line">
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
      <button onClick={() => { onClose(); showPicker(true); }} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-raise">
        <Icon name="people" className="size-4 text-card-sub" />다른 계정·워크스페이스…
      </button>
      <button onClick={() => { onClose(); openRoom({ kind: 'me' }); }} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] hover:bg-raise">
        <Icon name="person" className="size-4 text-card-sub" />마이페이지
      </button>
    </div>
  );
}

function Section({ id, title, action, children }) {
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(`sec:${id}`) !== '0'; } catch { return true; } });
  const toggle = () => setOpen((v) => { try { localStorage.setItem(`sec:${id}`, v ? '0' : '1'); } catch { /* 그만 */ } return !v; });
  return (
    <section className="group/sec mt-3">
      <h6 className="m-0 mb-[3px] flex h-[22px] items-center justify-between pr-1.5 pl-2.5 text-[11.5px] font-semibold text-sub">
        <button onClick={toggle} className="flex items-center gap-1 hover:text-ink">
          {title}
          <Icon name="fwd" className={`size-3 opacity-0 transition group-hover/sec:opacity-100 ${open ? 'rotate-90' : ''}`} />
        </button>
        {action && <span className="opacity-0 transition-opacity group-hover/sec:opacity-100">{action}</span>}
      </h6>
      {open && children}
    </section>
  );
}

function WallpaperPicker({ current, onClose }) {
  return (
    <div onClick={(e) => e.stopPropagation()}
         className="absolute right-2 bottom-[50px] left-2 z-10 rounded-[14px] bg-card p-3 text-card-ink shadow-[0_10px_40px_rgb(0_0_0/.22)] ring-1 ring-card-line">
      <div className="mb-2 ml-0.5 text-xs font-semibold text-card-sub">배경화면</div>
      <div className="grid grid-cols-3 gap-2">
        {WALLS.map((w) => (
          <button key={w.id} onClick={() => a2a.send('wallpaper:set', w.id)} title={w.name}
                  className={`wall-${w.id} relative h-[46px] rounded-[10px] shadow-[inset_0_0_0_1px_rgb(0_0_0/.08)]
                              ${current === w.id ? 'outline-2 outline-offset-2 outline-accent' : ''}`}>
            <span className={`absolute bottom-1 left-1.5 text-[10.5px] font-semibold ${w.tone === 'dark' ? 'text-white/80' : 'text-black/60'}`}>{w.name}</span>
          </button>
        ))}
      </div>
      <button onClick={() => { onClose(); a2a.send('wallpaper:pick'); }}
              className="mt-2.5 flex h-8 w-full items-center justify-center gap-1.5 rounded-[9px] bg-raise font-medium hover:brightness-95">
        <Icon name="image" />내 이미지 선택…
      </button>
    </div>
  );
}

export default function Sidebar({ glass }) {
  const S = useStore((s) => s.S);
  const channels = useStore((s) => s.channels);
  const room = useStore((s) => s.room);
  const me = useStore((s) => s.me);
  const [wallOpen, setWallOpen] = useState(false);
  const [wsOpen, setWsOpen] = useState(false);
  const [moreChats, setMoreChats] = useState(false);
  const [moreBm, setMoreBm] = useState(false);
  const [dragOver, setDragOver] = useState(null);     // 끌어 놓을 자리(탭 id)
  const [importMsg, setImportMsg] = useState(null);
  const importChrome = async () => {
    setImportMsg({ text: '크롬 즐겨찾기를 읽는 중…' });
    const r = await a2a.invoke('bookmarks:import').catch(() => null);
    setImportMsg(!r ? { text: '가져오지 못했어요', warn: true }
      : !r.ok ? { text: r.error, warn: true }
      : { text: r.added ? `${r.added}개를 가져왔어요${r.skipped ? ` (이미 있는 ${r.skipped}개 제외)` : ''}` : '새로 가져올 즐겨찾기가 없어요' });
    if (r?.added) setMoreBm(true);
    setTimeout(() => setImportMsg(null), 4000);
  };
  const BM_SHOW = 8;

  useEffect(() => {
    const close = () => { setWallOpen(false); setWsOpen(false); };
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, []);

  const inRoom = (kind, id) => S.panelOpen && room.kind === kind && room.id === id;
  const newTab = () => { a2a.send('tab:new'); setTimeout(() => document.getElementById('ask')?.focus(), 80); };
  const aiChats = S.tasks.filter((t) => !t.room);       // 채널에서 부른 작업은 그 채널 방에 있다
  const chats = aiChats.slice(0, moreChats ? 12 : 5);
  const working = new Set(S.tasks.filter((t) => t.room && t.status === 'working').map((t) => t.room.cid));

  return (
    // 사진 배경 위에선 사이드바 뒤에 반투명 유리를 깔아 글자가 사진에 묻히지 않게
    <aside className={`flex min-w-0 flex-col overflow-hidden ${glass ? 'bg-black/30 backdrop-blur-2xl backdrop-saturate-150' : ''}`}>
      <div className="drag flex h-[46px] shrink-0 items-center justify-end px-2.5">
        <IconButton name="sidebar" title="사이드바 접기 (⌘S)" onClick={() => a2a.send('sidebar:toggle')} />
      </div>

      <div className="relative flex items-center gap-1 pt-0.5 pr-3 pb-2.5 pl-1.5">
        {/* 누르면 워크스페이스 메뉴(바꾸기 · 다른 프로필 · 마이페이지) */}
        <button id="ws-button" onClick={(e) => { e.stopPropagation(); setWsOpen((v) => !v); }} title="워크스페이스 · 프로필"
                className={`flex min-w-0 flex-1 items-center gap-2.5 rounded-[10px] px-2 py-1.5 text-left ${wsOpen || inRoom('me', undefined) ? 'bg-sel shadow-sel' : 'hover:bg-hover'}`}>
          <Avatar id={me?.user_id || S.user} name={me?.display_name || S.user} size="ws" />
          <span className="flex min-w-0 flex-1 flex-col leading-tight">
            <b className="truncate text-[13.5px] font-semibold">{me?.display_name || S.user}</b>
            <span className="truncate text-[11.5px] text-sub">{S.tenantName || me?.tenant || S.tenant}</span>
          </span>
          <Icon name="chevronDown" className={`size-3.5 shrink-0 text-sub transition ${wsOpen ? 'rotate-180' : ''}`} />
        </button>
        {wsOpen && <WorkspaceMenu S={S} onClose={() => setWsOpen(false)} />}
        <IconButton name="compose" title="새 작업 (⌘T)" onClick={newTab} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 [&::-webkit-scrollbar]:w-0">
        <Section id="bm" title="즐겨찾기" action={(
          <span className="flex">
            <IconButton small name="folder" title="크롬에서 즐겨찾기 가져오기" onClick={importChrome} />
            <IconButton small name="plus" title="지금 페이지 추가 (⌘D)" onClick={() => a2a.send('bookmark:toggle')} />
          </span>
        )}>
          {importMsg && <div className={`px-2.5 py-1 text-[11.5px] ${importMsg.warn ? 'text-warn' : 'text-sub'}`}>{importMsg.text}</div>}
          {(moreBm ? S.bookmarks : S.bookmarks.slice(0, BM_SHOW)).map((b) => (
            <button key={b.url} title={b.url} className={rowCls(S.tabs.find((t) => t.active)?.url === b.url)} onClick={() => a2a.send('bookmark:open', b.url)}>
              <Favicon url={b.url} favicon={b.favicon} title={b.title} />
              <span className="min-w-0 flex-1 truncate">{b.title}</span>
              <CloseX title="즐겨찾기에서 빼기" onClick={() => a2a.send('bookmark:remove', b.url)} />
            </button>
          ))}
          {S.bookmarks.length > BM_SHOW && (
            <button className={`${rowCls(false)} text-sub`} onClick={() => setMoreBm((v) => !v)}>
              <span className="w-4" /><span className="flex-1">{moreBm ? '접기' : `더 보기 (${S.bookmarks.length - BM_SHOW})`}</span>
            </button>
          )}
          {!S.bookmarks.length && <div className="px-2.5 py-1.5 text-xs text-faint">⌘D로 지금 페이지를 추가하세요</div>}
        </Section>

        <Section id="chats" title="AI 채팅">
          <button className={`${rowCls(inRoom('ai', null))} text-sub`} onClick={newAiChat} disabled={S.busy}>
            <Icon name="compose" className="size-4" />
            <span className="min-w-0 flex-1 truncate">새 AI 채팅</span>
            <kbd className="font-sans text-[11px] text-faint opacity-0 group-hover:opacity-100">⌘J</kbd>
          </button>
          {chats.map((t) => (
            <button key={t.id} title={statusText(t)} onClick={() => openTask(t.id)}
                    className={rowCls(inRoom('ai', t.id))}>
              <span className={`mx-1 size-[7px] shrink-0 rounded-full ${STATUS_DOT[t.status]}`} />
              <span className="min-w-0 flex-1 truncate">{t.title}</span>
              <AtButton m={{ kind: 'task', id: t.id, label: t.title }} />
            </button>
          ))}
          {aiChats.length > 5 && (
            <button className={`${rowCls(false)} text-sub`} onClick={() => setMoreChats((v) => !v)}>
              <span className="w-4" /><span className="flex-1">{moreChats ? '접기' : '더 보기'}</span>
            </button>
          )}
        </Section>

        <Section id="ch" title="팀 채널">
          {channels === null && <div className="px-2.5 py-1.5 text-xs text-faint">불러오는 중…</div>}
          {channels?.length === 0 && <div className="px-2.5 py-1.5 text-xs text-faint">채널을 불러오지 못했습니다</div>}
          {channels?.slice(0, 10).map((c) => {
            const unread = !inRoom('channel', c.id) && c.unread > 0;
            return (
              <button key={c.id} className={rowCls(inRoom('channel', c.id))} onClick={() => openRoom({ kind: 'channel', id: c.id })}>
                <Icon name={c.visibility === 'private' ? 'lock' : c.kind === 'dm' ? 'person' : 'hash'} className="size-4 text-sub" />
                <span className={`min-w-0 flex-1 truncate ${unread ? 'font-semibold' : ''}`}>{c.name}</span>
                {working.has(c.id) && <Icon name="spark" className="size-3.5 animate-twinkle text-accent" />}
                {unread && (
                  <span className={`grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 text-[10.5px] font-bold tabular-nums
                                    ${c.mentions > 0 ? 'bg-accent text-white' : 'bg-sel text-ink'}`}>
                    {c.mentions > 0 ? `@${c.mentions}` : c.unread > 99 ? '99+' : c.unread}
                  </span>
                )}
                <AtButton m={{ kind: 'channel', id: c.name, label: c.name }} />
              </button>
            );
          })}
        </Section>

        <Section id="tabs" title="탭">
          <button className={`${rowCls(false)} text-sub`} onClick={newTab}>
            <Icon name="plus" className="size-4" />
            <span className="min-w-0 flex-1 truncate">새 탭</span>
            <kbd className="font-sans text-[11px] text-faint opacity-0 group-hover:opacity-100">⌘T</kbd>
          </button>
          {S.tabs.map((t) => (
            <button key={t.id} title={t.url || '새 탭'} draggable
                    className={`${rowCls(t.active)} ${dragOver === t.id ? 'shadow-[inset_0_2px_0_var(--color-accent)]' : ''}`}
                    onDragStart={(e) => { e.dataTransfer.setData('text/a2a-tab', String(t.id)); e.dataTransfer.effectAllowed = 'move'; }}
                    onDragOver={(e) => { if (e.dataTransfer.types.includes('text/a2a-tab')) { e.preventDefault(); setDragOver(t.id); } }}
                    onDragLeave={() => setDragOver((v) => (v === t.id ? null : v))}
                    onDrop={(e) => {
                      e.preventDefault();
                      setDragOver(null);
                      const id = Number(e.dataTransfer.getData('text/a2a-tab'));
                      if (id && id !== t.id) a2a.send('tab:move', id, S.tabs.findIndex((x) => x.id === t.id));
                    }}
                    onClick={() => a2a.send('tab:switch', t.id)}
                    onAuxClick={(e) => e.button === 1 && a2a.send('tab:close', t.id)}>
              <Favicon url={t.url} favicon={t.favicon} title={t.title} loading={t.loading} newtab={t.newtab && !t.loading} />
              <span className="min-w-0 flex-1 truncate">{t.newtab ? '새 탭' : t.title}</span>
              {t.agent && (
                <span className="flex items-center gap-0.5 text-[10.5px] font-semibold text-accent">
                  <Icon name="spark" className="size-3 animate-twinkle" />AI
                </span>
              )}
              {!t.newtab && <AtButton m={{ kind: 'tab', id: t.id, label: t.title }} />}
              <CloseX title="탭 닫기 (⌘W)" onClick={() => a2a.send('tab:close', t.id)} />
            </button>
          ))}
        </Section>

      </div>

      <div className="relative shrink-0 px-2 pt-1.5 pb-2.5">
        {wallOpen && <WallpaperPicker current={S.wallpaper} onClose={() => setWallOpen(false)} />}
        <button className={rowCls(inRoom('history', undefined))} onClick={() => (inRoom('history', undefined) ? a2a.send('panel:set', false) : openRoom({ kind: 'history' }))}>
          <Icon name="clock" className="size-4 text-sub" />
          <span className="min-w-0 flex-1 truncate">방문 기록</span>
          <kbd className="font-sans text-[11px] text-faint opacity-0 group-hover:opacity-100">⌘Y</kbd>
        </button>
        <button className={rowCls(inRoom('downloads', undefined))} onClick={() => (inRoom('downloads', undefined) ? a2a.send('panel:set', false) : openRoom({ kind: 'downloads' }))}>
          <Icon name="download" className="size-4 text-sub" />
          <span className="min-w-0 flex-1 truncate">다운로드</span>
          <kbd className="font-sans text-[11px] text-faint opacity-0 group-hover:opacity-100">⌥⌘L</kbd>
        </button>
        <button id="open-map" className={rowCls(S.tabs.find((t) => t.active)?.internal === 'map')} onClick={() => a2a.send('map:open')}>
          <Icon name="graph" className="size-4 text-sub" />
          <span className="min-w-0 flex-1 truncate">업무 지도</span>
          <kbd className="font-sans text-[11px] text-faint opacity-0 group-hover:opacity-100">⇧⌘G</kbd>
        </button>
        <button className={rowCls(false)} onClick={(e) => { e.stopPropagation(); setWallOpen((v) => !v); }}>
          <Icon name="palette" className="size-4 text-sub" />
          <span className="min-w-0 flex-1 truncate">배경화면</span>
        </button>
      </div>
    </aside>
  );
}
