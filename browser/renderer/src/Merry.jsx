// 메리 — 투자 딜룸(invest/*). 즐겨찾기 "메리"(a2a://메리)로 연다.
//   왼쪽: 투자 건 목록 · 새 투자 건
//   가운데: 보고서(A4 종이 문서) / 논의·결정 / 근거·자료 / 가정·계산
// 여기서 남긴 논의·결정·가정은 모두 수집 로그(invest.*)를 거쳐 업무 지도에 투자 건·쟁점·결정 노드로 쌓인다.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import { a2a } from './store.js';
import { ago as agoMs } from './format.js';
import './merry.css';
import DocEditor from './merry/DocEditor.jsx';
import Resizer from './Resizer.jsx';

const ago = (iso) => (iso ? agoMs(Date.parse(iso)) : '');

const call = (name, ...args) => a2a.invoke('invest:call', name, ...args);
const KIND = { question: '질문', issue: '쟁점', bull: '강점', bear: '우려', answer: '답', comment: '코멘트', request: '받을 자료' };
const KIND_TONE = { question: 'bg-info/10 text-info', issue: 'bg-warn/10 text-warn', bull: 'bg-ok/10 text-ok', bear: 'bg-accent-soft text-accent', answer: 'bg-raise text-card-sub', comment: 'bg-raise text-card-sub', request: 'bg-accent-soft text-accent' };

function Btn({ children, onClick, primary, disabled, title, small }) {
  return (
    <button title={title} disabled={disabled} onClick={onClick}
            className={`inline-flex ${small ? 'h-6 px-2 text-[11px]' : 'h-7 px-2.5 text-xs'} items-center gap-1.5 rounded-lg font-semibold disabled:opacity-40
                        ${primary ? 'bg-card-ink text-card' : 'ring-1 ring-card-line hover:bg-raise'}`}>
      {children}
    </button>
  );
}
const Field = (p) => <input {...p} className={`h-8 w-full rounded-lg bg-field px-2.5 text-[13px] ring-1 ring-field-line outline-none focus:ring-card-faint ${p.className || ''}`} />;
const Area = (p) => <textarea {...p} className={`w-full rounded-lg bg-field px-2.5 py-2 text-[13px] ring-1 ring-field-line outline-none focus:ring-card-faint ${p.className || ''}`} />;

// ── 섹션 HTML 정리 — 허용한 태그·클래스만 남기고, [f:…]는 근거 번호, [확인 필요…]는 빨간 표시로 ──
const TAGS = new Set(['H2', 'H3', 'H4', 'P', 'UL', 'OL', 'LI', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'STRONG', 'EM', 'DIV', 'BLOCKQUOTE', 'BR', 'U', 'SPAN']);
const DROP = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'LINK', 'META', 'IMG', 'SVG', 'MATH', 'FORM', 'INPUT', 'BUTTON', 'TEXTAREA', 'SELECT', 'TEMPLATE']);
const CLASSES = new Set(['box', 'caption', 'num']);
function sanitize(html, citeNo) {
  const doc = new DOMParser().parseFromString(`<div>${html || ''}</div>`, 'text/html');
  const root = doc.body.firstChild;
  const walk = (el) => {
    for (const c of [...el.childNodes]) {
      if (c.nodeType === 3) continue;
      if (c.nodeType !== 1 || DROP.has(c.tagName)) { c.remove(); continue; }
      if (!TAGS.has(c.tagName)) { walk(c); c.replaceWith(...c.childNodes); continue; }
      for (const a of [...c.attributes]) {
        const keep = (a.name === 'class' && a.value.split(/\s+/).every((x) => CLASSES.has(x))) || ((a.name === 'colspan' || a.name === 'rowspan') && /^\d{1,2}$/.test(a.value));
        if (!keep) c.removeAttribute(a.name);
      }
      walk(c);
    }
  };
  walk(root);
  // 글자 속 표시를 요소로
  const tw = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts = [];
  while (tw.nextNode()) texts.push(tw.currentNode);
  const RE = /\[f:([a-z0-9_]+)\]|\[확인 필요(?::\s*([^\]]*))?\]/g;
  for (const t of texts) {
    const s = t.nodeValue;
    if (!RE.test(s)) continue;
    RE.lastIndex = 0;
    const frag = doc.createDocumentFragment();
    let last = 0, m;
    while ((m = RE.exec(s))) {
      if (m.index > last) frag.append(s.slice(last, m.index));
      if (m[1]) {
        const sup = doc.createElement('sup');
        sup.className = 'mr-cite';
        sup.dataset.f = m[1];
        sup.textContent = citeNo(m[1]);
        frag.append(sup);
      } else {
        const sp = doc.createElement('span');
        sp.className = 'mr-tbd';
        sp.textContent = `확인 필요${m[2] ? `: ${m[2]}` : ''}`;
        frag.append(sp);
      }
      last = RE.lastIndex;
    }
    if (last < s.length) frag.append(s.slice(last));
    t.replaceWith(frag);
  }
  // 음수 숫자 칸은 빨간 기울임(PDF 표와 같게)
  for (const td of root.querySelectorAll('td')) if (/^\s*[(-−][\d,.]+\)?\s*$/.test(td.textContent)) td.classList.add('mr-neg');
  return root.innerHTML;
}

// ── 보고서: 표지 + 섹션마다 종이 한 장(넘치면 길어진다) ──
function Cover({ deal, v }) {
  const val = (k) => v[k]?.display;
  const dates = deal.stageDates || {};
  return (
    <section className="mr-page mr-doc mr-cover">
      <div className="mr-kicker">₩투자심사보고서</div>
      <div className="mr-title">{deal.company}</div>
      <div className="mr-vision">{deal.vision ? `“${deal.vision}”` : <span className="mr-tbd mr-noprint">한 줄 비전 확인 필요</span>}</div>
      <div className="mr-sched">
        <span>내부 IR</span><span>{dates.internal_ir || '—'}</span>
        <span>임팩트 심의위</span><span>{dates.impact_committee || '—'}</span>
        <span>본투심</span><span>{dates.investment_committee || '—'}</span>
      </div>
      <div className="mr-bottom">
        <table>
          <tbody>
            <tr><th>투자 대상</th><td>{deal.company}</td></tr>
            <tr><th>투자 금액</th><td>{val('invest_amount') || (deal.amount ? `₩${Number(deal.amount).toLocaleString('ko-KR')}` : <span className="mr-tbd">확인 필요</span>)}</td></tr>
            <tr><th>투자 계정</th><td>{deal.fund || <span className="mr-tbd">확인 필요</span>}</td></tr>
            <tr><th>투자 방식</th><td>{deal.method || <span className="mr-tbd">확인 필요</span>}</td></tr>
            <tr><th>심사 담당</th><td>{deal.owner}</td></tr>
          </tbody>
        </table>
      </div>
      <div className="mr-folio">1</div>
    </section>
  );
}

// 쓰는 중인 초안의 {{var:…}}을 화면에서 바로 채운다(저장된 섹션은 메인이 채워서 준다)
const fillVars = (html, vars) => String(html || '').replace(/\{\{var:([a-z0-9_]+)\}\}/g, (m, k) => vars[k]?.display || `[확인 필요: ${k}]`);

