// 다운로드 목록 — 진행률, 일시정지/재개/취소, Finder에서 보기, 열기(실행 파일 종류는 열지 않는다)
import Icon from './Icon.jsx';
import { a2a, useStore } from './store.js';
import { ago } from './format.js';

const size = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)}GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}MB` : n >= 1e3 ? `${Math.round(n / 1e3)}KB` : `${n || 0}B`);
const act = (id, what) => a2a.send('dl:action', id, what);
const STATE = { completed: '완료', cancelled: '취소됨', interrupted: '실패', paused: '일시정지', progressing: '받는 중' };

function Btn({ onClick, title, icon, children }) {
  return (
    <button onClick={onClick} title={title}
            className="flex h-7 items-center gap-1 rounded-md px-2 text-[11.5px] font-semibold text-card-sub hover:bg-card hover:text-card-ink">
      {icon && <Icon name={icon} className="size-3.5" />}{children}
    </button>
  );
}

export default function Downloads() {
  const list = useStore((s) => s.S.downloads || []);
  if (!list.length) {
    return <div className="px-6 pt-[14vh] text-center text-[12.5px] text-card-faint">받은 파일이 없어요.<br />웹에서 내려받으면 여기에 쌓입니다.</div>;
  }
  return (
    <div className="flex flex-col gap-1 p-2">
      <div className="flex justify-end px-1"><Btn onClick={() => act('', 'clear')}>끝난 항목 지우기</Btn></div>
      {list.map((d) => {
        const live = d.state === 'progressing' || d.state === 'paused';
        const pct = d.total > 0 ? Math.min(100, Math.round((d.received / d.total) * 100)) : null;
        return (
          <div key={d.id} className="rounded-xl px-3 py-2.5 hover:bg-raise">
            <div className="flex items-center gap-2.5">
              <span className={`grid size-8 shrink-0 place-items-center rounded-lg ${d.state === 'completed' ? 'bg-ok/12 text-ok' : d.state === 'interrupted' ? 'bg-warn/12 text-warn' : 'bg-raise text-card-sub'}`}>
                <Icon name={d.state === 'completed' ? 'check' : d.state === 'interrupted' ? 'alert' : 'download'} className="size-4" />
              </span>
              <div className="min-w-0 flex-1 leading-tight">
                <div className={`truncate text-[13px] font-medium ${d.state === 'cancelled' || !d.exists ? 'text-card-faint line-through' : ''}`}>{d.filename}</div>
                <div className="truncate text-[11.5px] text-card-faint">
                  {STATE[d.state]}{live ? ` · ${size(d.received)}${d.total ? ` / ${size(d.total)}` : ''}` : d.state === 'completed' ? ` · ${size(d.received)}` : ''}
                  {' · '}{ago(d.ended || d.started)}{!d.exists && d.state === 'completed' ? ' · 파일이 옮겨졌거나 지워짐' : ''}
                </div>
              </div>
            </div>
            {live && (
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-raise">
                <div className={`h-full rounded-full bg-accent ${pct === null ? 'w-1/3 animate-pulse' : ''}`} style={pct === null ? undefined : { width: `${pct}%` }} />
              </div>
            )}
            <div className="mt-1.5 flex flex-wrap gap-0.5 pl-10">
              {d.state === 'progressing' && <Btn onClick={() => act(d.id, 'pause')}>일시정지</Btn>}
              {d.state === 'paused' && <Btn onClick={() => act(d.id, 'resume')}>계속</Btn>}
              {live && <Btn onClick={() => act(d.id, 'cancel')}>취소</Btn>}
              {d.state === 'completed' && d.exists && !d.risky && <Btn icon="external" onClick={() => act(d.id, 'open')}>열기</Btn>}
              {d.state === 'completed' && d.exists && <Btn icon="folder" onClick={() => act(d.id, 'reveal')}>Finder에서 보기</Btn>}
              {d.risky && d.state === 'completed' && <span className="self-center px-1 text-[11px] text-warn">실행 파일이라 바로 열지 않아요</span>}
              {!live && <Btn onClick={() => act(d.id, 'remove')}>목록에서 지우기</Btn>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
