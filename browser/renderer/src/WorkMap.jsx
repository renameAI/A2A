// 업무 지도 — 업무 흐름 온톨로지를 옵시디언처럼 둘러본다(ontology.js · harness.js).
//   왼쪽: 찾기 · 유형 · 목록 (탐색) / 신호·교훈·하네스 버전 (개선)
//   가운데: 노트 — 속성, 관계([[링크]]), 백링크, 원문, 주장 튜플(s,r,o,I,b,m,π,v,c,ζ)
//   오른쪽: 그래프 — 고른 노트 주변(깊이 2) 또는 전체
// 신호·교훈·하네스도 같은 그래프의 노드라 노트 머리에 그 자리의 동작(교훈 제안·재실행 비교·채택)이 붙는다.
import { useCallback, useEffect, useMemo, useState } from 'react';
import Icon from './Icon.jsx';
import GraphView, { TYPE_COLOR } from './GraphView.jsx';
import { a2a, useStore } from './store.js';
import { ago } from './format.js';

const call = (name, ...args) => a2a.invoke('onto:call', name, ...args);
const Dot = ({ type, className = 'size-2' }) => <span className={`inline-block shrink-0 rounded-full ${className}`} style={{ background: TYPE_COLOR[type] || '#94a3b8' }} />;

function Btn({ children, onClick, primary, disabled, title, id }) {
  return (
    <button id={id} title={title} disabled={disabled} onClick={onClick}
            className={`inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-xs font-semibold disabled:opacity-40 ${primary ? 'bg-card-ink text-card' : 'ring-1 ring-card-line hover:bg-raise'}`}>
      {children}
    </button>
  );
}

function Link({ node, onOpen }) {
  if (!node) return <span className="text-card-faint">(없음)</span>;
  return (
    <button onClick={() => onOpen(node.id)} title={node.id}
            className="inline-flex max-w-full items-center gap-1.5 rounded px-1 text-left text-[13px] text-[#6d4bd8] hover:bg-raise dark:text-[#b9a6ff]">
      <Dot type={node.type} /><span className="truncate">[[{node.label}]]</span>
    </button>
  );
}

// 관계 묶음 — 같은 관계끼리
function Relations({ title, edges, onOpen, empty }) {
  const groups = useMemo(() => {
    const m = new Map();
    for (const e of edges) (m.get(e.rel) || m.set(e.rel, []).get(e.rel)).push(e);
    return [...m.entries()];
  }, [edges]);
  return (
    <section className="mt-5">
      <h3 className="mb-1.5 text-[12px] font-semibold text-card-sub">{title} <span className="font-normal text-card-faint">{edges.length}</span></h3>
      {!edges.length && <div className="text-xs text-card-faint">{empty}</div>}
      {groups.map(([rel, es]) => (
        <div key={rel} className="mb-1.5 flex gap-2">
          <span className="w-24 shrink-0 pt-0.5 text-[12px] text-card-faint">{rel}</span>
          <div className="flex min-w-0 flex-1 flex-wrap gap-x-1 gap-y-0.5">
            {es.slice(0, 40).map((e) => <Link key={e.id} node={e.node} onOpen={onOpen} />)}
            {es.length > 40 && <span className="text-xs text-card-faint">외 {es.length - 40}</span>}
          </div>
        </div>
      ))}
    </section>
  );
}

