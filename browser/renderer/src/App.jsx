// 셸 — 배경화면 위에 사이드바 | 주소 줄 + 페이지 카드 | 패널.
// 카드·채팅 자리의 화면 좌표를 메인에 알리면, 메인이 그 자리에 실제 웹뷰를 얹는다.
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import FindBar from './FindBar.jsx';
import InfoBar from './InfoBar.jsx';
import ProfilePicker from './ProfilePicker.jsx';
import Sidebar from './Sidebar.jsx';
import TopBar from './TopBar.jsx';
import NewTab from './NewTab.jsx';
import WorkMap from './WorkMap.jsx';
import Merry from './Merry.jsx';
import Learn from './Learn.jsx';
import RoomPanel from './Room.jsx';
import Resizer from './Resizer.jsx';
import { a2a, useStore, get, openRoom, openTeam } from './store.js';
import { WALLS } from './format.js';

function useReportRects(card) {
  const last = useRef('');
  const report = () => {
    const r = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return { x: b.left, y: b.top, width: b.width, height: b.height }; };
    const rects = { card: r(card.current) };
    const key = JSON.stringify(rects);
    if (key !== last.current) { last.current = key; a2a.send('ui:rects', rects); }
  };
  useLayoutEffect(report);                       // 매 렌더 뒤(사이드바·패널 여닫기)
  useEffect(() => {
    const ro = new ResizeObserver(report);
    if (card.current) ro.observe(card.current);
    window.addEventListener('resize', report);
    return () => { ro.disconnect(); window.removeEventListener('resize', report); };
  }, []);
}

function Wallpaper({ name }) {
  const custom = useStore((s) => s.custom);
  const preset = WALLS.find((w) => w.id === name) || WALLS[0];
  const isCustom = name === 'custom' && custom.img;
  const tone = isCustom ? custom.tone : preset.tone;
  useEffect(() => { document.body.dataset.tone = tone; }, [tone]);
  if (isCustom) return <div className="fixed inset-0 bg-cover bg-center" style={{ backgroundImage: `url("${custom.img}")` }} />;
  return <div className={`fixed inset-0 wall-${preset.id}`} />;
}

export default function App() {
  const S = useStore((s) => s.S);
  const card = useRef(null);
  useReportRects(card);
  const [findOpen, setFindOpen] = useState(false);
  const [drag, setDrag] = useState(null);           // 끄는 중의 폭 { sidebar | panel }
  const activeTabId = useStore((s) => s.S?.tabs.find((t) => t.active)?.id);
  useEffect(() => { setFindOpen(false); }, [activeTabId]);      // 탭을 바꾸면 찾기 바를 닫는다

  useEffect(() => {
    a2a.on('cmd', (name) => {
      if (name === 'focus-url') window.dispatchEvent(new Event('a2a:focus-url'));
      if (name === 'history' || name === 'downloads') {
        const { room, S: st } = get();
        if (st.panelOpen && room.kind === name) a2a.send('panel:set', false); else openRoom({ kind: name });
      }
      if (name === 'find') {
        const cur = get().S?.tabs.find((t) => t.active);
        if (cur && !cur.newtab && !cur.internal) { setFindOpen(true); setTimeout(() => window.dispatchEvent(new Event('a2a:find')), 30); }
      }
      if (name === 'focus-ask') setTimeout(() => document.getElementById('ask')?.focus(), 80);
      if (name === 'room-ai') { const { room, S: st } = get(); if (st.panelOpen && room.kind === 'ai') a2a.send('panel:set', false); else openRoom({ kind: 'ai', id: st.currentTaskId ?? room.id ?? null }); }
      if (name === 'room-team') { const { room, S: st } = get(); if (st.panelOpen && room.kind === 'channel') a2a.send('panel:set', false); else openTeam(); }
    });
  }, []);

  if (!S) return <div className="fixed inset-0 wall-sky" />;
  const cur = S.tabs.find((t) => t.active);
  // 좌우 폭 — 끌어서 조절(설정에 저장). 끄는 동안엔 화면에서만 바꾸고 놓을 때 저장
  const SW = { def: 256, min: 200, max: 440 }, PW = { def: 408, min: 320, max: 760 };
  const clamp = (v, r) => Math.round(Math.min(r.max, Math.max(r.min, v)));
  const sideW = drag?.sidebar ?? S.prefs?.sidebarWidth ?? SW.def;
  const panelW = drag?.panel ?? S.prefs?.panelWidth ?? PW.def;
  const cols = `${S.sidebarOpen ? sideW : 0}px minmax(0,1fr) ${S.panelOpen ? panelW : 0}px`;
  const base = { sidebar: S.prefs?.sidebarWidth ?? SW.def, panel: S.prefs?.panelWidth ?? PW.def };

  return (
    <>
      <Wallpaper name={S.wallpaper} />
      <div className="fixed inset-0 grid" style={{ gridTemplateColumns: cols }}>
        {S.sidebarOpen ? <Sidebar glass={S.wallpaper === 'custom'} /> : <div />}
        <main className={`flex min-w-0 flex-col pb-2.5 ${S.sidebarOpen ? '' : 'pl-2.5'} ${S.panelOpen ? '' : 'pr-2.5'}`}>
          <TopBar />
          {findOpen && !cur?.newtab && !cur?.internal && <FindBar onClose={() => setFindOpen(false)} />}
          {!cur?.newtab && !cur?.internal && <InfoBar />}
          {/* 홈(새 탭)에선 카드가 투명해져 배경화면이 그대로 보인다 */}
          <div id="card" ref={card} className={`relative min-h-0 flex-1 overflow-hidden rounded-[14px]
                                                 ${cur?.newtab ? '' : 'bg-card text-card-ink shadow-card'}`}>
            {cur?.newtab && <NewTab />}
            {cur?.internal === 'map' && <WorkMap />}
            {cur?.internal === 'merry' && <Merry />}
            {cur?.internal === 'learn' && <Learn />}
          </div>
        </main>
        {S.panelOpen ? <RoomPanel /> : <div />}
      </div>
      {S.sidebarOpen && (
        <Resizer id="resize-sidebar" className="fixed bottom-0 top-0" style={{ left: sideW - 4 }}
                 onDrag={(dx) => setDrag({ sidebar: clamp(base.sidebar + dx, SW) })}
                 onEnd={(dx) => { a2a.send('prefs:set', 'sidebarWidth', clamp(base.sidebar + dx, SW)); setDrag(null); }}
                 onReset={() => a2a.send('prefs:set', 'sidebarWidth', SW.def)} />
      )}
      {S.panelOpen && (
        <Resizer id="resize-panel" className="fixed bottom-0 top-0" style={{ right: panelW - 4 }}
                 onDrag={(dx) => setDrag({ panel: clamp(base.panel - dx, PW) })}
                 onEnd={(dx) => { a2a.send('prefs:set', 'panelWidth', clamp(base.panel - dx, PW)); setDrag(null); }}
                 onReset={() => a2a.send('prefs:set', 'panelWidth', PW.def)} />
      )}
      {S.pickProfile && <ProfilePicker />}
    </>
  );
}
