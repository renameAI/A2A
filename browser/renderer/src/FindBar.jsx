// 페이지 내 찾기(⌘F) — 주소 줄과 카드 사이 한 줄. 카드 안에 띄우면 웹페이지가 덮어 버린다.
import { useEffect, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import { a2a } from './store.js';

export default function FindBar({ onClose }) {
  const [text, setText] = useState('');
  const [res, setRes] = useState(null);       // { active, total }
  const input = useRef(null);

  useEffect(() => {
    input.current?.focus();
    const off = a2a.on('find:result', (r) => setRes(r));
    const refocus = () => { input.current?.focus(); input.current?.select(); };
    window.addEventListener('a2a:find', refocus);
    return () => { off?.(); window.removeEventListener('a2a:find', refocus); a2a.send('find:stop'); };
  }, []);

  const go = (opts = {}) => { if (text) a2a.send('find:go', text, opts); };
  const close = () => { a2a.send('find:stop'); onClose(); };

  return (
    <div className="no-drag mb-2 flex h-9 shrink-0 items-center gap-1.5 self-end rounded-[11px] bg-card pr-1 pl-3 text-card-ink shadow-md ring-1 ring-card-line">
      <Icon name="search" className="size-3.5 text-card-faint" />
      <input id="find" ref={input} value={text} spellCheck={false} placeholder="페이지에서 찾기"
             onChange={(e) => { setText(e.target.value); setRes(null); if (e.target.value) a2a.send('find:go', e.target.value, {}); else a2a.send('find:stop'); }}
             onKeyDown={(e) => {
               if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); go({ next: true, forward: !e.shiftKey }); }
               if (e.key === 'Escape') { e.preventDefault(); close(); }
             }}
             className="h-full w-56 bg-transparent text-[13px] outline-none select-text placeholder:text-card-faint" />
      <span className="w-14 text-right text-[11.5px] tabular-nums text-card-faint">
        {text && res ? (res.total ? `${res.active}/${res.total}` : '없음') : ''}
      </span>
      <button title="이전 (⇧Enter)" onClick={() => go({ next: true, forward: false })} className="grid size-7 place-items-center rounded-md text-card-sub hover:bg-raise">
        <Icon name="chevronDown" className="size-3.5 rotate-180" />
      </button>
      <button title="다음 (Enter)" onClick={() => go({ next: true, forward: true })} className="grid size-7 place-items-center rounded-md text-card-sub hover:bg-raise">
        <Icon name="chevronDown" className="size-3.5" />
      </button>
      <button title="닫기 (Esc)" onClick={close} className="grid size-7 place-items-center rounded-md text-card-sub hover:bg-raise">
        <Icon name="x" className="size-3.5" />
      </button>
    </div>
  );
}