// 노트 머리의 동작 — 신호·교훈·하네스 버전
function Actions({ note, harness, onOpen, busy, run }) {
  if (!harness) return null;
  if (note.type === 'signal') {
    const sig = harness.signals.find((s) => s.id === note.id);
    if (!sig) return null;
    if (sig.locked) return <div className="text-xs text-warn">가드레일(승인) 쪽 신호라 자동 제안을 하지 않아요 — 사람이 검토해 주세요</div>;
    return sig.lesson
      ? <Btn onClick={() => onOpen(sig.lesson)}>교훈 보기</Btn>
      : <Btn id="act-propose" primary disabled={busy} onClick={() => run(() => call('propose', sig.id), (l) => onOpen(l.id))}><Icon name="spark" className="size-3.5" />교훈 제안(Claude)</Btn>;
  }
  if (note.type === 'lesson') {
    const l = harness.lessons.find((x) => x.id === note.id);
    if (!l) return null;
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-card-sub">적용: <b>{l.worktype ? `업무 유형 · ${harness.worktypes?.find((t) => t.id === l.worktype)?.name || l.worktype}` : '전체'}</b> · 상태: <b>{{ proposed: '제안됨', probed: '재실행함', adopted: '채택됨', rejected: '거절됨' }[l.status] || l.status}</b>
          {l.verdict && <> · 재실행: <b>{l.verdict === 'B' ? '후보 우세' : l.verdict === 'A' ? '기준 우세' : '비슷함'}</b>{l.fixed ? ' · 신호 해결' : ''}</>}</span>
        {l.task && l.status !== 'adopted' && <Btn id="act-probe" disabled={busy} onClick={() => run(() => call('probe', l.id), (p) => onOpen(p.id))}><Icon name="flask" className="size-3.5" />재실행 비교{l.model ? ` (${harness.models?.[l.model]?.label || l.model})` : ''}</Btn>}
        {l.status !== 'adopted' && <Btn id="act-adopt" primary disabled={busy} onClick={() => run(() => call('adopt', l.id), (v) => onOpen(`harness:${v.id}`))}>채택</Btn>}
        {l.status !== 'adopted' && l.status !== 'rejected' && <Btn id="act-reject" disabled={busy} onClick={() => run(() => call('reject', l.id))}>거절</Btn>}
      </div>
    );
  }
  // 업무 유형 — 이 유형을 어떤 모델로 실행할지(재실행 비교로 정한다)
  if (note.type === 'worktype') {
    const t = harness.worktypes?.find((x) => x.id === note.id);
    if (!t) return null;
    const M = harness.models || {};
    const cur = t.model || harness.baseModel;
    const other = Object.keys(M).find((m) => m !== cur);
    const probes = harness.probes.filter((p) => p.kind === 'model' && p.worktype === t.id && p.status === 'done');
    const lastB = probes.filter((p) => p.models?.b === other);
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-card-sub">실행 모델: <b>{M[cur]?.label || cur}</b>{t.model ? ' (이 유형 설정)' : ' (기본)'} · 지난 작업 {t.runs}개
          {lastB.length > 0 && <> · {M[other]?.label} 비교 {lastB.length}번: {lastB.filter((p) => p.verdict?.b_ok).length}번 맡겨도 됨</>}</span>
        {other && <Btn id="act-probe-model" disabled={busy || !t.runs} onClick={() => run(() => call('probeType', t.id, other), (ps) => ps?.at(-1) && onOpen(ps.at(-1).id))}><Icon name="flask" className="size-3.5" />{M[other].label}로 재실행 비교</Btn>}
        {other && <Btn id="act-set-model" primary={lastB.some((p) => p.verdict?.b_ok)} disabled={busy} onClick={() => run(() => call('setTypeModel', t.id, other))}>이 유형은 {M[other].label}로</Btn>}
        {t.model && <Btn disabled={busy} onClick={() => run(() => call('setTypeModel', t.id, null))}>기본 모델로</Btn>}
        {harness.types?.[t.id] && <span className="text-xs text-card-sub">· 이 유형 교훈 {harness.types[t.id].lessons.length}개 채택</span>}
        {harness.types?.[t.id] && <Btn disabled={busy} onClick={() => run(() => call('clearTypeEdit', t.id))}>유형 교훈 비우기</Btn>}
      </div>
    );
  }
  if (note.type === 'harness' && note.id.startsWith('harness:v')) {
    const vid = note.id.slice(8);
    return harness.active === vid
      ? <Btn disabled={busy} onClick={() => run(() => call('rollback', null))}>기본 하네스로 되돌리기</Btn>
      : <Btn primary disabled={busy} onClick={() => run(() => call('rollback', vid))}>이 버전 쓰기</Btn>;
  }
  return null;
}