function SectionPage({ s, sec, no, citeNo, onCite, onWrite, busy, live, target, onTarget, dealId, vars }) {
  // 메리가 쓰는 중이면 실시간 초안(읽기 전용), 저장된 섹션은 문서 편집기(토큰이 살아 있는 원문 sec.html), 없으면 빈 종이
  const html = useMemo(() => (live ? sanitize(live, citeNo) : ''), [live, citeNo]);
  const [saved, setSaved] = useState(null);
  const sum = sec?.summary;
  return (
    <section data-section={s.key} className={`mr-page mr-doc ${target ? 'mr-target' : ''}`}
             onClick={(e) => { if (live) { const f = e.target.closest?.('sup.mr-cite'); if (f) onCite(f.dataset.f); } else if (sec) onTarget(s.key); }}>
      <div className="mr-status mr-noprint">
        {live && <span className="warn">메리가 쓰는 중…</span>}
        {!live && sec && <span className={sec.status === 'verified' ? 'ok' : sec.status === 'edited' ? '' : 'warn'}>{sec.status === 'verified' ? '검증 통과' : sec.status === 'edited' ? '사람이 고침' : '검토 필요'}</span>}
        {!live && sum && <span className={sum.score >= 90 ? 'ok' : sum.score >= 70 ? 'warn' : 'bad'}>체크리스트 {sum.score}%</span>}
        {!live && sum?.unsupported > 0 && <span className="bad">근거 불일치 {sum.unsupported}</span>}
        {!live && sec && <span>{ago(sec.at)}</span>}
        {target && <span className="ok">대화 대상</span>}
        {!live && sec && <span>{busy ? '메리가 일하는 중 — 잠김' : saved && Date.now() - saved < 4000 ? '저장됨' : '바로 고칠 수 있어요'}</span>}
      </div>
      <h1 className="mr-chapter">{s.title}</h1>
      {live ? <div dangerouslySetInnerHTML={{ __html: html }} />
        : sec ? (
          <DocEditor html={sec.html} vars={vars} citeNo={citeNo} onCite={onCite} readOnly={busy}
                     onSave={async (h) => { const r = await call('saveSectionHtml', dealId, s.key, h); if (r?.ok) setSaved(Date.now()); }} />
        ) : (
          <div className="mr-noprint mt-6 rounded-md border border-dashed border-[#bbb] p-6 text-center font-sans text-[13px] text-[#666]">
            아직 쓰지 않은 섹션입니다.
            <div className="mt-3"><Btn primary onClick={onWrite} disabled={busy}><Icon name="spark" className="size-3.5" />메리와 함께 쓰기</Btn></div>
          </div>
        )}
      <div className="mr-folio">{no}</div>
    </section>
  );
}

// ── 메리와 함께 쓰기 — 대화창 ──
function Thinking({ label, text, open: open0 = false, live = false }) {
  const [open, setOpen] = useState(open0);
  return (
    <div className="text-[12px] text-card-faint">
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 hover:text-card-sub">
        <Icon name="spark" className={`size-3 ${live ? 'animate-twinkle' : ''}`} />{live ? '생각하는 중' : '생각'}{label ? ` · ${label}` : ''}<span className="text-[10px]">{open ? '▾' : '▸'}</span>
      </button>
      {open && <div className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap border-l-2 border-card-line pl-2.5 italic leading-relaxed">{text}</div>}
    </div>
  );
}

function AskCard({ m, dealId, onDone }) {
  const [text, setText] = useState('');
  const open = m.status === 'open';
  const send = async (o) => { try { await call('answer', dealId, m.id, o); } finally { onDone(); } };
  return (
    <div data-ask={open ? 'open' : 'done'} className={`rounded-xl p-3 text-[13px] ${open ? 'bg-info/[.07] ring-1 ring-info/30' : 'bg-raise/70'}`}>
      <div className="mb-1 flex items-center gap-1.5 text-[11px] font-bold text-info"><span>메리의 질문</span>{m.data?.why && <span className="font-normal text-card-faint">· {m.data.why}</span>}</div>
      <div className="whitespace-pre-wrap">{m.text}</div>
      {open ? (
        <>
          {m.options?.length > 0 && (
            <div className="mt-2 flex flex-col gap-1.5">
              {m.options.map((o, i) => (
                <button key={o} data-opt onClick={() => send({ choice: o })} className="rounded-lg bg-card px-3 py-1.5 text-left text-[12.5px] ring-1 ring-card-line hover:ring-card-ink">
                  <span className="mr-2 text-card-faint">{i + 1}</span>{o}
                </button>
              ))}
            </div>
          )}
          <div className="mt-2 flex gap-1.5">
            <Field placeholder="직접 답하기" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && text.trim() && !e.nativeEvent.isComposing) send({ text }); }} />
            <Btn small onClick={() => send({ skip: true })}>건너뛰기</Btn>
          </div>
        </>
      ) : <div className="mt-1.5 text-[12px] text-card-sub">{m.status === 'skipped' ? '건너뜀' : `→ ${m.answer}`}</div>}
    </div>
  );
}

