// 카드 위 주소 줄 — 이동 버튼 · 주소창 · 즐겨찾기 · 에이전트/팀 채팅 토글
import { useEffect, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import { IconButton } from './Sidebar.jsx';
import { a2a, useStore, openRoom, openTeam } from './store.js';

function Pill({ icon, label, title, on, busy, onClick }) {
  return (
    <button title={title} onClick={onClick}
            className={`no-drag flex h-8 items-center gap-1.5 rounded-[10px] pr-3 pl-2.5 text-[12.5px] font-semibold backdrop-blur-xl
                        ${on ? 'bg-card text-card-ink shadow-sm ring-1 ring-card-line' : 'bg-glass text-ink ring-1 ring-glass-line ring-inset hover:bg-hover'}`}>
      <Icon name={icon} className={`size-4 ${busy ? 'animate-twinkle text-accent' : ''}`} />
      {label}
    </button>
  );
}

// 받는 중이면 진행 표시, 끝난 게 있으면 점 — 누르면 패널에 다운로드 목록
function DownloadsButton({ room, open, downloads }) {
  if (!downloads.length) return null;
  const live = downloads.filter((d) => d.state === 'progressing' || d.state === 'paused');
  const on = open && room.kind === 'downloads';
  return (
    <button title="다운로드" onClick={() => (on ? a2a.send('panel:set', false) : openRoom({ kind: 'downloads' }))}
            className={`no-drag relative grid size-8 place-items-center rounded-[10px] backdrop-blur-xl
                        ${on ? 'bg-card text-card-ink shadow-sm ring-1 ring-card-line' : 'bg-glass text-ink ring-1 ring-glass-line ring-inset hover:bg-hover'}`}>
      <Icon name="download" className={`size-4 ${live.length ? 'animate-pulse text-accent' : ''}`} />
      {live.length > 0 && <span className="absolute -top-1 -right-1 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-white">{live.length}</span>}
    </button>
  );
}

export default function TopBar() {
  const S = useStore((s) => s.S);
  const cur = S.tabs.find((t) => t.active);
  const isNew = !cur || cur.newtab;
  const [value, setValue] = useState('');
  const [editing, setEditing] = useState(false);
  const [typed, setTyped] = useState(false);       // 포커스 뒤 실제로 쳤을 때만 제안
  const [sugs, setSugs] = useState([]);
  const [sel, setSel] = useState(-1);
  const input = useRef(null);

  // 방문 기록·즐겨찾기 제안 — 잠깐 멈추면 묻는다
  useEffect(() => {
    if (!editing || !typed || !value.trim()) { setSugs([]); return undefined; }
    const t = setTimeout(() => a2a.invoke('history:suggest', value.trim()).then((r) => { setSugs(r || []); setSel(-1); }).catch(() => {}), 80);
    return () => clearTimeout(t);
  }, [value, editing, typed]);
  const open = editing && typed && !!value.trim() && sugs.length > 0;
  // 제안이 보이는 동안엔 웹페이지를 잠깐 내려 둔다(카드 자리를 셸이 쓴다)
  useEffect(() => { a2a.send('ui:overlay', open); }, [open]);
  useEffect(() => () => a2a.send('ui:overlay', false), []);
  const goTo = (target) => { a2a.send('nav:go', target); setTyped(false); setSugs([]); input.current.blur(); };

  useEffect(() => { if (!editing) setValue(isNew ? '' : cur.url); }, [cur?.url, isNew, editing]);
  useEffect(() => {
    const focus = () => { input.current?.focus(); input.current?.select(); };
    window.addEventListener('a2a:focus-url', focus);
    return () => window.removeEventListener('a2a:focus-url', focus);
  }, []);

  const urlIcon = isNew ? 'search' : cur.url.startsWith('https:') ? 'lock' : 'globe';
  const room = useStore((s) => s.room);
  const agentOn = S.panelOpen && room.kind === 'ai';
  const chatOn = S.panelOpen && room.kind === 'channel';

  return (
    <div className="drag flex h-[50px] shrink-0 items-center gap-2.5">
      <div className="flex items-center gap-0.5">
        {!S.sidebarOpen && <IconButton name="sidebar" title="사이드바 열기 (⌘S)" className="ml-[70px]" onClick={() => a2a.send('sidebar:toggle')} />}
        <IconButton name="back" title="뒤로" disabled={!cur?.canBack} onClick={() => a2a.send('nav:back')} />
        <IconButton name="fwd" title="앞으로" disabled={!cur?.canForward} onClick={() => a2a.send('nav:forward')} />
        <IconButton name="reload" title="새로고침 (⌘R)" disabled={isNew} onClick={() => a2a.send('nav:reload')} />
        <IconButton name="home" title="홈 (⇧⌘H)" disabled={isNew} onClick={() => a2a.send('nav:home')} />
      </div>

      <form onSubmit={(e) => { e.preventDefault(); goTo(sel >= 0 && sugs[sel] ? sugs[sel].url : value); }}
            className="no-drag relative z-20 mx-auto flex h-[34px] max-w-[760px] flex-1 items-center gap-2 rounded-[11px] bg-glass pr-1.5 pl-3
                       ring-1 ring-glass-line ring-inset backdrop-blur-xl backdrop-saturate-150 transition
                       focus-within:bg-card focus-within:text-card-ink focus-within:shadow-lg focus-within:ring-card-line">
        <Icon name={urlIcon} className="size-3.5 text-sub" />
        <input ref={input} value={value} spellCheck={false} autoComplete="off" placeholder="검색하거나 주소 입력"
               onChange={(e) => { setValue(e.target.value); setTyped(true); }}
               onFocus={(e) => { setEditing(true); setTyped(false); setTimeout(() => e.target.select(), 0); }}
               onBlur={() => { setEditing(false); setTyped(false); }}
               onKeyDown={(e) => {
                 if (e.key === 'Escape') { setValue(isNew ? '' : cur.url); e.target.blur(); }
                 if (open && e.key === 'ArrowDown') { e.preventDefault(); setSel((i) => Math.min(sugs.length - 1, i + 1)); }
                 if (open && e.key === 'ArrowUp') { e.preventDefault(); setSel((i) => Math.max(-1, i - 1)); }
               }}
               className="h-full min-w-0 flex-1 bg-transparent text-[13px] outline-none select-text placeholder:text-sub" />
        {!isNew && cur.zoom !== 100 && (
          <button type="button" onClick={() => a2a.send('zoom', 'reset')} title="원래 크기로 (⌘0)"
                  className="h-[22px] shrink-0 rounded-md bg-raise/70 px-1.5 text-[11px] font-semibold tabular-nums text-sub hover:text-ink">
            {cur.zoom}%
          </button>
        )}
        {!isNew && (
          <button type="button" onClick={() => a2a.send('bookmark:toggle')}
                  title={cur.bookmarked ? '즐겨찾기에서 빼기 (⌘D)' : '즐겨찾기 추가 (⌘D)'}
                  className={`grid size-[22px] place-items-center rounded-md hover:bg-hover ${cur.bookmarked ? 'text-[#e9a23b]' : 'text-sub'}`}>
            <Icon name="star" className="size-3.5" fill={cur.bookmarked ? 'currentColor' : 'none'} />
          </button>
        )}
        {open && (
          <div id="url-suggest" className="absolute top-[40px] right-0 left-0 z-30 overflow-hidden rounded-xl bg-card py-1 text-card-ink shadow-[0_14px_40px_rgb(0_0_0/.2)] ring-1 ring-card-line">
            <button type="button" onMouseDown={(e) => { e.preventDefault(); goTo(value); }}
                    className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] ${sel === -1 ? 'bg-raise' : ''}`}>
              <Icon name="search" className="size-3.5 text-card-faint" />
              <span className="truncate">{value}</span>
              <span className="ml-auto shrink-0 text-[11px] text-card-faint">Enter</span>
            </button>
            {sugs.map((s, i) => (
              <button key={s.url} type="button" title={s.url} onMouseEnter={() => setSel(i)}
                      onMouseDown={(e) => { e.preventDefault(); goTo(s.url); }}
                      className={`flex w-full items-center gap-2.5 px-3 py-2 text-left ${sel === i ? 'bg-raise' : ''}`}>
                <Icon name={s.bookmark ? 'star' : 'clock'} className="size-3.5 shrink-0 text-card-faint" />
                <span className="min-w-0 flex-1 truncate text-[13px]">{s.title || s.url}</span>
                <span className="max-w-[45%] shrink-0 truncate text-[11.5px] text-card-faint">{s.url.replace(/^https?:\/\/(www\.)?/, '')}</span>
              </button>
            ))}
          </div>
        )}
      </form>

      <div className="flex gap-1.5">
        <DownloadsButton room={room} open={S.panelOpen} downloads={S.downloads || []} />
        <Pill icon="spark" label="AI" title="AI 채팅 (⌘J)" on={agentOn} busy={S.busy}
              onClick={() => (agentOn ? a2a.send('panel:set', false) : openRoom({ kind: 'ai', id: S.currentTaskId ?? room.id ?? null }))} />
        <Pill icon="people" label="팀" title="팀 채널 (⌘K)" on={chatOn}
              onClick={() => (chatOn ? a2a.send('panel:set', false) : openTeam())} />
      </div>
    </div>
  );
}