function LessonDetail({ id, harness }) {
  const l = harness?.lessons.find((x) => x.id === id);
  if (!l) return null;
  return (
    <section className="mt-5 rounded-xl bg-raise/60 p-3 text-[13px]">
      <div className="font-semibold">{l.text}</div>
      {l.when && <div className="mt-1 text-xs text-card-sub">언제: {l.when}</div>}
      <pre className="mt-2 max-h-56 overflow-auto rounded-lg bg-card p-2 text-[11.5px] leading-relaxed whitespace-pre-wrap">{JSON.stringify(l.edit, null, 2)}</pre>
      <div className="mt-1 text-[11px] text-card-faint">하네스에 들어가는 것은 위 수정안뿐이에요. 안전 규칙(승인·비밀번호·데이터 경계)은 잠겨 있어 바뀌지 않아요.</div>
    </section>
  );
}
function ProbeDetail({ id, harness, onOpen, busy, run }) {
  const p = harness?.probes.find((x) => x.id === id);
  if (!p) return null;
  const M = harness.models || {};
  const isModel = p.kind === 'model';
  const on = p.model ? ` (${M[p.model]?.label || p.model})` : '';
  const nameA = isModel ? `A · ${M[p.models?.a]?.label || p.models?.a}` : `A · 기준 하네스${on}`;
  const nameB = isModel ? `B · ${M[p.models?.b]?.label || p.models?.b}` : `B · 후보 하네스${on}`;
  const fromHere = harness.lessons.find((l) => l.fromProbe === p.id);
  const side = (name, r) => (
    <div className="min-w-0 flex-1 rounded-xl bg-raise/60 p-3">
      <div className="text-[12px] font-semibold">{name}</div>
      <div className="mt-1 text-[11.5px] text-card-sub">도구: {r?.tools?.join(' → ') || '(없음)'}</div>
      {r?.usage && <div className="text-[11.5px] text-card-sub">입력 {r.usage.input + r.usage.cacheWrite + r.usage.cacheRead}·출력 {r.usage.output} 토큰 · {Math.round(r.usage.ms / 1000)}초{r.usage.cost != null ? ` · 약 $${r.usage.cost}` : ''}</div>}
      <div className="mt-2 max-h-48 overflow-auto text-[12px] whitespace-pre-wrap">{r?.text || ''}</div>
    </div>
  );
  return (
    <section className="mt-5">
      {p.status === 'running' && <div className="text-xs text-card-sub">재실행하는 중…</div>}
      {p.error && <div className="text-xs text-warn">{p.error}</div>}
      {p.verdict && <div className="mb-2 text-[13px]"><b>판정: {p.verdict.winner === 'tie' ? '비슷함' : `${(p.verdict.winner === 'A' ? nameA : nameB).slice(4)} 우세`}{isModel ? (p.verdict.b_ok ? ' · B에 맡겨도 됨' : ' · B에 맡기기 어려움') : ''}</b> — {p.verdict.reason}
        {p.verdict.regressions?.length ? <div className="text-xs text-warn">새 문제: {p.verdict.regressions.join(' · ')}</div> : null}
        {isModel && p.worktype && p.verdict.regressions?.length > 0 && (fromHere
          ? <div className="mt-1.5"><Btn onClick={() => onOpen(fromHere.id)}>이 비교에서 나온 교훈 보기</Btn></div>
          : <div className="mt-1.5"><Btn id="act-propose-probe" primary disabled={busy} onClick={() => run(() => call('proposeFromProbe', p.id), (l) => onOpen(l.id))}><Icon name="spark" className="size-3.5" />B가 놓친 것으로 유형 교훈 제안</Btn></div>)}</div>}
      {p.baseline && <div className="flex gap-2">{side(nameA, p.baseline)}{side(nameB, p.candidate)}</div>}
    </section>
  );
}

