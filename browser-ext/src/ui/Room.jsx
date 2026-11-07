// 채팅방 — AI 대화와 팀 채널을 같은 모양으로 그린다.
// 에이전트의 일(작업 과정 · 결과 페이지 · 승인 요청)도 채팅 메시지로 흐른다.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import Icon from './Icon.jsx';
import Markdown from './Markdown.jsx';
import { Mark, Suggestion } from './NewTab.jsx';
import { Avatar, Chip, MentionChips, useMentionPicker } from './Mentions.jsx';
import {
  a2a, useStore, update, send, route, destChannel, answerConfirm, addMention, react, loadOlder,
  newAiChat, roomKey, takeDraft,
} from './store.js';
import { STEP, STATUS_DOT, SUGS_NEW, SUGS_PAGE, brief, statusText, timeOf, dayOf } from './format.js';
import { useNow } from './useNow.js';
import MyPage from './MyPage.jsx';
import Downloads from './Downloads.jsx';
import History from './History.jsx';

// 채팅이 아닌 패널 화면 — 같은 자리를 쓴다(웹페이지에 가려지지 않는 곳)
const VIEWS = {
  me: { icon: 'person', title: '마이페이지', sub: '프로필 · 알림 · 계정', Comp: MyPage },
  // 다운로드·방문 기록은 Chromium의 실제 기록을 보여 준다(chrome.downloads·chrome.history)
  downloads: { icon: 'download', title: '다운로드', sub: '받은 파일', Comp: Downloads },
  history: { icon: 'clock', title: '방문 기록', sub: '최근에 연 페이지', Comp: History },
};

// ── 조각들 ───────────────────────────────────────────────────────────
function DaySep({ label }) {
  return (
    <div className="my-3 flex items-center gap-3 text-[11px] font-medium text-card-faint">
      <span className="h-px flex-1 bg-card-line" />{label}<span className="h-px flex-1 bg-card-line" />
    </div>
  );
}

function Row({ avatar, name, sub, ts, compact, children, actions }) {
  return (
    <div className={`group relative flex gap-2.5 rounded-lg px-2 py-1 hover:bg-raise/60 ${compact ? '' : 'mt-2'}`}>
      <div className="w-8 shrink-0">{!compact && avatar}</div>
      <div className="min-w-0 flex-1">
        {!compact && (
          <div className="mb-0.5 flex items-baseline gap-1.5">
            <span className="text-[13px] font-semibold">{name}</span>
            {sub && <span className="text-[11px] text-card-faint">{sub}</span>}
            {ts && <span className="text-[11px] text-card-faint">{timeOf(ts)}</span>}
          </div>
        )}
        {children}
      </div>
      {actions && (
        <div className="absolute -top-3 right-2 hidden items-center gap-0.5 rounded-lg bg-card p-0.5 shadow-md ring-1 ring-card-line group-hover:flex">
          {actions}
        </div>
      )}
    </div>
  );
}

