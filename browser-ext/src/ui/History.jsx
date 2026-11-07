// 방문 기록 — 검색, 날짜별 묶음, 한 줄 지우기, 지난 1시간/오늘/전체 지우기. 누르면 새 탭으로 연다.
import { useEffect, useState } from 'react';
import Icon from './Icon.jsx';
import { a2a } from './store.js';
import { Favicon } from './Favicon.jsx';
import { dayOf, timeOf } from './format.js';

export default function History() {
  const [q, setQ] = useState('');
  const [rows, setRows] = useState(null);
  const [armed, setArmed] = useState(null);       // 지우기 확인 대기
  const load = (s = q) => a2a.invoke('history:query', s).then(setRows).catch(() => setRows([]));
  useEffect(() => { const t = setTimeout(() => load(q), 120); return () => clearTimeout(t); }, [q]);

  const clear = (range) => {
    if (armed !== range) { setArmed(range); setTimeout(() => setArmed((a) => (a === range ? null : a)), 3000); return; }
    a2a.send('history:clear', range);
    setArmed(null);
    setTimeout(() => load(), 150);
  };

  let lastDay = null;
  return (
    <div className="flex flex-col">
      <div className="sticky top-0 z-[2] bg-card px-3 pt-3 pb-2">
        <div className="flex h-9 items-center gap-2 rounded-lg bg-field px-3 ring-1 ring-field-line focus-within:ring-accent">
          <Icon name="search" className="size-3.5 text-card-faint" />
          <input id="history-q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="제목이나 주소로 찾기"
                 className="h-full min-w-0 flex-1 bg-transparent text-[13px] outline-none select-text placeholder:text-card-faint" />
        </div>
        <div className="mt-2 flex gap-1">
          {[['hour', '지난 1시간'], ['day', '오늘'], ['all', '전체']].map(([r, label]) => (
            <button key={r} onClick={() => clear(r)}
                    className={`h-7 rounded-md px-2 text-[11.5px] font-semibold ${armed === r ? 'bg-warn/15 text-warn' : 'text-card-sub hover:bg-raise'}`}>
              {armed === r ? `${label} 지우기?` : `${label} 지우기`}
            </button>
          ))}
        </div>
      </div>
      {rows === null && <div className="p-6 text-center text-xs text-card-faint">불러오는 중…</div>}
      {rows?.length === 0 && <div className="p-6 text-center text-[12.5px] text-card-faint">{q ? '찾는 기록이 없어요' : '방문 기록이 없어요'}</div>}
      <div className="px-1.5 pb-3">
        {rows?.map((r) => {
          const day = dayOf(r.at);
          const head = day !== lastDay ? (lastDay = day) : null;
          return (
            <div key={r.url}>
              {head && <div className="px-2 pt-3 pb-1 text-[11px] font-semibold text-card-faint">{head}</div>}
              <div role="button" tabIndex={0} title={r.url} onClick={() => a2a.send('tab:new', r.url)}
                   className="group flex items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-raise">
                <Favicon url={r.url} title={r.title} />
                <div className="min-w-0 flex-1 leading-tight">
                  <div className="truncate text-[13px]">{r.title || r.url}</div>
                  <div className="truncate text-[11px] text-card-faint">{r.url.replace(/^https?:\/\/(www\.)?/, '')}</div>
                </div>
                {r.agent && <span className="flex shrink-0 items-center gap-0.5 text-[10.5px] font-semibold text-accent"><Icon name="spark" className="size-3" />AI</span>}
                <span className="shrink-0 text-[11px] text-card-faint tabular-nums">{timeOf(r.at)}</span>
                <button title="기록에서 지우기" onClick={(e) => { e.stopPropagation(); a2a.send('history:remove', r.url); setRows((x) => x.filter((y) => y.url !== r.url)); }}
                        className="hidden size-6 shrink-0 place-items-center rounded-md text-card-sub group-hover:grid hover:bg-card">
                  <Icon name="x" className="size-3" />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