// SSoT 임시 발행 — 업무 단위(작업·투자 건·결정·지시)의 임시 Work ID와 그 업무 유형의 SSoT(ssot.js). 정책 확정 전(provisional-v0)
const MODE_LABEL = { conversation: '탐색(대화)', shadow: 'AI 초안(shadow)', committed: '공식(committed)' };
function SsotBlock({ note, onIssued }) {
  const x = note.ssot;
  if (!x?.eligible) return null;
  const r = x.record, l = x.live;
  return (
    <div id="map-ssot" className="mt-4 rounded-xl p-3 ring-1 ring-card-line">
      <div className="mb-1.5 flex items-center gap-2 text-[12px]">
        <span className="font-semibold">SSoT</span>
        {r ? <span className="rounded bg-raise px-1.5 py-0.5 font-mono text-[12px] font-semibold">{r.id}</span> : <span className="text-card-faint">아직 발행 안 됨</span>}
        <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10.5px] text-warn" title="정책 확정 전 임시 ID — 정책이 확정되면 Registry 정식 ID로 옮깁니다">임시 · {r?.policy || 'provisional-v0'}</span>
        <span className="flex-1" />
        {!r && <Btn primary onClick={onIssued}>발행</Btn>}
      </div>
      {l && (
        <table className="w-full text-[12.5px]"><tbody>
          <tr><td className="w-28 py-0.5 text-card-faint">업무 유형</td><td>{l.worktype?.name || <span className="text-warn">업무 유형 미정</span>}</td></tr>
          <tr><td className="py-0.5 text-card-faint">원본 시스템</td><td>{l.ssot.system}{l.ssot.defaulted && <span className="ml-1.5 text-[11px] text-card-faint">(기본값 — 사람이 확정 전)</span>}</td></tr>
          <tr><td className="py-0.5 text-card-faint">기준 ID</td><td className="font-mono">{l.work.anchor || <span className="font-sans text-card-faint">{l.ssot.anchor ? `${l.ssot.anchor} 필요 — 아직 연결 안 됨` : '없음'}</span>}{l.work.prn && ` · PRN ${l.work.prn}`}{l.work.fund_id && ` · fund ${l.work.fund_id}`}</td></tr>
          <tr><td className="py-0.5 text-card-faint">계층</td><td className={l.mode === 'committed' ? 'font-semibold text-ok' : ''}>{MODE_LABEL[l.mode] || l.mode}{l.status ? ` · 상태 ${l.status}` : ''}</td></tr>
          <tr><td className="py-0.5 text-card-faint">가드</td><td>{(l.ssot.guards || []).join(' · ')}</td></tr>
          {r && <tr><td className="py-0.5 text-card-faint">발행</td><td>{r.issued_by === 'auto' ? '자동' : r.issued_by} · {ago(Date.parse(r.issued_at))}{r.at_issue?.mode && r.at_issue.mode !== l.mode && <span className="ml-1.5 text-card-faint">(발행 때 계층: {MODE_LABEL[r.at_issue.mode]})</span>}</td></tr>}
        </tbody></table>
      )}
    </div>
  );
}

