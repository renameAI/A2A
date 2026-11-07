// 학습 모드 — 링크 하나로 학습 틀(learn/index.js). 즐겨찾기 "학습"(a2a://학습)
//   왼쪽: 새 학습(링크) · 과정 · 장(진도) · 오늘 복습
//   가운데: 장 학습 카드 — 읽기 전 질문 → 원문(옆 탭) → 요약·용어 → 우리 코드 → 인출 연습 → 과제 → 메모
//   오른쪽: 튜터(질문 · 설명 · 소크라테스 · 과제 검토)
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import Icon from './Icon.jsx';
import Resizer from './Resizer.jsx';
import { a2a } from './store.js';

const call = (name, ...args) => a2a.invoke('learn:call', name, ...args);
const errText = (e) => String(e?.message || e).replace(/^Error invoking remote method '[^']+': (Error: )?/, '');
const STATUS = { todo: ['읽기 전', 'bg-card-line'], reading: ['읽는 중', 'bg-warn'], done: ['완료', 'bg-ok'], skip: ['건너뜀', 'bg-card-line/40'] };
const VERDICT = { correct: ['맞음', 'text-ok'], partial: ['일부', 'text-warn'], wrong: ['다시', 'text-accent'] };

function Btn({ children, onClick, primary, disabled, small, title }) {
  return (
    <button title={title} disabled={disabled} onClick={onClick}
            className={`inline-flex ${small ? 'h-6 px-2 text-[11px]' : 'h-7 px-2.5 text-xs'} items-center gap-1.5 rounded-lg font-semibold disabled:opacity-40 ${primary ? 'bg-card-ink text-card' : 'ring-1 ring-card-line hover:bg-raise'}`}>
      {children}
    </button>
  );
}
const Section = ({ title, hint, children }) => (
  <section className="mb-6">
    <div className="mb-2 flex items-baseline gap-2"><h3 className="text-[13.5px] font-bold">{title}</h3>{hint && <span className="text-[11.5px] text-card-faint">{hint}</span>}</div>
    {children}
  </section>
);

function useWidth(key, def, min, max) {
  const read = () => { try { const v = Number(localStorage.getItem(key)); return Number.isFinite(v) && v >= min && v <= max ? v : def; } catch { return def; } };
  const [w, setW] = useState(read);
  const [live, setLive] = useState(null);
  const clamp = (v) => Math.round(Math.min(max, Math.max(min, v)));
  return { w: live ?? w, drag: (dx, dir = 1) => setLive(clamp(w + dir * dx)), end: (dx, dir = 1) => { const v = clamp(w + dir * dx); setW(v); setLive(null); try { localStorage.setItem(key, String(v)); } catch { /* 이번만 */ } }, reset: () => { setW(def); setLive(null); } };
}

// 코드 열람 — 연결한 프로젝트의 실제 줄
function CodeView({ anchor, onClose }) {
  const [k, setK] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => { setK(null); setErr(''); call('code', anchor.file, anchor.find).then(setK).catch((e) => setErr(errText(e))); }, [anchor.file, anchor.find]);
  return (
    <div className="mt-2 overflow-hidden rounded-xl ring-1 ring-card-line">
      <div className="flex items-center gap-2 border-b border-card-line bg-raise px-3 py-1.5 text-[11.5px]">
        <span className="font-mono font-semibold">{anchor.file}</span><span className="text-card-faint">· {anchor.find}{k ? ` · ${k.line}행` : ''}</span>
        <button className="ml-auto text-card-faint hover:text-card-ink" onClick={onClose}>닫기</button>
      </div>
      {err ? <div className="p-3 text-[12px] text-accent">{err}</div> : !k ? <div className="p-3 text-[12px] text-card-faint">불러오는 중…</div> : (
        <pre className="max-h-[360px] overflow-auto bg-field p-0 text-[11.5px] leading-[1.55]">
          {k.text.split('\n').map((l, i) => (
            <div key={i} className={`flex ${k.from + i === k.line ? 'bg-warn/15' : ''}`}>
              <span className="w-12 shrink-0 select-none pr-3 text-right text-card-faint">{k.from + i}</span>
              <code className="whitespace-pre pr-4 font-mono">{l}</code>
            </div>
          ))}
        </pre>
      )}
    </div>
  );
}