function CopilotPanel({ data, live, target, setTarget, run, refresh, chatW }) {
  const { deal, chat, copilot, template } = data;
  const [text, setText] = useState('');
  const [drag, setDrag] = useState(false);
  const [err, setErr] = useState('');
  const box = useRef(null);
  const busy = !!copilot?.busy;
  const waiting = copilot?.waiting?.length > 0;
  const titleOf = (k) => template.sections.find((s) => s.key === k)?.title || k;
  // 대화 + (대화 밖에서 내린) 결정을 시간순으로
  const items = useMemo(() => {
    return [...chat.map((m) => ({ ...m, _t: 'chat' })),
      ...deal.decisions.map((x) => ({ ...x, _t: 'decision' }))]
      .sort((a, b) => String(a.at).localeCompare(String(b.at)));
  }, [chat, deal.decisions]);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }); }, [items.length, live.thinking, live.reply]);

  const act = async (fn) => { setErr(''); try { await fn(); } catch (e) { setErr(String(e.message || e).replace(/^Error invoking remote method '[^']+': (Error: )?/, '')); } finally { refresh(); } };
  const send = () => { const t = text.trim(); if (!t) return; setText(''); act(() => call('say', deal.id, t, target)); };
  const files = async (list) => act(async () => {
    const ids = [];
    for (const f of [...list].slice(0, 8)) {
      const src = await call('attachBytes', deal.id, f.name, new Uint8Array(await f.arrayBuffer()));
      ids.push(src.id);
    }
    if (ids.length) await call('readAttached', deal.id, ids);
  });

  return (
    <aside id="merry-chat" className={`relative flex shrink-0 flex-col border-l border-card-line ${drag ? 'bg-info/[.04]' : ''}`} style={{ width: chatW?.w ?? 380 }}
           onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
           onDrop={(e) => { e.preventDefault(); setDrag(false); files(e.dataTransfer.files); }}>
      {chatW && <Resizer id="resize-merry-chat" className="absolute -left-[4px] bottom-0 top-0" onDrag={(dx) => chatW.drag(dx, -1)} onEnd={(dx) => chatW.end(dx, -1)} onReset={chatW.reset} />}
      <div className="flex items-center gap-2 border-b border-card-line px-3.5 py-2.5">
        <div className="text-[13px] font-bold">메리와 함께 쓰기</div>
        {busy && <span className="flex min-w-0 items-center gap-1 truncate text-[11.5px] text-card-sub"><Icon name="spark" className="size-3 animate-twinkle" />{waiting ? '답을 기다리는 중' : copilot.busy}</span>}
        <div className="ml-auto flex gap-1">
          {busy && <Btn small onClick={() => act(() => call('stop', deal.id))}><Icon name="stop" className="size-3" />멈추기</Btn>}
          {!busy && <Btn small onClick={() => window.print()}>인쇄·PDF</Btn>}
        </div>
      </div>
      <div className="flex flex-wrap gap-1 border-b border-card-line px-3 py-2">
        {template.sections.filter((s) => s.key !== 'cover').map((s) => {
          const sec = data.sections[s.key];
          return (
            <button key={s.key} disabled={busy} title={`${s.title} — ${sec ? '다시 쓰기' : '메리와 함께 쓰기'}`}
                    onClick={() => act(() => call('write', deal.id, s.key))}
                    className={`flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] ring-1 ring-card-line hover:bg-raise disabled:opacity-40 ${target === s.key ? 'bg-raise font-semibold' : ''}`}>
              <span className={`size-1.5 rounded-full ${!sec ? 'bg-card-line' : sec.status === 'verified' ? 'bg-ok' : sec.status === 'edited' ? 'bg-info' : 'bg-warn'}`} />
              {s.title.replace(/\s*\(.*\)$/, '').replace(/^([IVX]+)\.\s*/, '$1 ')}
            </button>
          );
        })}
      </div>

      <div ref={box} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3.5 py-3">
        {!items.length && (
          <div className="rounded-xl bg-raise/70 p-3.5 text-[12.5px] leading-relaxed text-card-sub">
            IR 자료·재무제표·계약서를 여기로 끌어다 놓거나 📎로 첨부하세요. 메리가 읽고 쪽 번호가 달린 근거를 뽑습니다.<br />
            그다음 위에서 섹션을 고르면 구성을 먼저 보여 드리고, 판단이 필요한 것은 물어본 뒤 씁니다. 종이의 섹션을 누르고 말하면 그 섹션을 고쳐 씁니다.
          </div>
        )}
        {items.map((m) => {
          if (m._t === 'decision') {
            return (
              <div key={m.id} className={`rounded-lg px-3 py-2 text-[12px] ring-1 ${m.supersededBy ? 'opacity-50 ring-card-line' : 'ring-card-ink/60'}`}>
                <span className="mr-1.5 rounded bg-card-ink px-1 py-px text-[10px] font-bold text-card">결정</span>
                <b>{m.topic}</b> → {m.chosen}<span className="ml-1 text-card-faint">· {m.by} · {m.status === 'committed' ? '✔ 공식' : '탐색'}</span>
              </div>
            );
          }
          if (m.kind === 'thinking') return <Thinking key={m.id} label={m.label} text={m.text} />;
          if (m.kind === 'step') return <div key={m.id} className="flex gap-1.5 whitespace-pre-wrap text-[11.5px] leading-relaxed text-card-faint"><Icon name="check" className="mt-0.5 size-3 shrink-0" /><span>{m.text}</span></div>;
          if (m.kind === 'ask') return <AskCard key={m.id} m={m} dealId={deal.id} onDone={refresh} />;
          if (m.role === 'user') {
            return (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-card-ink px-3 py-2 text-[13px] text-card">
                  {m.section && <div className="mb-0.5 text-[10.5px] opacity-60">{titleOf(m.section)}</div>}{m.text}
                </div>
              </div>
            );
          }
          return (
            <div key={m.id} className="text-[13px] leading-relaxed">
              <div className="mb-0.5 text-[11px] font-bold text-card-faint">메리{m.section ? ` · ${titleOf(m.section)}` : ''}</div>
              <div className={`whitespace-pre-wrap ${m.data?.error ? 'text-accent' : ''}`}>{m.text}</div>
            </div>
          );
        })}
        {live.thinking && <Thinking label={live.label} text={live.thinking} open live />}
        {live.reply && <div className="whitespace-pre-wrap text-[13px] leading-relaxed"><div className="mb-0.5 text-[11px] font-bold text-card-faint">메리</div>{live.reply}</div>}
        {live.step && busy && <div className="text-[11.5px] text-card-faint">{live.step}</div>}
      </div>

      <div className="border-t border-card-line p-2.5">
        {target && (
          <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-card-sub">
            <span className="rounded bg-raise px-1.5 py-0.5">{titleOf(target)}에 대해 말하는 중 — 고쳐 씁니다</span>
            <button className="text-card-faint hover:text-card-ink" onClick={() => setTarget(null)}>해제</button>
          </div>
        )}
        <div className="flex items-end gap-1.5 rounded-xl bg-field p-1.5 ring-1 ring-field-line focus-within:ring-card-faint">
          <button title="자료 첨부 (PDF·텍스트)" disabled={busy && !waiting} onClick={() => act(() => call('attach', deal.id))} className="grid size-7 shrink-0 place-items-center rounded-lg text-card-sub hover:bg-raise disabled:opacity-40">📎</button>
          <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)}
                    placeholder={waiting ? '질문에 직접 답하기…' : target ? '예: 할인율 근거를 표로 정리해 줘' : '자료에 대해 묻거나, 섹션을 골라 지시하세요'}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
                    className="max-h-40 min-h-[38px] flex-1 resize-none bg-transparent px-1 py-1 text-[13px] outline-none" />
          <button onClick={send} disabled={!text.trim() || (busy && !waiting)} className="grid size-7 shrink-0 place-items-center rounded-lg bg-card-ink text-card disabled:opacity-30"><Icon name="arrowUp" className="size-3.5" /></button>
        </div>
        {err && <div className="mt-1 text-[11.5px] text-accent">{err}</div>}
      </div>
      {drag && <div className="pointer-events-none absolute inset-2 grid place-items-center rounded-xl border-2 border-dashed border-info/60 text-[13px] font-semibold text-info">여기에 놓으면 메리가 읽어요</div>}
    </aside>
  );
}