function Note({ id, onOpen, harness, busy, run }) {
  const [note, setNote] = useState(null);
  const [claims, setClaims] = useState(false);
  useEffect(() => { let off = false; if (id) call('node', id).then((n) => { if (!off) setNote(n); }); return () => { off = true; }; }, [id, harness]);
  if (!id) return <div className="grid h-full place-items-center text-sm text-card-faint">왼쪽에서 노트를 고르거나 그래프의 점을 눌러 보세요</div>;
  if (!note) return <div className="p-6 text-sm text-card-faint">불러오는 중…</div>;
  const props = Object.entries(note.props || {}).filter(([k]) => !['hash', 'ihash'].includes(k));
  return (
    <article id="map-note" className="mx-auto max-w-[760px] px-7 py-6">
      <div className="mb-1 flex items-center gap-2 text-[12px] text-card-sub"><Dot type={note.type} />{note.typeLabel}<span className="text-card-faint">· {note.id.slice(0, 48)}</span></div>
      <h1 className="text-[22px] leading-snug font-bold">{note.label}</h1>
      <div className="mt-1 text-[11.5px] text-card-faint">{note.first && `처음 ${ago(Date.parse(note.first))}`}{note.last && note.last !== note.first && ` · 마지막 ${ago(Date.parse(note.last))}`}</div>
      <div className="mt-3"><Actions note={note} harness={harness} onOpen={onOpen} busy={busy} run={run} /></div>
      <SsotBlock note={note} onIssued={() => call('ssotIssue', note.id).then(() => call('node', note.id)).then(setNote)} />
      {props.length > 0 && (
        <table className="mt-4 w-full text-[13px]"><tbody>
          {props.map(([k, v]) => (
            <tr key={k} className="border-t border-card-line/70 align-top"><td className="w-28 py-1 pr-2 text-card-faint">{k}</td>
              <td className="py-1 break-all">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</td></tr>
          ))}
        </tbody></table>
      )}
      {note.type === 'lesson' && <LessonDetail id={note.id} harness={harness} />}
      {note.type === 'probe' && <ProbeDetail id={note.id} harness={harness} onOpen={onOpen} busy={busy} run={run} />}
      <Relations title="관계" edges={note.out} onOpen={onOpen} empty="나가는 관계가 없어요" />
      <Relations title="백링크" edges={note.back} onOpen={onOpen} empty="이 노트를 가리키는 곳이 없어요" />
      {note.text && (
        <section className="mt-5">
          <h3 className="mb-1.5 text-[12px] font-semibold text-card-sub">원문 <span className="font-normal text-card-faint">마스킹된 기록 · {note.props.hash?.slice(7, 19)}</span></h3>
          <pre className="max-h-80 overflow-auto rounded-xl bg-raise/60 p-3 font-sans text-[12.5px] leading-relaxed whitespace-pre-wrap">{note.text}</pre>
        </section>
      )}
      <section className="mt-5">
        <button className="text-[12px] font-semibold text-card-sub hover:underline" onClick={() => setClaims((v) => !v)}>
          주장 튜플 {claims ? '▾' : '▸'} <span className="font-normal text-card-faint">(s, r, o, I, b, m, π, v, c, ζ) — 업무 흐름 기록이라 m=recorded</span>
        </button>
        {claims && (
          <div className="mt-2 overflow-auto"><table id="claims-table" className="w-full text-[11px]"><thead className="text-card-faint"><tr>
            {['s', 'r', 'o', 'b', 'π', 'v', 'c', 'ζ'].map((h) => <th key={h} className="px-1 text-left font-semibold">{h}</th>)}</tr></thead>
            <tbody>{note.out.slice(0, 60).map((e) => (
              <tr key={e.id} className="border-t border-card-line/60 align-top">
                <td className="px-1">{note.label.slice(0, 18)}</td><td className="px-1">{e.r}</td><td className="px-1">{e.node?.label.slice(0, 22)}</td>
                <td className="px-1 whitespace-nowrap">{e.b?.slice(5, 16).replace('T', ' ')}</td><td className="px-1">{e.pi ? `${e.pi.kind}${e.pi.end != null ? ` ${e.pi.start ?? 0}–${e.pi.end}` : ''}` : ''}</td>
                <td className="px-1 font-mono">{e.v?.slice(7, 15) || ''}</td><td className="px-1">{e.c?.task_id || ''}</td><td className="px-1">{e.zeta?.platform} · {e.zeta?.kind}</td>
              </tr>))}</tbody></table></div>
        )}
      </section>
    </article>
  );
}

