// 좌우 폭 손잡이 — 경계선을 끌어 옆 칸의 폭을 바꾼다. 두 번 누르면 기본 폭으로.
// 웹 페이지는 셸 위에 얹힌 별도 화면이라, 끄는 동안 마우스를 놓치지 않게 웹 화면을 잠시 내린다(ui:overlay).
import { useRef } from 'react';
import { a2a } from './store.js';

export default function Resizer({ id, onDrag, onEnd, onReset, style, className = '', title = '끌어서 폭 조절 · 두 번 누르면 기본 폭' }) {
  const start = useRef(null);
  const down = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    start.current = e.clientX;
    e.currentTarget.setPointerCapture(e.pointerId);
    a2a.send('ui:overlay', true);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };
  const move = (e) => { if (start.current !== null) onDrag(e.clientX - start.current); };
  const up = (e) => {
    if (start.current === null) return;
    const dx = e.clientX - start.current;
    start.current = null;
    a2a.send('ui:overlay', false);
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    onEnd?.(dx);
  };
  return (
    <div id={id} role="separator" aria-orientation="vertical" title={title} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
         onDoubleClick={onReset}
         className={`group z-40 w-[7px] cursor-col-resize ${className}`} style={style}>
      <div className="mx-auto h-full w-px bg-transparent transition-colors group-hover:bg-sky-400/70" />
    </div>
  );
}
