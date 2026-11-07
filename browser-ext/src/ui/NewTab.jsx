// 새 탭 = AI 작업 시작 화면 — 맡기기/검색 입력창 · 추천 · 최근 작업 카드
import { useEffect, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import { a2a, useStore, runAgentFresh, openTask } from './store.js';
import { SUGS_NEW, STEP, STATUS_DOT, brief, statusText } from './format.js';
import { useNow } from './useNow.js';
import { MentionChips, useMentionPicker } from './Mentions.jsx';

export function Mark({ small }) {
  return (
    <div className={`grid place-items-center bg-gradient-to-br from-[#ff8a5c] via-accent to-[#e2557b] text-white shadow-[0_8px_22px_rgb(255_107_69/.35)]
                     ${small ? 'size-[38px] rounded-xl' : 'size-[46px] rounded-[14px]'}`}>
      <Icon name="logo" className={small ? 'size-[21px]' : 'size-[26px]'} strokeWidth={1.9} />
    </div>
  );
}

export function Suggestion({ text, onPick, block }) {
  return (
    <button onClick={() => onPick(text)}
            className={`flex items-center gap-1.5 bg-card text-[12.5px] text-card-sub ring-1 ring-card-line ring-inset hover:bg-raise hover:text-card-ink
                        ${block ? 'rounded-[10px] px-3 py-2 text-left' : 'h-8 rounded-full px-3.5'}`}>
      <Icon name="spark" className="size-3.5 text-accent" />{text}
    </button>
  );
}

export function TaskCard({ t, now }) {
  const showSteps = t.status === 'working' || !t.summary;
  return (
    <button onClick={() => openTask(t.id)}
            className="flex min-h-[168px] flex-col overflow-hidden rounded-[14px] bg-field px-3 pt-3 text-left text-card-ink
                       shadow-[0_2px_8px_rgb(14_30_48/.04)] ring-1 ring-card-line transition hover:-translate-y-0.5 hover:shadow-[0_10px_26px_rgb(14_30_48/.1)]">
      <div className="flex items-center gap-1.5 text-[11.5px] text-card-faint">
        <span className={`size-1.5 rounded-full ${STATUS_DOT[t.status]}`} />{statusText(t, now)}
      </div>
      <div className="mt-1 mb-2 line-clamp-2 text-[13.5px] font-semibold">{t.title}</div>
      {showSteps ? (
        <div className="mb-2.5 flex flex-col gap-[3px]">
          {t.steps.slice(-3).map((s, i) => {
            const [ic, label] = STEP[s.name] || ['spark', s.name];
            return (
              <div key={i} className="flex items-center gap-1.5 truncate text-xs text-card-sub">
                <Icon name={ic} className="size-[13px] text-card-faint" />
                <span className="truncate">{label} {brief(s.name, s.input)}</span>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="mb-2.5 line-clamp-3 text-xs text-card-sub">{t.summary.replace(/[*#`|]/g, ' ').replace(/\s+/g, ' ')}</div>
      )}
      {t.thumb && (
        <div className="-mx-3 mt-auto h-[92px] border-t border-card-line bg-cover bg-top"
             style={{ backgroundImage: `url("${t.thumb}")` }} />
      )}
    </button>
  );
}

export default function NewTab() {
  const S = useStore((s) => s.S);
  const [mode, setMode] = useState(() => { try { return localStorage.getItem('askMode') || 'ai'; } catch { return 'ai'; } });
  const [text, setText] = useState('');
  const ta = useRef(null);
  const now = useNow(S.tasks.some((t) => t.status === 'working'));
  const mention = useMentionPicker(mode === 'ai' ? text : '', setText, 'newtab');

  useEffect(() => { try { localStorage.setItem('askMode', mode); } catch { /* 저장 안 돼도 그만 */ } }, [mode]);
  useEffect(() => {
    const t = ta.current;
    t.style.height = 'auto';
    t.style.height = Math.min(t.scrollHeight, 200) + 'px';
  }, [text]);
  useEffect(() => { ta.current?.focus(); }, []);

  const submit = () => {
    const v = text.trim();
    if (!v) return;
    if (mode === 'ai') { if (S.busy) return; runAgentFresh(v); } else a2a.send('nav:go', v);
    setText('');
  };

  return (
    <div id="newtab" className="absolute inset-0 overflow-y-auto">
      <div className="mx-auto flex max-w-[760px] flex-col items-center px-7 pt-[13vh] pb-12">
        <Mark />
        <h1 className="mt-[18px] mb-1.5 text-[30px] font-bold tracking-tight text-ink [text-shadow:0_1px_16px_rgb(0_0_0/.18)]">무엇을 맡길까요?</h1>
        <p className="mb-[26px] text-sm text-ink/80 [text-shadow:0_1px_10px_rgb(0_0_0/.2)]">찾고, 읽고, 정리해서 팀 채널에 올리는 일까지 — 브라우저가 대신합니다.</p>

        <div className="relative w-full">
        <form onSubmit={(e) => { e.preventDefault(); submit(); }}
              className="w-full rounded-[18px] bg-field pt-3.5 pr-3.5 pb-2.5 pl-[18px] shadow-[0_10px_30px_rgb(14_30_48/.08)] ring-1 ring-field-line
                         transition focus-within:ring-[1.5px] focus-within:ring-accent">
          {mode === 'ai' && <MentionChips />}
          <textarea id="ask" ref={ta} rows={1} value={text} onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (mention.onKeyDown(e)) return; if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }}
                    placeholder={mode === 'ai' ? '일을 맡기세요 · @로 탭·지난 채팅·채널을 붙일 수 있어요' : '검색어나 주소를 입력하세요'}
                    className="max-h-[200px] w-full resize-none bg-transparent text-base leading-normal text-card-ink outline-none select-text placeholder:text-card-faint" />
          <div className="mt-1.5 flex items-center gap-2.5">
            <div className="flex flex-1 flex-wrap gap-1.5">
              <span title="제출·전송·결제·삭제, 채널 게시는 실행 전에 확인합니다"
                    className="inline-flex h-[26px] items-center gap-1 rounded-lg bg-raise px-2.5 text-xs text-card-sub">
                <Icon name="shield" className="size-[13px]" />위험 동작은 확인 후 실행
              </span>
              <span className="inline-flex h-[26px] items-center gap-1 rounded-lg bg-raise px-2.5 text-xs text-card-sub">
                <Icon name="spark" className="size-[13px]" />Opus 5.5
              </span>
            </div>
            <div className="flex rounded-[9px] bg-raise p-0.5">
              {[['search', '검색'], ['ai', 'AI에게']].map(([m, label]) => (
                <button key={m} type="button" onClick={() => { setMode(m); ta.current.focus(); }}
                        className={`h-[26px] rounded-[7px] px-3 text-[12.5px] font-semibold
                                    ${mode === m ? 'bg-field text-card-ink shadow-sm ring-1 ring-field-line' : 'text-card-sub'}`}>
                  {label}
                </button>
              ))}
            </div>
            <button type="submit" disabled={mode === 'ai' && S.busy} title="보내기 (Enter)"
                    className="grid size-8 place-items-center rounded-[10px] bg-accent text-white shadow-[0_4px_12px_rgb(255_107_69/.3)]
                               disabled:bg-raise disabled:text-card-faint disabled:shadow-none">
              <Icon name="arrowUp" className="size-[17px]" strokeWidth={2} />
            </button>
          </div>
        </form>
        {mention.picker && <div className="absolute top-full right-0 left-0 mt-2">{mention.picker}</div>}
        </div>

        {!S.hasKey && <div className="mt-3 text-xs text-warn">ANTHROPIC_API_KEY가 없습니다 — A2A/.env에 넣거나 환경변수로 실행하세요.</div>}

        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {SUGS_NEW.map((s) => <Suggestion key={s} text={s} onPick={(v) => { setMode('ai'); runAgentFresh(v); }} />)}
        </div>

        <div className="mt-[54px] w-full">
          <h2 className="mb-3 ml-0.5 text-[15px] font-semibold text-ink [text-shadow:0_1px_10px_rgb(0_0_0/.2)]">최근 작업</h2>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(210px,1fr))] gap-3">
            {S.tasks.length === 0 && (
              <div className="col-span-full rounded-[14px] border-[1.5px] border-dashed border-glass-line bg-glass p-[22px] text-center text-[12.5px] text-ink/80 backdrop-blur-md">
                아직 맡긴 일이 없어요. 위에서 첫 작업을 맡겨 보세요.
              </div>
            )}
            {S.tasks.slice(0, 6).map((t) => <TaskCard key={t.id} t={t} now={now} />)}
          </div>
        </div>
      </div>
    </div>
  );
}