function ReportTab({ data, live, target, setTarget, run, refresh, chatW }) {
  const { deal, sections, template, vars } = data;
  const [fact, setFact] = useState(null);
  const order = useRef(new Map());
  order.current = new Map();
  const citeNo = useCallback((id) => { if (!order.current.has(id)) order.current.set(id, order.current.size + 1); return order.current.get(id); }, [data]);  // eslint-disable-line react-hooks/exhaustive-deps
  const factOf = (id) => deal.facts.find((f) => f.id === id);
  const body = template.sections.filter((s) => s.key !== 'cover');
  const busy = !!data.copilot?.busy;
  // 종이 폭(210mm ≈ 794px)이 가운데 화면보다 넓으면 폭에 맞춰 줄인다 — 문서 뷰어의 "폭 맞춤"
  const desk = useRef(null);
  const [zoom, setZoom] = useState(1);
  useEffect(() => {
    const el = desk.current;
    if (!el) return undefined;
    const fit = () => setZoom(Math.min(1.15, Math.max(0.45, (el.clientWidth - 40) / 794)));
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // 메리가 쓰기 시작하면 그 종이로
  const followed = useRef(null);
  useEffect(() => {
    if (!live.draftSection || followed.current === live.draftSection) return;
    followed.current = live.draftSection;
    desk.current?.querySelector(`[data-section="${live.draftSection}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [live.draftSection]);
  useEffect(() => { if (!live.draftSection) followed.current = null; }, [live.draftSection]);
  const f = fact && factOf(fact);
  const fsrc = f && deal.sources.find((s) => s.id === f.sourceId);
  return (
    <div className="flex min-h-0 flex-1">
      <div ref={desk} className="mr-desk relative min-w-0 flex-1 overflow-y-auto overflow-x-hidden py-5">
        <div style={{ zoom }}>
          <Cover deal={deal} v={vars} />
          {body.map((s, i) => (
            <SectionPage key={s.key} s={s} sec={sections[s.key]} no={i + 2} citeNo={citeNo} onCite={setFact} busy={busy}
                         live={live.draftSection === s.key && live.draft ? fillVars(live.draft, vars) : null}
                         target={target === s.key} onTarget={setTarget} dealId={deal.id} vars={vars}
                         onWrite={() => run('write', deal.id, s.key)} />
          ))}
        </div>
        {fact && (
          <div className="sticky bottom-3 mx-auto w-[min(560px,90%)] rounded-xl bg-card p-3 text-[12.5px] text-card-ink shadow-card ring-1 ring-card-line">
            <div className="mb-1 flex items-center gap-2 font-bold">근거 {citeNo(fact)}<span className="ml-auto cursor-pointer text-card-faint" onClick={() => setFact(null)}>닫기</span></div>
            {f ? (
              <>
                <div className="font-semibold">{f.label}: {typeof f.value === 'number' ? f.value.toLocaleString('ko-KR') : String(f.value)}{f.unit ? ` ${f.unit}` : ''}{f.period ? ` (${f.period})` : ''}</div>
                {f.quote && <div className="mt-1 border-l-2 border-card-line pl-2 text-card-sub">“{f.quote}”</div>}
                <div className="mt-1 text-card-faint">{fsrc?.name || '직접 입력'}{f.page ? ` · ${f.page}쪽` : ''} · {f.id}</div>
              </>
            ) : <div className="text-accent">없는 근거 id입니다 — 검토가 필요합니다</div>}
          </div>
        )}
      </div>
      <CopilotPanel data={data} live={live} target={target} setTarget={setTarget} run={run} refresh={refresh} chatW={chatW} />
    </div>
  );
}

// 공식화(Commit) — 탐색 대화의 결정을 사람이 명시적으로 공식 업무로(G-COMMIT). 역할을 남긴다
function CommitBtn({ deal, d, roles = {}, done }) {
  const [open, setOpen] = useState(false);
  const [role, setRole] = useState('pm');
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  if (!open) return <Btn small onClick={() => setOpen(true)} title="탐색 대화의 결정을 공식 업무로 올립니다(사람만)"><Icon name="check" className="size-3" />공식화</Btn>;
  return (
    <div className="flex w-full flex-wrap items-center gap-1.5 rounded-lg bg-raise p-2">
      <select value={role} onChange={(e) => setRole(e.target.value)} className="h-7 rounded-md bg-field px-1.5 text-[12px] ring-1 ring-field-line">
        {Object.entries(roles).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="메모(선택)" className="h-7 min-w-0 flex-1 rounded-md bg-field px-2 text-[12px] ring-1 ring-field-line" />
      <Btn small primary onClick={async () => { try { await call('commit', deal.id, d.id, { role, note }); setOpen(false); done(); } catch (e) { setErr(String(e.message || e).replace(/^Error invoking remote method '[^']+': (Error: )?/, '')); } }}>공식화(Commit)</Btn>
      <Btn small onClick={() => setOpen(false)}>취소</Btn>
      {err && <span className="text-[11px] text-accent">{err}</span>}
    </div>
  );
}

// Work ID — FRN·PRN·fund_id·IRN/INV. 머리에 칩으로, 누르면 고친다
function WorkIdBar({ deal, refresh }) {
  const w = deal.work || {};
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState('');
  const chip = (k, v) => <span key={k} className="rounded bg-raise px-1.5 py-0.5 font-mono text-[10.5px] text-card-sub">{k} {Array.isArray(v) ? v.join(',') : v}</span>;
  if (edit) {
    const f = (k, ph) => <input value={edit[k] || ''} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} placeholder={ph} className="h-6 w-28 rounded bg-field px-1.5 font-mono text-[11px] ring-1 ring-field-line" />;
    return (
      <span className="flex flex-wrap items-center gap-1">
        {f('prn', 'PRN 예: AC-00')}{f('fund_id', 'fund_id')}{f('irn', 'IRN 예: 26-03')}{f('inv', 'INV-331')}
        <Btn small primary onClick={async () => { try { await call('setWork', deal.id, { prn: edit.prn, fund_id: edit.fund_id, irn: edit.irn ? edit.irn.split(',').map((x) => x.trim()) : undefined, inv: edit.inv ? edit.inv.split(',').map((x) => x.trim()) : undefined }); setEdit(null); refresh(); } catch (e) { setErr(String(e.message || e)); } }}>저장</Btn>
        <Btn small onClick={() => setEdit(null)}>취소</Btn>{err && <span className="text-[11px] text-accent">{err}</span>}
      </span>
    );
  }
  return (
    <button onClick={() => setEdit({ prn: w.prn || '', fund_id: w.fund_id || '', irn: [].concat(w.irn || []).join(','), inv: [].concat(w.inv || []).join(',') })} title="Work ID — 눌러서 PRN·fund_id·IRN·INV 연결" className="flex flex-wrap items-center gap-1">
      {w.frn ? chip('FRN', w.frn) : <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10.5px] text-accent">FRN 없음</span>}
      {w.prn && chip('PRN', w.prn)}{w.fund_id && chip('fund', w.fund_id)}{w.irn && chip('IRN', w.irn)}{w.inv && chip('INV', w.inv)}
      {w.invalid && <span className="text-[10.5px] text-accent">형식 확인: {w.invalid.join(', ')}</span>}
      <span className="text-[10.5px] text-card-faint">+ Work ID</span>
    </button>
  );
}

// ── 논의·결정 ── 업무 지도에 남는 핵심
function DiscussTab({ data, refresh, busy }) {
  const { deal, template } = data;
  const [kind, setKind] = useState('issue');
  const [text, setText] = useState('');
  const [section, setSection] = useState('');
  const [dc, setDc] = useState(null);           // 결정 쓰는 중 { topic, chosen, rationale, evidence, resolves, supersedes }
  const [err, setErr] = useState('');
  const act = async (fn) => { setErr(''); try { await fn(); refresh(); } catch (e) { setErr(String(e.message || e).replace(/^Error invoking remote method '[^']+': (Error: )?/, '')); } };
  const active = deal.decisions.filter((x) => !x.supersededBy);
  const timeline = useMemo(() => [...deal.items.map((x) => ({ ...x, _t: 'item' })), ...deal.decisions.map((x) => ({ ...x, _t: 'decision' }))]
    .sort((a, b) => String(b.at).localeCompare(String(a.at))), [deal]);
  const secTitle = (k) => template.sections.find((s) => s.key === k)?.title || '';
  const startDecision = (from) => setDc({ topic: from?.topic || '', chosen: from?.chosen || '', rationale: '', evidence: from?.evidence || [], resolves: from?.resolves || [], supersedes: from?.supersedes || '', section: from?.section || '' });

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-h-0 overflow-auto p-5">
        {deal.items.some((x) => x.kind === 'request') && (
          <div className="mb-6">
            <div className="mb-2 flex items-center gap-2">
              <h2 className="text-[15px] font-bold">받을 자료</h2>
              <span className="text-[12px] text-card-faint">
                {deal.items.filter((x) => x.kind === 'request' && x.status === 'open').length}개 남음 · 받음 {deal.items.filter((x) => x.kind === 'request' && (x.status === 'received' || x.status === 'partial')).length}
              </span>
              <span className="ml-auto text-[11.5px] text-card-faint">자료가 오면 보고서 탭 대화창에 끌어다 놓으세요 — 들어온 것은 메리가 체크합니다</span>
            </div>
            <div className="divide-y divide-card-line rounded-xl ring-1 ring-card-line">
              {deal.items.filter((x) => x.kind === 'request').map((x) => {
                const [what, why] = x.text.split('\n(왜: ');
                const done = x.status === 'received' || x.status === 'waived';
                return (
                  <div key={x.id} className={`flex items-start gap-2.5 px-3 py-2 text-[12.5px] ${done ? 'opacity-60' : ''}`}>
                    <span className={`mt-1 size-2 shrink-0 rounded-full ${x.status === 'received' ? 'bg-ok' : x.status === 'partial' ? 'bg-info' : x.status === 'waived' ? 'bg-card-line' : x.priority === '필수' ? 'bg-accent' : 'bg-warn'}`} />
                    <div className="min-w-0 flex-1">
                      <div className={`font-semibold ${x.status === 'received' ? 'line-through' : ''}`}>{what}</div>
                      <div className="text-[11.5px] text-card-faint">{[x.priority, x.from && `${x.from}에게`, why && `왜: ${why.replace(/\)$/, '')}`, x.section && template.sections.find((s) => s.key === x.section)?.title].filter(Boolean).join(' · ')}</div>
                    </div>
                    <span className="shrink-0 text-[11px] text-card-sub">{{ open: '대기', received: '받음', partial: '일부 받음', waived: '해당 없음' }[x.status] || x.status}</span>
                    <div className="flex shrink-0 gap-1">
                      {x.status !== 'received' && <Btn small onClick={() => act(() => call('setRequest', deal.id, x.id, 'received', '사람이 확인'))}>받음</Btn>}
                      {x.status === 'open' && <Btn small onClick={() => act(() => call('setRequest', deal.id, x.id, 'waived', '해당 없음'))}>해당 없음</Btn>}
                      {x.status !== 'open' && <Btn small onClick={() => act(() => call('setRequest', deal.id, x.id, 'open', '다시 열기'))}>다시</Btn>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-[15px] font-bold">논의와 결정</h2>
          <span className="text-[12px] text-card-faint">미해결 {deal.items.filter((x) => x.status === 'open').length} · 유효한 결정 {active.length}</span>
        </div>
        <div className="space-y-2">
          {timeline.map((x) => (x._t === 'decision' ? (
            <div key={x.id} className={`rounded-xl p-3 ring-1 ${x.supersededBy ? 'opacity-55 ring-card-line' : 'ring-card-ink/70'}`}>
              <div className="flex items-center gap-2 text-[11px]">
                <span className="rounded bg-card-ink px-1.5 py-0.5 font-bold text-card">결정</span>
                {x.section && <span className="text-card-faint">{secTitle(x.section)}</span>}
                {x.supersededBy && <span className="text-card-faint">· 이후 결정으로 대체됨</span>}
                {x.supersedes && <span className="text-card-faint">· 이전 결정을 바꿈</span>}
                <span className={x.status === 'committed' ? 'font-semibold text-ok' : 'text-card-faint'}>{x.status === 'committed' ? '공식' : '탐색(공식화 전)'}</span>
                <span className="ml-auto text-card-faint">{x.by} · {ago(x.at)}</span>
              </div>
              <div className="mt-1.5 text-[13.5px] font-semibold">{x.topic}</div>
              <div className="mt-0.5 text-[13.5px]">→ {x.chosen}</div>
              {x.rationale && <div className="mt-1 text-[12.5px] text-card-sub">근거: {x.rationale}</div>}
              {(x.evidence.length > 0 || x.resolves.length > 0) && <div className="mt-1 text-[11px] text-card-faint">{x.evidence.length ? `근거 ${x.evidence.join(', ')}` : ''}{x.resolves.length ? ` · 해소 ${x.resolves.length}건` : ''}</div>}
              {x.status === 'committed' && <div className="mt-1.5 text-[11.5px] font-semibold text-ok">✔ 공식화됨 · {x.committed?.roleLabel} {x.committed?.by} · {ago(x.committed?.at)}{x.committed?.note ? ` — ${x.committed.note}` : ''}</div>}
              {!x.supersededBy && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <Btn small onClick={() => startDecision({ topic: x.topic, chosen: '', supersedes: x.id, section: x.section })}>이 결정 바꾸기</Btn>
                  {x.status !== 'committed' && <CommitBtn deal={deal} d={x} roles={data.ssow?.roles} done={refresh} />}
                </div>
              )}
            </div>
          ) : (
            <div key={x.id} className="rounded-xl bg-raise/60 p-3">
              <div className="flex items-center gap-2 text-[11px]">
                <span className={`rounded px-1.5 py-0.5 font-bold ${KIND_TONE[x.kind]}`}>{KIND[x.kind]}</span>
                {x.byAgent && <span className="text-card-faint">메리</span>}
                {x.section && <span className="text-card-faint">{secTitle(x.section)}</span>}
                {x.status === 'open' && <span className="font-semibold text-warn">미해결</span>}
                {x.status === 'resolved' && <span className="text-ok">해소됨</span>}
                {x.status === 'answered' && <span className="text-card-faint">답함</span>}
                <span className="ml-auto text-card-faint">{x.by} · {ago(x.at)}</span>
              </div>
              <div className="mt-1.5 whitespace-pre-wrap text-[13px]">{x.text}</div>
              {x.replyTo && <div className="mt-1 text-[11px] text-card-faint">↳ {short(deal.items.find((q) => q.id === x.replyTo)?.text, 70)}</div>}
              {x.status === 'open' && x.options?.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {x.options.map((o) => <Btn key={o} small onClick={() => act(() => call('addItem', deal.id, { kind: 'answer', text: o, replyTo: x.id, section: x.section }))}>{o}</Btn>)}
                </div>
              )}
              <div className="mt-2 flex gap-1.5">
                {(x.kind === 'question' || x.kind === 'issue') && x.status !== 'resolved' && (
                  <Btn small onClick={() => startDecision({ topic: x.text.split('\n')[0], resolves: [x.id], evidence: [x.id], section: x.section })}>결정으로 정리</Btn>
                )}
                {x.kind === 'answer' && <Btn small onClick={() => { const q = deal.items.find((i) => i.id === x.replyTo); startDecision({ topic: q?.text.split('\n')[0] || '', chosen: x.text, resolves: q ? [q.id] : [], evidence: [x.id], section: x.section }); }}>결정으로 남기기</Btn>}
              </div>
            </div>
          )))}
          {!timeline.length && <div className="py-10 text-center text-[13px] text-card-faint">아직 논의가 없어요. 오른쪽에서 쟁점이나 질문을 남기거나, 보고서 탭에서 "확인할 질문 만들기"를 눌러 보세요.</div>}
        </div>
      </div>

      <aside className="min-h-0 space-y-4 overflow-auto border-l border-card-line p-4">
        {dc ? (
          <div className="space-y-2">
            <div className="text-[13px] font-bold">{dc.supersedes ? '결정 바꾸기' : '결정 남기기'}</div>
            <div className="text-[11.5px] text-card-faint">무엇을 · 어떻게 · 왜. 업무 지도에 누가 언제 무엇을 근거로 정했는지 남습니다.</div>
            <Field placeholder="무엇을 정했나 (예: 적용 PER 밴드)" value={dc.topic} onChange={(e) => setDc({ ...dc, topic: e.target.value })} />
            <Area rows={2} placeholder="어떻게 (예: 13 / 15 / 17배)" value={dc.chosen} onChange={(e) => setDc({ ...dc, chosen: e.target.value })} />
            <Area rows={3} placeholder="왜 (예: Peer 평균 21.4배에 코스닥 신규 상장 할인율 22~34% 반영)" value={dc.rationale} onChange={(e) => setDc({ ...dc, rationale: e.target.value })} />
            <select value={dc.section} onChange={(e) => setDc({ ...dc, section: e.target.value })} className="h-8 w-full rounded-lg bg-field px-2 text-[13px] ring-1 ring-field-line">
              <option value="">섹션(선택)</option>
              {template.sections.map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}
            </select>
            <div className="text-[11px] text-card-faint">{dc.evidence.length ? `근거: ${dc.evidence.join(', ')}` : '근거 없음'}{dc.resolves.length ? ` · 해소: ${dc.resolves.length}건` : ''}</div>
            <div className="flex gap-1.5">
              <Btn primary disabled={busy || !dc.topic.trim() || !dc.chosen.trim()} onClick={() => act(async () => { await call('decide', deal.id, { ...dc, section: dc.section || null, supersedes: dc.supersedes || null }); setDc(null); })}>결정 기록</Btn>
              <Btn onClick={() => setDc(null)}>취소</Btn>
            </div>
          </div>
        ) : (
          <>
            <div className="space-y-2">
              <div className="text-[13px] font-bold">논의 남기기</div>
              <div className="flex flex-wrap gap-1">
                {['issue', 'question', 'bull', 'bear', 'comment'].map((k) => (
                  <button key={k} onClick={() => setKind(k)} className={`rounded-md px-2 py-1 text-[11.5px] font-semibold ${kind === k ? 'bg-card-ink text-card' : 'ring-1 ring-card-line'}`}>{KIND[k]}</button>
                ))}
              </div>
              <Area rows={4} placeholder="예: 심사역 추정 원가율이 회사 제시(18%)보다 높은 근거를 IR 때 확인" value={text} onChange={(e) => setText(e.target.value)} />
              <select value={section} onChange={(e) => setSection(e.target.value)} className="h-8 w-full rounded-lg bg-field px-2 text-[13px] ring-1 ring-field-line">
                <option value="">섹션(선택)</option>
                {template.sections.map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}
              </select>
              <Btn primary disabled={busy || !text.trim()} onClick={() => act(async () => { await call('addItem', deal.id, { kind, text, section: section || null }); setText(''); })}>남기기</Btn>
            </div>
            <div className="border-t border-card-line pt-4">
              <Btn onClick={() => startDecision()}><Icon name="check" className="size-3.5" />결정 남기기</Btn>
            </div>
          </>
        )}
        {err && <div className="text-[12px] text-accent">{err}</div>}
      </aside>
    </div>
  );
}

// ── 근거·자료 ──
function FactsTab({ data, busy, run, refresh }) {
  const { deal } = data;
  const [q, setQ] = useState('');
  const list = deal.facts.filter((f) => !q || `${f.label} ${f.key} ${f.value} ${f.quote}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="min-h-0 flex-1 overflow-auto p-5">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-[15px] font-bold">자료</h2>
        <Btn small onClick={async () => { await call('attach', deal.id); refresh(); }} disabled={busy}><Icon name="plus" className="size-3" />자료 올리기 (PDF) — 메리가 바로 읽음</Btn>
      </div>
      <div className="mb-5 space-y-1.5">
        {deal.sources.map((s) => {
          const n = deal.facts.filter((f) => f.sourceId === s.id).length;
          return (
            <div key={s.id} className="flex items-center gap-2 rounded-lg px-3 py-2 ring-1 ring-card-line">
              <Icon name="page" className="size-4 text-card-faint" />
              <span className="min-w-0 flex-1 truncate text-[13px]">{s.name}</span>
              <span className="text-[11.5px] text-card-faint">{Math.round(s.bytes / 1024)}KB · 근거 {n}</span>
              <Btn small onClick={() => run('extract', deal.id, s.id)} disabled={busy}>{n ? '다시 뽑기' : '근거 뽑기'}</Btn>
            </div>
          );
        })}
        {!deal.sources.length && <div className="text-[13px] text-card-faint">IR 자료, 재무제표, 투자계약서 PDF를 올리면 메리가 쪽 번호와 원문 인용이 달린 근거로 뽑습니다.</div>}
      </div>
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-[15px] font-bold">근거 {deal.facts.length}</h2>
        <div className="ml-auto w-64"><Field placeholder="찾기" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      <table className="w-full text-[12.5px]">
        <thead><tr className="border-b border-card-line text-left text-[11px] text-card-faint"><th className="py-1.5 pr-2">이름</th><th className="pr-2">값</th><th className="pr-2">시점</th><th className="pr-2">쪽</th><th>원문</th></tr></thead>
        <tbody>
          {list.slice(0, 400).map((f) => (
            <tr key={f.id} className="border-b border-card-line/60 align-top">
              <td className="py-1.5 pr-2">
                <div className="font-semibold">{f.basis === 'claim' && <span className="mr-1 rounded bg-warn/15 px-1 text-[10px] text-warn">미확인 주장</span>}{f.basis === 'registry' && <span className="mr-1 rounded bg-info/10 px-1 text-[10px] text-info">FRN 등록</span>}{f.basis === 'web' && <span className="mr-1 rounded bg-ok/10 px-1 text-[10px] text-ok">웹 공개자료</span>}{f.label}</div>
                {f.url && <a href={f.url} onClick={(e) => { e.preventDefault(); a2a.send('bookmark:open', f.url); }} className="block truncate text-[10.5px] text-info hover:underline">{f.url}</a>}
                <div className="text-[10.5px] text-card-faint">{f.id}</div>
              </td>
              <td className="pr-2 tabular-nums">{typeof f.value === 'number' ? f.value.toLocaleString('ko-KR') : f.value}{f.unit ? ` ${f.unit}` : ''}</td>
              <td className="pr-2 text-card-sub">{f.period}</td>
              <td className="pr-2 text-card-sub">{f.page ?? ''}</td>
              <td className="text-card-sub">{f.quote}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── 가정·계산 ── 잠근 가정만 보고서 숫자가 된다
function PackTab({ data, busy, run, refresh }) {
  const { deal, vars, checks } = data;
  const last = deal.packs[deal.packs.length - 1];
  const [text, setText] = useState(() => (last ? JSON.stringify(last.pack, null, 2) : ''));
  const [gaps, setGaps] = useState([]);
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const draft = async () => { setErr(''); const r = await run('draftPack', deal.id); if (r?.pack) { setText(JSON.stringify(r.pack, null, 2)); setGaps(r.gaps || []); } };
  const lock = async () => {
    setErr('');
    let pack;
    try { pack = JSON.parse(text); } catch { setErr('JSON 모양이 맞지 않아요'); return; }
    try { await call('lockPack', deal.id, pack, { note }); setNote(''); refresh(); } catch (e) { setErr(String(e.message || e)); }
  };
  return (
    <div className="grid min-h-0 flex-1 grid-cols-2">
      <div className="flex min-h-0 flex-col gap-2 border-r border-card-line p-5">
        <div className="flex items-center gap-2">
          <h2 className="text-[15px] font-bold">가정 {last ? `v${last.version} 잠김` : ''}</h2>
          <Btn small onClick={draft} disabled={busy || !deal.facts.length} title="근거와 결정으로 초안을 만듭니다(숫자를 추측하지 않음)">근거로 초안 만들기</Btn>
        </div>
        {gaps.length > 0 && <div className="rounded-lg bg-raise p-2.5 text-[12px]"><b>근거가 없는 칸</b>{gaps.map((g) => <div key={g}>• {g}</div>)}</div>}
        <Area className="min-h-0 flex-1 font-mono text-[11.5px]" value={text} onChange={(e) => setText(e.target.value)} placeholder='{"deal":{...},"pnl":{...},"valuation":{...}}' />
        <div className="flex gap-2">
          <Field placeholder="잠그는 이유(예: 본투심 전 심사역 추정 확정)" value={note} onChange={(e) => setNote(e.target.value)} />
          <Btn primary onClick={lock} disabled={busy || !text.trim()}><Icon name="lock" className="size-3.5" />잠그고 계산</Btn>
        </div>
        {err && <div className="text-[12px] text-accent">{err}</div>}
      </div>
      <div className="min-h-0 overflow-auto p-5">
        <h2 className="mb-2 text-[15px] font-bold">검산</h2>
        <div className="mb-4 space-y-1 text-[12.5px]">
          {(checks || []).map((c) => <div key={c.id} className={c.status === 'fail' ? 'text-accent' : c.status === 'warn' ? 'text-warn' : 'text-ok'}>{c.status === 'pass' ? '✓' : c.status === 'warn' ? '△' : '✕'} {c.message}</div>)}
          {!checks?.length && <div className="text-card-faint">가정을 잠그면 계산하고 표끼리 맞는지 검산합니다.</div>}
        </div>
        <h2 className="mb-2 text-[15px] font-bold">계산 변수 <span className="text-[12px] font-normal text-card-faint">보고서는 숫자를 {'{{var:이름}}'}으로만 부릅니다</span></h2>
        <table className="w-full text-[12px]">
          <tbody>
            {Object.entries(vars || {}).map(([k, x]) => (
              <tr key={k} className="border-b border-card-line/60 align-top">
                <td className="py-1 pr-2 font-mono text-[11px]">{k}</td>
                <td className="pr-2 text-right font-semibold tabular-nums">{x.display}</td>
                <td className="text-card-faint">{x.formula}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const short = (s, n) => { s = String(s ?? ''); return s.length > n ? `${s.slice(0, n)}…` : s; };

// 메리 안의 좌우 폭(투자 건 목록·대화창) — 이 화면에만 기억한다(localStorage, 실패해도 기본 폭)
function useWidth(key, def, min, max) {
  const read = () => { try { const v = Number(localStorage.getItem(key)); return Number.isFinite(v) && v >= min && v <= max ? v : def; } catch { return def; } };
  const [w, setW] = useState(read);
  const [live, setLive] = useState(null);
  const clamp = (v) => Math.round(Math.min(max, Math.max(min, v)));
  return {
    w: live ?? w,
    drag: (dx, dir = 1) => setLive(clamp(w + dir * dx)),
    end: (dx, dir = 1) => { const v = clamp(w + dir * dx); setW(v); setLive(null); try { localStorage.setItem(key, String(v)); } catch { /* 저장 못 해도 이번엔 적용 */ } },
    reset: () => { setW(def); setLive(null); try { localStorage.removeItem(key); } catch { /* 그만 */ } },
  };
}

export default function Merry() {
  const listW = useWidth('merry.listWidth', 230, 180, 420);
  const chatW = useWidth('merry.chatWidth', 380, 300, 720);
  const [deals, setDeals] = useState([]);
  const [sel, setSel] = useState(null);
  const [data, setData] = useState(null);
  const [tab, setTab] = useState('report');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [live, setLive] = useState({ thinking: '', label: '', draft: null, draftSection: null, reply: '', step: '' });
  const [target, setTarget] = useState(null);
  const selRef = useRef(sel);
  selRef.current = sel;
  const [err, setErr] = useState('');
  const [form, setForm] = useState(null);
  const [intake, setIntake] = useState(null);       // { text, instructor, state, error }

  const refresh = useCallback(async () => {
    const list = await call('list');
    setDeals(list);
    const id = sel || list[0]?.id || null;
    if (id !== sel) setSel(id);
    setData(id ? await call('deal', id) : null);
  }, [sel]);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    const a = a2a.on('invest:changed', () => { setLive((l) => (l.thinking ? { ...l, thinking: '' } : l)); refresh(); });
    const b = a2a.on('invest:progress', (p) => {
      if (!p) return;
      if (p.type === 'intake') {
        if (p.state === 'deal') { setSel(p.dealId); setTab('report'); setIntake(null); }
        else if (p.state === 'error') setIntake((x) => ({ ...(x || {}), state: 'error', error: p.text }));
        else if (p.state === 'done') setIntake((x) => (x?.state === 'error' ? x : null));
        refresh();
        return;
      }
      if (p.dealId && p.dealId !== selRef.current) return;
      if (p.type === 'thinking') setLive((l) => ({ ...l, thinking: (l.label === p.label ? l.thinking : '') + p.delta, label: p.label }));
      else if (p.type === 'draft') setLive((l) => ({ ...l, draft: p.html, draftSection: p.html ? p.section : null }));
      else if (p.type === 'reply') setLive((l) => ({ ...l, reply: p.text || '' }));
      else if (p.type === 'step') setLive((l) => ({ ...l, step: p.text }));
      else if (p.type === 'idle') { setLive({ thinking: '', label: '', draft: null, draftSection: null, reply: '', step: '' }); refresh(); }
      else if (p.type === 'busy' || p.type === 'ask') refresh();
      else if (p.text) setProgress(p.text);
    });
    return () => { a(); b(); };
  }, [refresh]);

  const run = useCallback(async (name, ...args) => {
    setBusy(true); setErr('');
    try { const r = await call(name, ...args); await refresh(); return r; }
    catch (e) { setErr(String(e.message || e).replace(/^Error invoking remote method '[^']+': (Error: )?/, '')); return null; }
    finally { setBusy(false); setProgress(''); }
  }, [refresh]);

  const stages = data?.template.stages || [];
  // 카드 크기에 고정(absolute) — 셸 격자 행은 높이 제한이 없어서, 흐름에 두면 긴 문서만큼 카드가 늘어나 스크롤이 안 된다
  return (
    <div className="absolute inset-0 flex min-h-0">
      <aside className="relative flex shrink-0 flex-col border-r border-card-line" style={{ width: listW.w }}>
        <Resizer id="resize-merry-list" className="absolute -right-[4px] bottom-0 top-0" onDrag={(dx) => listW.drag(dx)} onEnd={(dx) => listW.end(dx)} onReset={listW.reset} />
        <div className="flex items-center gap-2 px-4 pb-2 pt-4">
          <div className="text-[16px] font-extrabold tracking-tight">메리</div>
          <div className="text-[11px] text-card-faint">투자 딜룸</div>
          <button className="ml-auto rounded-md p-1 hover:bg-raise" title="새 투자 건" onClick={() => setForm({ company: '', fund: '', amount: '', method: '' })}><Icon name="plus" className="size-4" /></button>
        </div>
        {/* FRN 연결 — 대표·앵커의 지시를 그대로 붙여 넣으면 검토를 연다 */}
        <div className="mx-3 mb-2 rounded-xl bg-raise/70 p-2.5">
          {intake?.state === 'running' ? (
            <div className="flex items-center gap-1.5 text-[12px] text-card-sub"><Icon name="spark" className="size-3.5 animate-twinkle" />지시를 읽고 FRN을 찾는 중…</div>
          ) : (
            <>
              <textarea rows={intake ? 6 : 2} value={intake?.text || ''} onFocus={() => setIntake((x) => x || { text: '', instructor: '' })}
                        onChange={(e) => setIntake((x) => ({ ...(x || {}), text: e.target.value }))}
                        placeholder="지시 붙여넣기 — 예: (주)○○ FRN: AGF-KR-00-001 … 검토해보면 어떨까요?"
                        className="w-full resize-none rounded-lg bg-field px-2 py-1.5 text-[12px] ring-1 ring-field-line outline-none focus:ring-card-faint" />
              {intake && (
                <div className="mt-1.5 space-y-1.5">
                  <Field placeholder="지시한 사람(예: 대표)" value={intake.instructor || ''} onChange={(e) => setIntake({ ...intake, instructor: e.target.value })} />
                  <div className="flex gap-1.5">
                    <Btn primary small disabled={!intake.text?.trim()} onClick={async () => { setIntake({ ...intake, state: 'running' }); try { await call('intake', intake.text, { instructor: intake.instructor, channel: 'slack' }); } catch (e) { setIntake({ ...intake, state: 'error', error: String(e.message || e) }); } }}>메리에게 검토 맡기기</Btn>
                    <Btn small onClick={() => setIntake(null)}>닫기</Btn>
                  </div>
                  {intake.state === 'error' && <div className="text-[11.5px] text-accent">{intake.error}</div>}
                  <div className="text-[10.5px] leading-snug text-card-faint">FRN이 있으면 EMA 등록 정보를 붙이고, 지시 속 숫자는 확인 전 주장으로만 둡니다. 결론 대신 받을 자료부터 정리해요.</div>
                </div>
              )}
            </>
          )}
        </div>
        {form && (
          <div className="mx-3 mb-2 space-y-1.5 rounded-xl p-2.5 ring-1 ring-card-line">
            <Field placeholder="회사 이름" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} autoFocus />
            <Field placeholder="투자 계정(펀드)" value={form.fund} onChange={(e) => setForm({ ...form, fund: e.target.value })} />
            <Field placeholder="투자 금액(원)" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value.replace(/[^\d]/g, '') })} />
            <Field placeholder="투자 방식(예: 신주 / RCPS)" value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })} />
            <div className="flex gap-1.5">
              <Btn primary small disabled={!form.company.trim()} onClick={async () => { const d = await run('create', form); setForm(null); if (d) setSel(d.id); }}>만들기</Btn>
              <Btn small onClick={() => setForm(null)}>취소</Btn>
            </div>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-auto px-2">
          {deals.map((d) => (
            <button key={d.id} onClick={() => setSel(d.id)} className={`mb-0.5 w-full rounded-lg px-2.5 py-2 text-left ${d.id === sel ? 'bg-raise' : 'hover:bg-raise/60'}`}>
              <div className="truncate text-[13px] font-semibold">{d.company}</div>
              <div className="truncate text-[11px] text-card-faint">{stages.find((s) => s.key === d.stage)?.label || d.stage} · 섹션 {d.sections}/{d.total - 1} · 미해결 {d.open}</div>
            </button>
          ))}
          {!deals.length && !form && <div className="px-3 py-6 text-[12.5px] text-card-faint">+를 눌러 첫 투자 건을 만드세요.</div>}
        </div>
      </aside>

      {data ? (
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="border-b border-card-line px-5 pb-0 pt-3.5">
            <div className="flex items-baseline gap-3">
              <h1 className="text-[18px] font-extrabold tracking-tight">{data.deal.company}</h1>
              <WorkIdBar deal={data.deal} refresh={refresh} />
              <span className="text-[12px] text-card-faint">{[data.deal.fund, data.deal.method, data.vars.invest_amount?.display].filter(Boolean).join(' · ')}</span>
            </div>
            <div className="mt-2 flex items-center gap-1">
              {stages.filter((s) => s.key !== 'dropped').map((s, i, arr) => {
                const cur = arr.findIndex((x) => x.key === data.deal.stage);
                return (
                  <button key={s.key} disabled={busy} onClick={() => run('setStage', data.deal.id, s.key)}
                          className={`flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold ${i === cur ? 'bg-card-ink text-card' : i < cur ? 'text-card-ink' : 'text-card-faint'} hover:ring-1 hover:ring-card-line`}>
                    {i < cur && <Icon name="check" className="size-3" />}{s.label}{data.deal.stageDates?.[s.key] && <span className="font-normal opacity-70">{data.deal.stageDates[s.key].slice(5)}</span>}
                  </button>
                );
              })}
            </div>
            <nav className="mt-2 flex gap-4 text-[13px]">
              {[['report', '보고서'], ['discuss', `논의·결정 ${data.deal.items.filter((x) => x.status === 'open').length || ''}`], ['facts', `근거·자료 ${data.deal.facts.length || ''}`], ['pack', '가정·계산']].map(([k, l]) => (
                <button key={k} onClick={() => setTab(k)} className={`border-b-2 pb-2 font-semibold ${tab === k ? 'border-card-ink' : 'border-transparent text-card-faint hover:text-card-ink'}`}>{l}</button>
              ))}
              {(busy || progress) && <span className="ml-auto flex items-center gap-1.5 pb-2 text-[12px] text-card-sub"><Icon name="spark" className="size-3.5 animate-twinkle" />{progress || '처리 중…'}</span>}
              {err && !busy && <span className="ml-auto pb-2 text-[12px] text-accent">{err}</span>}
            </nav>
          </header>
          {tab === 'report' && <ReportTab data={data} live={live} target={target} setTarget={setTarget} run={run} refresh={refresh} chatW={chatW} />}
          {tab === 'discuss' && <DiscussTab data={data} busy={busy} refresh={refresh} />}
          {tab === 'facts' && <FactsTab data={data} busy={busy} run={run} refresh={refresh} />}
          {tab === 'pack' && <PackTab key={data.deal.packs.length} data={data} busy={busy} run={run} refresh={refresh} />}
        </div>
      ) : <div className="grid flex-1 place-items-center text-[13px] text-card-faint">투자 건을 고르거나 새로 만드세요.</div>}
    </div>
  );
}