// 인출 연습 카드 하나 — 답을 쓰면 채점(맞으면 상자 한 칸 위, 틀리면 처음으로)
function QuizCard({ c, q }) {
  const [text, setText] = useState('');
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const go = async (t) => { setBusy(true); setErr(''); try { setRes(await call('answer', c.id, q.id, t)); } catch (e) { setErr(errText(e)); } finally { setBusy(false); } };
  const last = q.history?.at(-1);
  return (
    <div className="rounded-xl p-3 ring-1 ring-card-line">
      <div className="mb-1 flex items-center gap-2 text-[11px] text-card-faint"><span className="rounded bg-raise px-1.5 py-0.5">{q.kind}</span><span>상자 {q.box + 1}/5</span>{last && <span className={VERDICT[last.verdict]?.[1]}>지난번 {VERDICT[last.verdict]?.[0]}</span>}</div>
      <div className="text-[13.5px] font-semibold leading-relaxed">{q.q}</div>
      {!res ? (
        <>
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="내 말로 답해 보세요 — 모르면 '모름'도 괜찮아요"
                    className="mt-2 w-full rounded-lg bg-field px-2.5 py-2 text-[13px] ring-1 ring-field-line outline-none focus:ring-card-faint" />
          <div className="mt-1.5 flex gap-1.5"><Btn primary small disabled={busy} onClick={() => go(text)}>{busy ? '채점 중…' : '채점'}</Btn><Btn small disabled={busy} onClick={() => go('')}>모르겠어요</Btn></div>
        </>
      ) : (
        <div className="mt-2 space-y-1.5 text-[12.5px] leading-relaxed">
          <div className={`font-bold ${VERDICT[res.verdict][1]}`}>{VERDICT[res.verdict][0]}</div>
          <div>{res.feedback}</div>
          <details className="text-card-sub"><summary className="cursor-pointer text-[11.5px]">모범 답 보기</summary><div className="mt-1 border-l-2 border-card-line pl-2">{res.model}</div></details>
          {res.followUp && <div className="text-card-sub">더 생각해 보기: {res.followUp}</div>}
          <div className="text-[11px] text-card-faint">다음 복습: {new Date(res.due).toLocaleDateString('ko-KR')}</div>
        </div>
      )}
      {err && <div className="mt-1 text-[11.5px] text-accent">{err}</div>}
    </div>
  );
}