export default function WorkMap() {
  const S = useStore((s) => s.S);
  const [summary, setSummary] = useState(null);
  const [mode, setMode] = useState('explore');           // explore | improve
  const [type, setType] = useState('task');
  const [q, setQ] = useState('');
  const [list, setList] = useState([]);
  const [hist, setHist] = useState({ stack: [], i: -1 });
  const [graph, setGraph] = useState(null);
  const [whole, setWhole] = useState(false);
  const [harness, setHarness] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [tick, setTick] = useState(0);
  const dark = typeof document !== 'undefined' && document.body.dataset.tone === 'dark';
  const sel = hist.stack[hist.i] || null;

  const open = useCallback((id) => setHist((h) => (h.stack[h.i] === id ? h : { stack: [...h.stack.slice(0, h.i + 1), id], i: h.i + 1 })), []);
  useEffect(() => {
    const off = a2a.on('onto:changed', () => setTick((t) => t + 1));
    const iv = setInterval(() => setTick((t) => t + 1), 6000);       // 새 업무 기록이 쌓이면 따라온다
    return () => { off(); clearInterval(iv); };
  }, []);
  useEffect(() => { call('summary').then(setSummary); call('harness').then(setHarness); }, [tick]);
  useEffect(() => { call('list', q ? null : type, q).then(setList); }, [type, q, tick]);
  useEffect(() => { call('graph', whole || !sel ? { limit: 350 } : { center: sel, depth: 2 }).then(setGraph); }, [sel, whole, tick]);
  useEffect(() => {
    if (!sel && summary?.recent?.length) open(summary.recent[0].id);
  }, [summary, sel, open]);

  const run = async (fn, then) => {
    setBusy(true); setMsg('');
    try { const r = await fn(); setTick((t) => t + 1); then?.(r); } catch (e) { setMsg(String(e.message || e).replace(/^Error invoking remote method 'onto:call': (Error: )?/, '')); }
    finally { setBusy(false); }
  };
  const types = summary ? Object.entries(summary.types).sort((a, b) => b[1] - a[1]) : [];
  const sync = S?.sync || { mode: 'off' };

  return (
    <div id="workmap" className="flex h-full min-h-0 text-card-ink">
      {/* 왼쪽 */}
      <aside className="flex w-[248px] shrink-0 flex-col border-r border-card-line">
        <div className="flex gap-1 p-2">
          {[['explore', '탐색'], ['improve', '개선']].map(([k, t]) => (
            <button key={k} id={`map-mode-${k}`} onClick={() => setMode(k)} className={`h-7 flex-1 rounded-lg text-xs font-semibold ${mode === k ? 'bg-raise' : 'text-card-sub hover:bg-raise'}`}>{t}</button>
          ))}
        </div>
        {mode === 'explore' ? (<>
          <div className="px-2"><div className="flex h-8 items-center gap-2 rounded-lg bg-raise px-2.5">
            <Icon name="search" className="size-3.5 text-card-faint" />
            <input id="map-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="노트 찾기" className="min-w-0 flex-1 bg-transparent text-[13px] outline-none" />
          </div></div>
          <div className="px-2 pt-2">
            {types.map(([t, n]) => (
              <button key={t} data-type={t} onClick={() => { setType(t); setQ(''); }}
                      className={`flex h-7 w-full items-center gap-2 rounded-lg px-2 text-[12.5px] ${!q && type === t ? 'bg-raise font-semibold' : 'hover:bg-raise'}`}>
                <Dot type={t} /><span className="flex-1 text-left">{summary.typeLabels[t] || t}</span><span className="text-card-faint">{n}</span>
              </button>
            ))}
          </div>
          <div className="mx-2 my-2 h-px bg-card-line" />
          <div id="map-list" className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            {list.map((n) => (
              <button key={n.id} onClick={() => open(n.id)} title={n.id}
                      className={`flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-[12.5px] ${sel === n.id ? 'bg-raise font-semibold' : 'hover:bg-raise'}`}>
                <Dot type={n.type} /><span className="min-w-0 flex-1 truncate">{n.label}</span><span className="text-[10.5px] text-card-faint">{n.degree}</span>
              </button>
            ))}
            {!list.length && <div className="px-2 text-xs text-card-faint">없어요</div>}
          </div>
        </>) : (
          <div id="map-improve" className="min-h-0 flex-1 overflow-y-auto px-2 pb-3 text-[12.5px]">
            <div className="px-2 pt-1 pb-1 text-[11px] font-semibold text-card-faint">하네스</div>
            <button onClick={() => harness?.active && open(`harness:${harness.active}`)} className="w-full rounded-lg px-2 py-1 text-left hover:bg-raise">
              사용 중: <b>{harness?.active || '기본'}</b>
            </button>
            {harness?.versions.map((v) => (
              <button key={v.id} onClick={() => open(`harness:${v.id}`)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left hover:bg-raise">
                <Dot type="harness" /><span className="min-w-0 flex-1 truncate">{v.id} · {v.note}</span>
              </button>
            ))}
            <div className="px-2 pt-3 pb-1 text-[11px] font-semibold text-card-faint">신호 {harness?.signals.length || 0}</div>
            {harness?.signals.map((s) => (
              <button key={s.id} data-signal={s.type} onClick={() => open(s.id)} className={`flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left hover:bg-raise ${sel === s.id ? 'bg-raise' : ''}`}>
                <Dot type="signal" /><span className="min-w-0 flex-1 truncate"><b>{s.type}</b> {s.title}</span>{s.lesson && <Icon name="check" className="size-3 text-ok" />}
              </button>
            ))}
            <div className="px-2 pt-3 pb-1 text-[11px] font-semibold text-card-faint">교훈 {harness?.lessons.length || 0}</div>
            {harness?.lessons.map((l) => (
              <button key={l.id} onClick={() => open(l.id)} className={`flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left hover:bg-raise ${sel === l.id ? 'bg-raise' : ''}`}>
                <Dot type="lesson" /><span className="min-w-0 flex-1 truncate">{l.text}</span><span className="text-[10.5px] text-card-faint">{{ proposed: '제안', probed: '비교함', adopted: '채택', rejected: '거절' }[l.status]}</span>
              </button>
            ))}
            <div className="mt-3 rounded-lg bg-raise/60 p-2 text-[11.5px] text-card-sub">
              업무 기록 올리기: <b>{{ off: '꺼짐', signals: '신호만', hashes: '해시까지', full: '원문까지' }[sync.mode]}</b>
              {sync.last && <> · {ago(Date.parse(sync.last))} · {sync.sent}건</>}{sync.error && <div className="text-warn">{sync.error}</div>}
              <div className="mt-1 text-card-faint">마이페이지에서 바꿔요</div>
            </div>
          </div>
        )}
      </aside>

      {/* 가운데: 노트 */}
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-10 shrink-0 items-center gap-1 border-b border-card-line px-2">
          <button disabled={hist.i <= 0} onClick={() => setHist((h) => ({ ...h, i: h.i - 1 }))} className="grid size-7 place-items-center rounded-lg hover:bg-raise disabled:opacity-30"><Icon name="back" className="size-3.5" /></button>
          <button disabled={hist.i >= hist.stack.length - 1} onClick={() => setHist((h) => ({ ...h, i: h.i + 1 }))} className="grid size-7 place-items-center rounded-lg hover:bg-raise disabled:opacity-30"><Icon name="fwd" className="size-3.5" /></button>
          <span className="ml-1 text-[12px] text-card-faint">노트 {summary?.nodes ?? '…'} · 관계 {summary?.edges ?? '…'}</span>
          {summary?.ssot && (
            <span id="map-ssot-stats" className="ml-2 flex items-center gap-1.5 text-[12px] text-card-sub" title="업무 단위(작업·투자 건·결정·지시)마다 임시 Work ID — 업무 지도를 열면 자동으로 발행합니다">
              SSoT 임시 발행 {summary.ssot.issued}/{summary.ssot.eligible}
              {summary.ssot.issued < summary.ssot.eligible && <Btn onClick={() => run(() => call('ssotSyncAll'), (r) => setMsg(`SSoT ${r.issued}건 발행`))}>모두 발행</Btn>}
            </span>
          )}
          {msg && <span className="ml-2 truncate text-[12px] text-warn" title={msg}>{msg}</span>}
          {busy && <span className="ml-2 text-[12px] text-card-sub">Claude가 일하는 중…</span>}
          <span className="flex-1" />
          <Btn title="모든 관계를 주장 튜플 JSONL로 다운로드 폴더에" onClick={() => run(() => call('exportClaims'), (f) => setMsg(`내보냄: ${f}`))}><Icon name="download" className="size-3.5" />주장 튜플</Btn>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto select-text">
          <Note id={sel} onOpen={open} harness={harness} busy={busy} run={run} />
        </div>
      </main>

      {/* 오른쪽: 그래프 */}
      <aside className="flex w-[38%] max-w-[520px] min-w-[300px] shrink-0 flex-col border-l border-card-line">
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-card-line px-3 text-[12px]">
          <Icon name="graph" className="size-4 text-card-sub" />
          <span className="font-semibold">{whole || !sel ? '전체 그래프' : '주변 그래프'}</span>
          <span className="text-card-faint">{graph?.nodes.length || 0}</span>
          <span className="flex-1" />
          <button id="graph-toggle" onClick={() => setWhole((v) => !v)} className="rounded-lg px-2 py-1 text-card-sub hover:bg-raise">{whole ? '주변만' : '전체'}</button>
        </div>
        <div className="min-h-0 flex-1"><GraphView data={graph} selected={sel} onSelect={open} dark={dark} /></div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-card-line px-3 py-2 text-[11px] text-card-sub">
          {types.slice(0, 10).map(([t]) => <span key={t} className="inline-flex items-center gap-1"><Dot type={t} />{summary.typeLabels[t]}</span>)}
        </div>
      </aside>
    </div>
  );
}