function WorkSteps({ steps }) {
  const running = steps.some((s) => s.state === 'pending');
  const [open, setOpen] = useState(false);
  const lastStep = steps[steps.length - 1];
  const show = open ? steps : running ? [lastStep] : [];
  return (
    <div className="my-1">
      <button onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-1.5 rounded-md py-0.5 pr-1.5 text-xs text-card-sub hover:text-card-ink">
        {running
          ? <span className="size-3 animate-spin rounded-full border-[1.6px] border-accent border-r-transparent" />
          : <Icon name="check" className="size-3.5 text-ok" />}
        {running ? '작업하는 중' : '작업 과정'} · {steps.length}단계
        <Icon name="fwd" className={`size-3 transition ${open ? 'rotate-90' : ''}`} />
      </button>
      {show.length > 0 && (
        <div className="mt-1 ml-1.5 border-l-[1.5px] border-card-line py-0.5">
          {show.map((s, i) => {
            const [ic, label] = STEP[s.name] || ['spark', s.name];
            return (
              <div key={i} title={s.summary || ''} className="-ml-[9px] mb-[3px] flex min-h-[22px] items-center gap-2 text-xs text-card-sub">
                <span className={`grid size-4 shrink-0 place-items-center rounded-full bg-card
                                  ${s.state === 'ok' ? 'text-ok' : s.state === 'bad' ? 'text-warn' : 'text-card-faint'}`}>
                  {s.state === 'pending'
                    ? <span className="size-[11px] animate-spin rounded-full border-[1.6px] border-accent border-r-transparent" />
                    : <Icon name={s.state === 'bad' ? 'alert' : ic} className="size-[13px]" />}
                </span>
                <span className="truncate">{label}<em className="ml-1 text-card-faint not-italic">{brief(s.name, s.input)}</em></span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// 결과 페이지를 대화 안의 카드로 — 열기 / 팀에 공유
export function ResultCard({ spec, tabId, taskId }) {
  const stats = spec.blocks.find((b) => b.type === 'stats')?.items.slice(0, 4) || [];
  const table = spec.blocks.find((b) => b.type === 'table');
  const cols = table?.columns.slice(0, 4) || [];
  return (
    <div className="my-1.5 overflow-hidden rounded-xl bg-field ring-1 ring-card-line">
      <div className="flex items-start gap-3 p-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent"><Icon name="page" /></div>
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-semibold">{spec.title}</div>
          {spec.subtitle && <div className="mt-0.5 line-clamp-2 text-xs text-card-sub">{spec.subtitle}</div>}
        </div>
      </div>
      {stats.length > 0 && (
        <div className="grid grid-cols-2 gap-px border-t border-card-line bg-card-line">
          {stats.map((s, i) => (
            <div key={i} className="bg-field px-3 py-2">
              <div className="truncate text-[11px] text-card-faint">{s.label}</div>
              <div className="truncate text-[15px] font-semibold tabular-nums">{s.value}</div>
            </div>
          ))}
        </div>
      )}
      {table && (
        <div className="border-t border-card-line px-3 py-2">
          <table className="w-full table-fixed text-[11.5px]">
            <thead><tr>{cols.map((c, i) => <th key={i} className="truncate pb-1 text-left font-medium text-card-faint">{c}</th>)}</tr></thead>
            <tbody>
              {table.rows.slice(0, 3).map((r, i) => (
                <tr key={i} className="border-t border-card-line/70">{cols.map((_, j) => <td key={j} className="truncate py-1 pr-2">{r[j]}</td>)}</tr>
              ))}
            </tbody>
          </table>
          {table.rows.length > 3 && <div className="pt-1 text-[11px] text-card-faint">외 {table.rows.length - 3}행</div>}
        </div>
      )}
      <div className="flex gap-1.5 border-t border-card-line p-2">
        <button onClick={() => a2a.send('page:open', tabId, spec)}
                className="flex h-7 flex-1 items-center justify-center gap-1.5 rounded-lg bg-card-ink text-xs font-semibold text-card">
          <Icon name="external" className="size-3.5" />결과 페이지 열기
        </button>
        {taskId && (
          <button onClick={() => addMention({ kind: 'task', id: taskId, label: spec.title })} title="이 결과를 붙여 두고, 사람을 태그해 보내세요"
                  className="flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-xs font-semibold ring-1 ring-card-line hover:bg-raise">
            <Icon name="share" className="size-3.5" />팀에 공유
          </button>
        )}
      </div>
    </div>
  );
}

function Confirm({ item, taskId }) {
  return (
    <div className={`my-1.5 rounded-xl bg-accent-soft p-3 ring-1 ring-accent/45 ring-inset ${item.answer !== null ? 'opacity-65' : ''}`}>
      <div className="mb-1.5 flex items-center gap-1.5 text-[13px] font-bold"><Icon name="shield" className="size-4 text-accent" />이 동작을 실행할까요?</div>
      <pre className="mb-2.5 max-h-[220px] overflow-auto rounded-lg bg-field px-2.5 py-2 font-mono text-xs leading-[1.55] whitespace-pre-wrap select-text">{item.text}</pre>
      <div className="flex items-center justify-end gap-2">
        {item.answer === null ? (
          <>
            <button onClick={() => answerConfirm(taskId, item.id, false)}
                    className="h-[30px] rounded-[9px] bg-field px-3.5 text-[13px] font-semibold ring-1 ring-card-line ring-inset">거절</button>
            <button onClick={() => answerConfirm(taskId, item.id, true)}
                    className="h-[30px] rounded-[9px] bg-accent px-3.5 text-[13px] font-semibold text-white">실행</button>
          </>
        ) : <span className="text-xs text-card-sub">{item.answer ? '승인함' : '거절함'}</span>}
      </div>
    </div>
  );
}

// 에이전트 한 차례(작업 과정 → 답 → 결과 카드 → 승인)
function AgentParts({ parts, taskId }) {
  return parts.map((it, i) => {
    switch (it.k) {
      case 'steps': return <WorkSteps key={i} steps={it.steps} />;
      case 'bot': return <div key={i} className="my-0.5"><Markdown src={it.src} /></div>;
      case 'page': return <ResultCard key={i} spec={it.spec} tabId={it.tabId} taskId={taskId} />;
      case 'confirm': return <Confirm key={i} item={it} taskId={taskId} />;
      case 'note': return <div key={i} className="my-1 rounded-lg bg-info/10 px-2.5 py-1.5 text-xs text-info">{it.text}</div>;
      case 'err': return <div key={i} className="my-1 rounded-lg bg-warn/10 px-2.5 py-1.5 text-xs text-warn">{it.text}</div>;
      default: return null;
    }
  });
}

// 지난 실행(앱을 다시 켠 뒤)은 대화 기록이 없다 — 남은 요약·단계·결과로 보여 준다
function replay(t) {
  const items = [{ k: 'user', text: t.title, ts: t.started }];
  if (t.steps.length) items.push({ k: 'steps', steps: t.steps.map((s) => ({ ...s, state: 'ok' })), ts: t.started });
  for (const p of t.pages || []) items.push({ k: 'page', spec: p, tabId: t.tabId, ts: t.ended || t.started });
  if (t.summary) items.push({ k: 'bot', src: t.summary, ts: t.ended || t.started });
  items.push({ k: 'note', text: '지난 실행의 기록입니다. 이어서 말하면 새 대화로 시작합니다.', ts: t.ended || t.started });
  return items;
}

function toTurns(items) {
  const turns = [];
  for (const it of items) {
    if (it.k === 'user') turns.push({ who: 'me', it, ts: it.ts });
    else {
      let t = turns[turns.length - 1];
      if (!t || t.who !== 'agent') { t = { who: 'agent', parts: [], ts: it.ts }; turns.push(t); }
      t.parts.push(it);
    }
  }
  return turns;
}

// ── AI 방 ────────────────────────────────────────────────────────────
function AiRoom({ room }) {
  const S = useStore((s) => s.S);
  const logs = useStore((s) => s.logs);
  const drafts = useStore((s) => s.drafts[roomKey(room)]);
  const me = useStore((s) => s.me);
  const task = S.tasks.find((t) => t.id === room.id);
  const items = room.id ? (logs[room.id]?.items ?? (task ? replay(task) : [])) : [];
  const timeline = useMemo(() => {
    const t = toTurns(items);
    for (const d of drafts || []) t.push({ who: 'team', d, ts: d.ts });
    return t.sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
  }, [items, drafts]);

  if (!timeline.length) {
    const onNewTab = !!S.tabs.find((t) => t.active)?.newtab;
    return (
      <div className="px-[18px] pt-[12vh] text-center text-card-sub">
        <div className="mx-auto mb-3.5 w-fit"><Mark small /></div>
        <b className="mb-1 block text-sm text-card-ink">{onNewTab ? '무엇이든 맡겨 보세요' : '지금 보고 있는 페이지에서 일을 맡기세요'}</b>
        <div className="text-[12.5px]">읽고, 누르고, 정리해서 결과 페이지로 보여 줍니다.<br />사람을 @태그하면 AI 대신 팀에게 보내요.</div>
        <div className="mt-[18px] flex flex-col items-stretch gap-2">
          {(onNewTab ? SUGS_NEW : SUGS_PAGE).map((s) => <Suggestion key={s} block text={s} onPick={(v) => send(v, { fresh: true })} />)}
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col pb-2">
      {timeline.map((t, i) => {
        if (t.who === 'me') {
          return (
            <Row key={i} avatar={<Avatar id={me?.user_id || S.user} name={me?.display_name || S.user} />} name={me?.display_name || S.user} ts={t.ts}>
              <div className="text-[13.5px] whitespace-pre-wrap">{t.it.text}</div>
              {t.it.mentions?.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{t.it.mentions.map((m, j) => <Chip key={j} m={m} tone="bubble" />)}</div>}
            </Row>
          );
        }
        if (t.who === 'team') {
          return (
            <div key={i} className="mx-2 my-2 rounded-xl bg-info/8 px-3 py-2 text-[12.5px] ring-1 ring-info/20">
              <div className="flex items-center gap-1.5 font-semibold text-info"><Icon name="people" className="size-3.5" />#{t.d.channel}에 @{t.d.who}에게 보냈어요</div>
              <div className="mt-0.5 line-clamp-2 text-card-sub">{t.d.text}</div>
              <div className="mt-1 text-[11px] text-card-faint">사람을 태그해서 AI는 답하지 않았어요.</div>
            </div>
          );
        }
        return (
          <Row key={i} avatar={<Avatar agent />} name="A2A 에이전트" sub={i === timeline.length - 1 && S.busy && room.id === S.currentTaskId ? '작업 중' : ''} ts={t.ts}>
            <AgentParts parts={t.parts} taskId={room.id} />
          </Row>
        );
      })}
    </div>
  );
}

// ── 채널 방 ──────────────────────────────────────────────────────────
const QUICK = ['👍', '✅', '👀'];
const ts = (m) => Date.parse(m.created_at);

function ChannelRoom({ room }) {
  const S = useStore((s) => s.S);
  const msgs = useStore((s) => s.msgs[room.id]);
  const members = useStore((s) => s.members);
  const me = useStore((s) => s.me);
  const logs = useStore((s) => s.logs);
  const live = S.tasks.filter((t) => t.room?.cid === room.id && t.status === 'working');

  if (!msgs || (msgs.loading && !msgs.list.length)) return <div className="p-6 text-center text-xs text-card-faint">불러오는 중…</div>;
  if (msgs.error) return <div className="p-6 text-center text-xs text-warn">채널을 불러오지 못했습니다 — 엔진(8423)이 떠 있는지 확인하세요.</div>;

  const out = [];
  let prev = null;
  for (const m of msgs.list) {
    const day = dayOf(ts(m));
    if (!prev || dayOf(ts(prev)) !== day) { out.push(<DaySep key={`d${m.seq}`} label={day} />); prev = null; }
    if (m.kind === 'activity' || m.kind === 'system' || m.author === 'platform') {
      out.push(
        <div key={m.seq} className="my-1 flex items-center gap-2 px-3 text-xs text-card-faint">
          <Icon name="spark" className="size-3" /><span className="truncate">{m.body}</span><span className="ml-auto shrink-0">{timeOf(ts(m))}</span>
        </div>,
      );
      prev = null;
      continue;
    }
    const isAgent = m.body.startsWith('🤖');
    const body = isAgent ? m.body.replace(/^🤖\s*/, '') : m.body;
    const who = members[m.author]?.display_name || m.author;
    const compact = prev && prev.author === m.author && prev.body.startsWith('🤖') === isAgent && ts(m) - ts(prev) < 5 * 60e3;
    out.push(
      <Row key={m.seq} compact={compact} ts={ts(m)}
           avatar={isAgent ? <Avatar agent /> : <Avatar id={m.author} name={who} />}
           name={isAgent ? 'A2A 에이전트' : who} sub={isAgent ? `${who}(@${m.author})의 에이전트` : `@${m.author}`}
           actions={!m.deleted && (
             <>
               {QUICK.map((e) => (
                 <button key={e} onClick={() => react(room.id, m.seq, e, !m.reactions?.some((r) => r.emoji === e && r.mine))}
                         className="grid size-7 place-items-center rounded-md text-sm hover:bg-raise">{e}</button>
               ))}
               <button title="이 메시지를 AI에게 맡기기" onClick={() => {
                 newAiChat();
                 addMention({ kind: 'msg', id: `${room.id}:${m.seq}`, label: body.replace(/\s+/g, ' ').slice(0, 28) });
               }} className="flex h-7 items-center gap-1 rounded-md px-1.5 text-[11px] font-semibold text-accent hover:bg-raise">
                 <Icon name="spark" className="size-3.5" />AI에게
               </button>
             </>
           )}>
        {m.deleted
          ? <div className="text-xs text-card-faint italic">삭제된 메시지</div>
          : <div className="text-[13.5px]"><Markdown src={body} /></div>}
        {m.edited_at && !m.deleted && <span className="text-[10.5px] text-card-faint">(수정됨)</span>}
        {(m.reactions?.length > 0 || m.reply_count > 0) && (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {m.reactions?.map((r) => (
              <button key={r.emoji} onClick={() => react(room.id, m.seq, r.emoji, !r.mine)}
                      className={`flex h-6 items-center gap-1 rounded-full px-2 text-xs ring-1 ${r.mine ? 'bg-info/10 ring-info/40' : 'bg-field ring-card-line'}`}>
                {r.emoji}<span className="tabular-nums">{r.count}</span>
              </button>
            ))}
            {m.reply_count > 0 && <span className="ml-1 flex items-center gap-1 text-xs font-medium text-info"><Icon name="reply" className="size-3.5" />답글 {m.reply_count}개</span>}
          </div>
        )}
      </Row>,
    );
    prev = m;
  }

  return (
    <div className="flex flex-col pb-2">
      {msgs.hasMore && (
        <button onClick={() => loadOlder(room.id)} className="mx-auto my-2 rounded-full px-3 py-1 text-xs text-card-sub ring-1 ring-card-line hover:bg-raise">
          이전 메시지 더 보기
        </button>
      )}
      {!msgs.list.length && <div className="p-6 text-center text-xs text-card-faint">아직 메시지가 없어요. 첫 메시지를 남겨 보세요.</div>}
      {out}
      {live.map((t) => (
        <Row key={t.id} avatar={<Avatar agent />} name="A2A 에이전트" sub={`${me?.display_name || S.user}의 에이전트 · 작업 중`} ts={t.started}>
          <AgentParts parts={(logs[t.id]?.items || []).filter((x) => x.k !== 'user')} taskId={t.id} />
        </Row>
      ))}
    </div>
  );
}

// ── 입력창 ───────────────────────────────────────────────────────────
function RouteHint({ room, mentions, busy }) {
  const channels = useStore((s) => s.channels);
  const r = route(room, mentions);
  const people = mentions.filter((m) => m.kind === 'person').map((m) => `@${m.label}`).join(' ');
  if (room.kind === 'channel') {
    if (r.to === 'people') {
      return <span>{people}에게 보내요{r.why === 'person-wins' && <b className="text-warn"> · 사람을 태그해서 AI는 답하지 않아요</b>}</span>;
    }
    if (r.to === 'agent-in-channel') {
      return busy ? <span className="text-warn">에이전트가 다른 일을 하는 중이에요</span>
        : <span className="text-accent">✦ 내 에이전트가 이 채널에서 일하고 답을 올려요</span>;
    }
    return <span>채널에 보내요 · AI는 <b>@에이전트</b>를 태그할 때만 답해요</span>;
  }
  if (r.to === 'people') {
    const dest = destChannel(mentions);
    return (
      <span className="flex items-center gap-1">
        <b className="text-info">{people}</b>에게
        <select value={dest?.id ?? ''} onChange={(e) => {
          const c = channels?.find((x) => x.id === Number(e.target.value));
          if (!c) return;
          update((s) => ({ mentions: [...s.mentions.filter((m) => m.kind !== 'channel'), { kind: 'channel', id: c.name, label: c.name }] }));
        }} className="rounded bg-raise px-1 py-0.5 text-[11px] font-semibold text-card-ink outline-none">
          {(channels || []).map((c) => <option key={c.id} value={c.id}>#{c.name}</option>)}
        </select>
        로 보내요 · <b className="text-warn">AI는 답하지 않아요</b>
      </span>
    );
  }
  return busy ? <span className="text-warn">에이전트가 일하는 중이에요 — 끝나면 이어서 말할 수 있어요</span>
    : <span>AI에게 · @로 탭·결과·사람 태그 · Enter 보내기</span>;
}

function Composer({ room }) {
  const S = useStore((s) => s.S);
  const mentions = useStore((s) => s.mentions);
  const channels = useStore((s) => s.channels);
  const [text, setText] = useState('');
  const ta = useRef(null);
  const mention = useMentionPicker(text, setText, room.kind === 'channel' ? 'channel' : 'ai');
  const draft = useStore((s) => s.draft);
  useEffect(() => {
    if (draft === null) return;
    const d = takeDraft();
    setText(d || '');
    setTimeout(() => { ta.current?.focus(); ta.current?.setSelectionRange(ta.current.value.length, ta.current.value.length); }, 30);
  }, [draft]);
  useEffect(() => {
    ta.current.style.height = 'auto';
    ta.current.style.height = Math.min(ta.current.scrollHeight, 160) + 'px';
  }, [text]);
  const r = route(room, mentions);
  const blocked = (r.to === 'agent' || r.to === 'agent-in-channel') && S.busy;
  const go = async () => {
    if (!text.trim() || blocked) return;
    const t = text;
    setText('');
    if (!(await send(t))) setText(t);
  };
  const chName = room.kind === 'channel' ? channels?.find((c) => c.id === room.id)?.name : '';
  return (
    <form onSubmit={(e) => { e.preventDefault(); go(); }}
          className="relative mx-3 mb-3 shrink-0 rounded-[14px] bg-field pt-2.5 pr-2.5 pb-2 pl-3 ring-1 ring-field-line focus-within:ring-[1.5px] focus-within:ring-accent">
      {mention.picker && <div className="absolute right-0 bottom-full left-0 mb-2">{mention.picker}</div>}
      <MentionChips />
      <textarea id="composer" ref={ta} rows={2} value={text} onChange={(e) => setText(e.target.value)}
                placeholder={room.kind === 'channel' ? `#${chName || ''}에 메시지 · @에이전트로 AI 부르기` : '무엇이든 맡기세요 · @로 탭·결과·사람 태그'}
                onKeyDown={(e) => { if (mention.onKeyDown(e)) return; if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); go(); } }}
                className="w-full resize-none bg-transparent text-[13.5px] text-card-ink outline-none select-text placeholder:text-card-faint" />
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[11px] text-card-faint"><RouteHint room={room} mentions={mentions} busy={S.busy} /></span>
        {S.busy && (
          <button type="button" onClick={() => a2a.send('agent:stop')}
                  className="flex h-7 shrink-0 items-center gap-1 rounded-lg bg-raise px-2.5 text-xs font-semibold">
            <Icon name="stop" className="size-[13px]" />중지
          </button>
        )}
        <button type="submit" disabled={blocked || !text.trim()}
                className="grid size-7 shrink-0 place-items-center rounded-lg bg-accent text-white disabled:bg-raise disabled:text-card-faint">
          <Icon name="arrowUp" className="size-[15px]" strokeWidth={2} />
        </button>
      </div>
    </form>
  );
}

// ── 패널 ─────────────────────────────────────────────────────────────
// 사이드 패널 전체 높이를 쓰는 채팅방. headerLeft(워크스페이스 단추)·onTitle(방 바꾸기 목록 열기)은 SidePanel이 준다.
export default function RoomPanel({ headerLeft = null, onTitle = null, switcherOpen = false }) {
  const S = useStore((s) => s.S);
  const room = useStore((s) => s.room);
  const channels = useStore((s) => s.channels);
  const chatStatus = useStore((s) => s.chatStatus);
  const now = useNow(S.busy);
  const scroller = useRef(null);
  const logs = useStore((s) => (room.kind === 'ai' ? s.logs[room.id] : null));
  const stick = useRef(true);

  const task = room.kind === 'ai' ? S.tasks.find((t) => t.id === room.id) : null;
  const ch = room.kind === 'channel' ? channels?.find((c) => c.id === room.id) : null;

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });
  useEffect(() => { stick.current = true; }, [room.kind, room.id]);

  return (
    <div className="flex h-screen min-w-0 flex-col overflow-hidden bg-card text-card-ink">
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex h-[52px] shrink-0 items-center gap-2 border-b border-card-line pr-2 pl-2">
          {headerLeft}
          {VIEWS[room.kind] ? <span className="grid size-6 place-items-center rounded-md bg-raise text-card-sub"><Icon name={VIEWS[room.kind].icon} className="size-3.5" /></span>
           : room.kind === 'ai' ? <Avatar agent size="sm" /> : (
            <span className="grid size-6 place-items-center rounded-md bg-raise text-card-sub"><Icon name={ch?.visibility === 'private' ? 'lock' : 'hash'} className="size-3.5" /></span>
          )}
          <button id="room-title" onClick={onTitle || undefined} title={onTitle ? '방 바꾸기' : undefined}
                  className={`flex min-w-0 flex-1 items-center gap-1 rounded-lg px-1.5 py-1 text-left leading-tight ${!onTitle ? 'cursor-default' : switcherOpen ? 'bg-raise' : 'hover:bg-raise'}`}>
           <span className="min-w-0 flex-1">
            <div className="truncate text-[13.5px] font-semibold">
              {VIEWS[room.kind] ? VIEWS[room.kind].title : room.kind === 'ai' ? (task?.title || logs?.title || '새 AI 채팅') : ch?.name || '채널'}
            </div>
            <div className="flex items-center gap-1.5 text-[11px] text-card-faint">
              {VIEWS[room.kind] ? VIEWS[room.kind].sub
                : room.kind === 'ai'
                ? (task ? <><span className={`size-1.5 rounded-full ${STATUS_DOT[task.status]}`} />{statusText(task, now)}</> : 'A2A 에이전트 · Opus 5.5')
                : <><span className={`size-1.5 rounded-full ${chatStatus === 'live' ? 'bg-ok' : 'bg-faint'}`} />{ch?.topic || (chatStatus === 'live' ? '실시간' : '연결 중…')}</>}
            </div>
           </span>
           {onTitle && <Icon name="chevronDown" className={`size-3.5 shrink-0 text-card-faint transition ${switcherOpen ? 'rotate-180' : ''}`} />}
          </button>
          {VIEWS[room.kind] ? null : room.kind === 'ai' ? (
            <button title="새 AI 채팅 (⌘J)" onClick={newAiChat} disabled={S.busy}
                    className="grid size-7 place-items-center rounded-lg text-card-sub hover:bg-raise hover:text-card-ink disabled:opacity-35">
              <Icon name="compose" />
            </button>
          ) : (
            <button title="A2A 웹앱에서 열기" onClick={() => a2a.send('web:open')}
                    className="grid size-7 place-items-center rounded-lg text-card-sub hover:bg-raise hover:text-card-ink">
              <Icon name="external" />
            </button>
          )}
        </div>
        <div ref={scroller} onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}
             className="min-h-0 flex-1 overflow-y-auto px-1.5 pt-1 select-text">
          {VIEWS[room.kind] ? (() => { const C = VIEWS[room.kind].Comp; return <C />; })() : room.kind === 'ai' ? <AiRoom room={room} /> : <ChannelRoom room={room} />}
        </div>
        {/* 방마다 입력창을 따로 — 쓰던 글이 다른 방으로 따라가지 않게 */}
        {!VIEWS[room.kind] && <Composer key={roomKey(room)} room={room} />}
      </div>
    </div>
  );
}