function Tutor({ c, ch, w }) {
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState('');
  const [mode, setMode] = useState('chat');
  const [live, setLive] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const load = useCallback(() => call('chat', c.id, ch.id).then(setMsgs).catch(() => {}), [c.id, ch.id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => a2a.on('learn:progress', (p) => { if (p?.chid === ch.id && p.type === 'reply') setLive(p.text || ''); }), [ch.id]);
  const send = async (t, m = mode) => {
    t = t.trim(); if (!t) return;
    setText(''); setBusy(true); setErr(''); setMsgs((x) => [...x, { id: `tmp${Date.now()}`, role: 'user', text: t, mode: m }]);
    try { await call('tutor', c.id, ch.id, t, { mode: m }); } catch (e) { setErr(errText(e)); } finally { setBusy(false); setLive(''); load(); }
  };
  const MODES = [['chat', '질문'], ['explain', '코드로 설명'], ['socratic', '질문으로만'], ['exercise', '과제 검토']];
  return (
    <aside className="relative flex shrink-0 flex-col border-l border-card-line" style={{ width: w.w }}>
      <Resizer className="absolute -left-[4px] bottom-0 top-0" onDrag={(dx) => w.drag(dx, -1)} onEnd={(dx) => w.end(dx, -1)} onReset={w.reset} />
      <div className="border-b border-card-line px-3.5 py-2.5">
        <div className="text-[13px] font-bold">튜터</div>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {MODES.map(([k, l]) => <button key={k} onClick={() => setMode(k)} className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ${mode === k ? 'bg-card-ink text-card' : 'ring-1 ring-card-line'}`}>{l}</button>)}
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3.5 py-3">
        {!msgs.length && !live && (
          <div className="space-y-1.5 rounded-xl bg-raise/70 p-3 text-[12.5px] text-card-sub">
            <div>튜터는 이 장 원문과 연결된 코드를 읽고 있어요. 이렇게 시작해 보세요.</div>
            {[['explain', '이 장의 핵심을 우리 코드로 설명해 줘'], ['socratic', '나한테 질문하면서 이 장을 이해시켜 줘'], ['chat', '이 장을 읽기 전에 알아야 할 것은?']].map(([m, t]) => (
              <button key={t} onClick={() => { setMode(m); send(t, m); }} className="block w-full rounded-lg bg-card px-2.5 py-1.5 text-left ring-1 ring-card-line hover:ring-card-ink">{t}</button>
            ))}
          </div>
        )}
        {msgs.map((m) => (m.role === 'user'
          ? <div key={m.id} className="flex justify-end"><div className="max-w-[88%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-card-ink px-3 py-2 text-[13px] text-card">{m.text}</div></div>
          : <div key={m.id} className="whitespace-pre-wrap text-[13px] leading-relaxed"><div className="mb-0.5 text-[11px] font-bold text-card-faint">튜터</div>{m.text}</div>))}
        {live && <div className="whitespace-pre-wrap text-[13px] leading-relaxed"><div className="mb-0.5 text-[11px] font-bold text-card-faint">튜터</div>{live}</div>}
        {busy && !live && <div className="flex items-center gap-1.5 text-[12px] text-card-faint"><Icon name="spark" className="size-3.5 animate-twinkle" />생각하는 중…</div>}
        {err && <div className="text-[12px] text-accent">{err}</div>}
      </div>
      <div className="border-t border-card-line p-2.5">
        <div className="flex items-end gap-1.5 rounded-xl bg-field p-1.5 ring-1 ring-field-line focus-within:ring-card-faint">
          <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder={mode === 'exercise' ? '한 일을 적거나 코드를 붙여 넣으세요' : '튜터에게 묻기'}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(text); } }}
                    className="max-h-40 min-h-[38px] flex-1 resize-none bg-transparent px-1 py-1 text-[13px] outline-none" />
          <button onClick={() => send(text)} disabled={!text.trim() || busy} className="grid size-7 shrink-0 place-items-center rounded-lg bg-card-ink text-card disabled:opacity-30"><Icon name="arrowUp" className="size-3.5" /></button>
        </div>
      </div>
    </aside>
  );
}

function ChapterView({ c, ch, refresh }) {
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [open, setOpen] = useState(null);
  const [notes, setNotes] = useState(ch.notes || '');
  const [quizOpen, setQuizOpen] = useState(false);
  useEffect(() => { setNotes(ch.notes || ''); setOpen(null); setQuizOpen(false); }, [ch.id]);
  const act = async (label, fn) => { setBusy(label); setErr(''); try { await fn(); await refresh(); } catch (e) { setErr(errText(e)); } finally { setBusy(''); } };
  const card = ch.card;
  const quiz = c.cards.filter((q) => q.chid === ch.id);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
      <div className="mx-auto max-w-[780px]">
        <div className="text-[12px] text-card-faint">{c.title} · {ch.n}장{ch.minutes ? ` · 약 ${ch.minutes}분` : ''}</div>
        <h1 className="mt-0.5 text-[22px] font-extrabold tracking-tight">{ch.title}</h1>
        {ch.goal && <div className="mt-1 text-[13.5px]"><b>목표</b> {ch.goal}</div>}
        {ch.why && <div className="mt-0.5 text-[12.5px] text-card-sub">{ch.why}</div>}
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <Btn onClick={() => a2a.send('bookmark:open', ch.url)}><Icon name="page" className="size-3.5" />원문 열기(옆 탭)</Btn>
          {['reading', 'done'].map((s) => <Btn key={s} small primary={ch.status === s} onClick={() => act('', () => call('setStatus', c.id, ch.id, s))}>{STATUS[s][0]}</Btn>)}
          {busy && <span className="flex items-center gap-1.5 text-[12px] text-card-sub"><Icon name="spark" className="size-3.5 animate-twinkle" />{busy}</span>}
          {err && <span className="text-[12px] text-accent">{err}</span>}
        </div>

        {!card ? (
          <div className="mt-8 rounded-2xl border border-dashed border-card-line p-8 text-center">
            <div className="text-[14px] font-semibold">이 장의 학습 카드를 만들까요?</div>
            <div className="mt-1 text-[12.5px] text-card-sub">원문을 읽고 읽기 전 질문, 요약, 핵심 용어, 우리 코드와의 연결, 인출 문제, 과제를 만듭니다(1~2분).</div>
            <div className="mt-4"><Btn primary disabled={!!busy} onClick={() => act('원문을 읽고 학습 카드를 만드는 중…', () => call('prepare', c.id, ch.id))}><Icon name="spark" className="size-3.5" />학습 카드 만들기</Btn></div>
          </div>
        ) : (
          <div className="mt-6">
            <Section title="① 읽기 전에 — 예측해 보기" hint="답을 몰라도 괜찮아요. 적어 두면 읽을 때 더 잘 들어옵니다">
              <ul className="mb-2 list-disc space-y-1 pl-5 text-[13.5px]">{card.pre.map((x, i) => <li key={i}>{typeof x === 'string' ? x : x.q || JSON.stringify(x)}</li>)}</ul>
              <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} onBlur={() => call('setNotes', c.id, ch.id, notes)}
                        placeholder="내 예측·메모(자동 저장 — 튜터도 읽어요)" className="w-full rounded-lg bg-field px-2.5 py-2 text-[13px] ring-1 ring-field-line outline-none focus:ring-card-faint" />
            </Section>
            <Section title="② 원문 읽기" hint="옆 탭에서 읽고 돌아오세요">
              <Btn onClick={() => a2a.send('bookmark:open', ch.url)}>원문 열기</Btn>
            </Section>
            <Section title="③ 핵심 정리">
              <ul className="list-disc space-y-1 pl-5 text-[13.5px] leading-relaxed">{card.summary.map((x, i) => <li key={i}>{x}</li>)}</ul>
              {card.terms?.length > 0 && (
                <div className="mt-3 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-[12.5px]">
                  {card.terms.map((t, i) => <Fragment key={i}><span className="font-semibold">{t.term}</span><span className="text-card-sub">{t.meaning}</span></Fragment>)}
                </div>
              )}
            </Section>
            {card.anchors?.length > 0 && (
              <Section title="④ 우리 코드에서 보기" hint="이 장의 개념이 실제로 쓰인 곳 — 눌러서 열람">
                <div className="space-y-1.5">
                  {card.anchors.map((a) => (
                    <div key={`${a.file}#${a.find}`}>
                      <button onClick={() => setOpen(open?.find === a.find && open?.file === a.file ? null : a)} className="flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left ring-1 ring-card-line hover:bg-raise">
                        <span className="font-mono text-[12px] font-semibold">{a.file}</span><span className="font-mono text-[12px] text-info">{a.find}</span>
                        <span className="min-w-0 flex-1 text-[12.5px] text-card-sub">— {a.why}</span>
                      </button>
                      {open?.file === a.file && open?.find === a.find && <CodeView anchor={a} onClose={() => setOpen(null)} />}
                    </div>
                  ))}
                </div>
              </Section>
            )}
            <Section title="⑤ 인출 연습" hint={`${quiz.length}문제 — 원문을 덮고 내 말로`}>
              {!quizOpen ? <Btn primary onClick={() => setQuizOpen(true)}>시작하기</Btn> : <div className="space-y-2.5">{quiz.map((q) => <QuizCard key={q.id} c={c} q={q} />)}</div>}
            </Section>
            {card.exercise?.title && (
              <Section title="⑥ 적용 과제" hint="해 본 뒤 튜터의 '과제 검토'로 보여 주세요">
                <div className="rounded-xl p-3 ring-1 ring-card-line text-[13px]">
                  <div className="font-semibold">{card.exercise.title}</div>
                  <ol className="mt-1.5 list-decimal space-y-0.5 pl-5">{(card.exercise.steps || []).map((x, i) => <li key={i}>{x}</li>)}</ol>
                  {card.exercise.done_when && <div className="mt-1.5 text-card-sub">완료 조건: {card.exercise.done_when}</div>}
                </div>
              </Section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Review({ c, onClose }) {
  const [cards, setCards] = useState(null);
  useEffect(() => { call('due', c.id).then(setCards); }, [c.id]);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
      <div className="mx-auto max-w-[780px]">
        <div className="mb-3 flex items-center gap-2"><h1 className="text-[20px] font-extrabold">오늘 복습</h1><span className="text-[12px] text-card-faint">{c.title}</span><span className="flex-1" /><Btn small onClick={onClose}>닫기</Btn></div>
        {!cards ? <div className="text-card-faint">불러오는 중…</div> : !cards.length ? <div className="text-[13px] text-card-sub">오늘 볼 카드가 없어요. 장을 더 읽거나 내일 다시 오세요.</div>
          : <div className="space-y-2.5">{cards.map((q) => <QuizCard key={q.id} c={c} q={q} />)}</div>}
      </div>
    </div>
  );
}

export default function Learn() {
  const [st, setSt] = useState(null);
  const [sel, setSel] = useState({ cid: null, chid: null, review: false });
  const [form, setForm] = useState({ url: '', goal: '' });
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const listW = useWidth('learn.listWidth', 260, 200, 420);
  const tutorW = useWidth('learn.tutorWidth', 380, 300, 720);
  const refresh = useCallback(async () => { const s = await call('state'); setSt(s); return s; }, []);
  useEffect(() => { refresh(); return a2a.on('learn:changed', () => refresh()); }, [refresh]);
  useEffect(() => a2a.on('learn:progress', (p) => { if (p?.text && !p.type) setBusy(p.text); }), []);
  const c = useMemo(() => st?.courses.find((x) => x.id === sel.cid) || st?.courses[0] || null, [st, sel.cid]);
  const ch = c && (c.chapters.find((x) => x.id === sel.chid) || c.chapters.find((x) => x.status === 'reading') || c.chapters.find((x) => !x.skip));
  const start = async () => {
    setBusy('목차를 읽고 학습 지도를 만드는 중…(1분 안팎)'); setErr('');
    try { const nc = await call('start', form.url, { goal: form.goal }); setForm({ url: '', goal: '' }); await refresh(); setSel({ cid: nc.id, chid: null, review: false }); }
    catch (e) { setErr(errText(e)); } finally { setBusy(''); }
  };
  if (!st) return <div className="absolute inset-0 grid place-items-center text-card-faint">불러오는 중…</div>;
  return (
    <div id="learn" className="absolute inset-0 flex min-h-0">
      <aside className="relative flex shrink-0 flex-col border-r border-card-line" style={{ width: listW.w }}>
        <Resizer className="absolute -right-[4px] bottom-0 top-0" onDrag={(dx) => listW.drag(dx)} onEnd={(dx) => listW.end(dx)} onReset={listW.reset} />
        <div className="px-4 pb-2 pt-4"><div className="text-[16px] font-extrabold tracking-tight">학습</div><div className="text-[11px] text-card-faint">링크 하나로 — 예측 · 읽기 · 인출 · 연결 · 적용 · 복습{st.project ? ` · 연결 프로젝트: ${st.project}` : ''}</div></div>
        <div className="mx-3 mb-2 space-y-1.5 rounded-xl bg-raise/70 p-2.5">
          <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="공부할 링크 (예: https://browser.engineering/)" className="h-8 w-full rounded-lg bg-field px-2.5 text-[12.5px] ring-1 ring-field-line outline-none focus:ring-card-faint" />
          <input value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} placeholder="왜 공부하나요? (선택 — 과정이 맞춰져요)" className="h-8 w-full rounded-lg bg-field px-2.5 text-[12.5px] ring-1 ring-field-line outline-none focus:ring-card-faint" />
          <Btn primary small disabled={!form.url.trim() || !!busy} onClick={start}>학습 시작</Btn>
        </div>
        {busy && <div className="mx-3 mb-2 flex items-center gap-1.5 text-[11.5px] text-card-sub"><Icon name="spark" className="size-3.5 animate-twinkle" />{busy}</div>}
        {err && <div className="mx-3 mb-2 text-[11.5px] text-accent">{err}</div>}
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {st.courses.map((x) => (
            <div key={x.id} className="mb-2">
              <button onClick={() => setSel({ cid: x.id, chid: null, review: false })} className={`w-full rounded-lg px-2.5 py-1.5 text-left ${c?.id === x.id ? 'bg-raise' : 'hover:bg-raise/60'}`}>
                <div className="truncate text-[13px] font-bold">{x.title}</div>
                <div className="text-[11px] text-card-faint">완료 {x.chapters.filter((k) => k.status === 'done').length}/{x.chapters.filter((k) => !k.skip).length}장 · 복습 {x.due}개</div>
              </button>
              {c?.id === x.id && (
                <div className="mt-1 space-y-px">
                  {x.due > 0 && <button onClick={() => setSel({ cid: x.id, chid: null, review: true })} className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1 text-left text-[12px] font-semibold text-info ${sel.review ? 'bg-raise' : 'hover:bg-raise/60'}`}><Icon name="reload" className="size-3.5" />오늘 복습 {x.due}개</button>}
                  {x.chapters.map((k) => (
                    <button key={k.id} onClick={() => setSel({ cid: x.id, chid: k.id, review: false })} title={k.skip ? k.skipReason : k.goal}
                            className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1 text-left text-[12.5px] ${!sel.review && ch?.id === k.id ? 'bg-raise font-semibold' : 'hover:bg-raise/60'} ${k.skip ? 'text-card-faint' : ''}`}>
                      <span className={`size-1.5 shrink-0 rounded-full ${STATUS[k.status]?.[1]}`} /><span className="w-5 shrink-0 text-right text-[11px] text-card-faint">{k.n}</span><span className="min-w-0 flex-1 truncate">{k.title}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
          {!st.courses.length && <div className="px-3 py-4 text-[12.5px] leading-relaxed text-card-faint">공부할 책·강의 사이트 링크를 넣으면 목차를 읽어 장별 과정을 만들어요.</div>}
        </div>
      </aside>
      {c && sel.review ? <Review c={c} onClose={() => setSel({ ...sel, review: false })} />
        : c && ch ? <><ChapterView c={c} ch={ch} refresh={refresh} /><Tutor key={ch.id} c={c} ch={ch} w={tutorW} /></>
          : <div className="grid flex-1 place-items-center text-[13px] text-card-faint">왼쪽에 링크를 넣고 학습을 시작하세요.</div>}
    </div>
  );
}
