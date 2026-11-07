// @ 태그 — 입력창에서 @를 치면 에이전트·사람·탭·지난 채팅(결과)·채널을 골라 붙인다.
// 사람/에이전트 태그는 "누구에게 보내나"를 정하고(store.route), 나머지는 맥락으로 붙는다.
import { useMemo, useState } from 'react';
import Icon from './Icon.jsx';
import { useStore, addMention, removeMention } from './store.js';

export const KIND_ICON = { tab: 'tabs', task: 'spark', channel: 'hash', person: 'person', agent: 'spark', msg: 'chat' };
const KIND_LABEL = { agent: 'AI', person: '사람', tab: '탭', task: '채팅 결과', channel: '채널', msg: '메시지' };

export function Chip({ m, onRemove, tone = 'field' }) {
  const who = m.kind === 'person' || m.kind === 'agent';
  return (
    <span className={`inline-flex h-6 max-w-[220px] items-center gap-1 rounded-md pr-1 pl-1.5 text-xs font-medium
                      ${who ? 'bg-info/12 text-info' : tone === 'field' ? 'bg-accent-soft text-card-ink' : 'bg-field/70 text-card-ink'}`}>
      {m.kind === 'person'
        ? <span className="font-bold">@</span>
        : <Icon name={KIND_ICON[m.kind]} className={`size-3 ${m.kind === 'agent' ? '' : 'text-accent'}`} />}
      <span className="truncate">{m.kind === 'channel' ? `#${m.label}` : m.label}</span>
      {onRemove && (
        <button type="button" onClick={onRemove} className="grid size-4 place-items-center rounded text-card-sub hover:bg-raise">
          <Icon name="x" className="size-3" />
        </button>
      )}
    </span>
  );
}

export function MentionChips() {
  const mentions = useStore((s) => s.mentions);
  if (!mentions.length) return null;
  return (
    <div className="mb-1.5 flex flex-wrap gap-1">
      {mentions.map((m) => <Chip key={`${m.kind}:${m.id}`} m={m} onRemove={() => removeMention(m)} />)}
    </div>
  );
}

// 입력창에 붙여 쓰는 훅: @질의 감지 → 후보 → 키보드 고르기
//   scope: 'channel'(에이전트·사람 먼저) | 'ai'(맥락 먼저, 사람도 가능) | 'newtab'(AI 전용 — 사람 없음)
export function useMentionPicker(text, setText, scope = 'ai') {
  const S = useStore((s) => s.S);
  const channels = useStore((s) => s.channels);
  const members = useStore((s) => s.members);
  const me = useStore((s) => s.me);
  const [sel, setSel] = useState(0);
  const [dismissed, setDismissed] = useState(null);
  const m = text.match(/(^|\s)@([^\s@]*)$/);
  const query = m ? m[2].toLowerCase() : null;

  const options = useMemo(() => {
    if (query === null || !S) return [];
    const people = Object.entries(members)
      .filter(([id]) => id !== me?.user_id)
      .map(([id, v]) => ({ kind: 'person', id, label: v.display_name, sub: `@${id}` }));
    const agent = [{ kind: 'agent', id: 'agent', label: '에이전트', sub: '내 브라우저 에이전트가 일하고 답해요' }];
    const ctx = [
      ...S.tabs.filter((t) => !t.newtab).map((t) => ({ kind: 'tab', id: t.id, label: t.title, sub: t.url })),
      ...S.tasks.map((t) => ({ kind: 'task', id: t.id, label: t.title, sub: t.status === 'done' ? '완료된 결과' : t.status })),
      ...(channels || []).map((c) => ({ kind: 'channel', id: c.name, label: c.name, sub: c.visibility === 'private' ? '비공개 채널' : '채널' })),
    ];
    const all = scope === 'channel' ? [...agent, ...people, ...ctx] : scope === 'ai' ? [...ctx, ...people] : ctx;
    const q = query;
    return all.filter((o) => !q || o.label.toLowerCase().includes(q) || String(o.sub || '').toLowerCase().includes(q)).slice(0, 10);
  }, [query, S, channels, members, me, scope]);

  const open = query !== null && dismissed !== text && options.length > 0;
  const pick = (o) => {
    addMention({ kind: o.kind, id: o.id, label: o.label });
    setText(text.replace(/(^|\s)@[^\s@]*$/, '$1'));
    setSel(0);
  };
  const onKeyDown = (e) => {
    if (!open) return false;
    if (e.key === 'ArrowDown') { setSel((i) => (i + 1) % options.length); e.preventDefault(); return true; }
    if (e.key === 'ArrowUp') { setSel((i) => (i - 1 + options.length) % options.length); e.preventDefault(); return true; }
    if ((e.key === 'Enter' || e.key === 'Tab') && !e.nativeEvent.isComposing) { pick(options[Math.min(sel, options.length - 1)]); e.preventDefault(); return true; }
    if (e.key === 'Escape') { setDismissed(text); e.preventDefault(); return true; }
    return false;
  };

  const picker = open && <MentionList options={options} sel={Math.min(sel, options.length - 1)} onPick={pick} onHover={setSel} />;
  return { picker, onKeyDown };
}

function MentionList({ options, sel, onPick, onHover }) {
  let lastKind = null;
  return (
    <div className="z-20 max-h-[320px] w-full overflow-y-auto rounded-xl bg-card py-1 text-card-ink shadow-[0_12px_36px_rgb(0_0_0/.18)] ring-1 ring-card-line">
      {options.map((o, i) => {
        const head = o.kind !== lastKind ? (lastKind = o.kind, KIND_LABEL[o.kind]) : null;
        return (
          <div key={`${o.kind}:${o.id}`}>
            {head && <div className="px-3 pt-1.5 pb-0.5 text-[10.5px] font-semibold text-card-faint">{head}</div>}
            <button type="button" onMouseDown={(e) => { e.preventDefault(); onPick(o); }} onMouseEnter={() => onHover(i)}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-left ${i === sel ? 'bg-raise' : ''}`}>
              {o.kind === 'person'
                ? <Avatar id={o.id} name={o.label} size="xs" />
                : <Icon name={KIND_ICON[o.kind]} className={`size-3.5 ${o.kind === 'agent' ? 'text-accent' : 'text-card-sub'}`} />}
              <span className="min-w-0 flex-1 truncate text-[13px]">{o.kind === 'channel' ? `#${o.label}` : o.label}</span>
              <span className="max-w-[45%] truncate text-[11px] text-card-faint">{o.sub}</span>
            </button>
          </div>
        );
      })}
    </div>
  );
}

const hueOf = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.codePointAt(0)) % 360; return h; };
export function Avatar({ id, name, size = 'md', agent }) {
  // 팀원이 고른 색(서버 프로필) — 없으면 아이디로 정한 기본 색
  const serverHue = useStore((s) => (s.me?.user_id === id ? s.me?.avatar_hue : s.members[id]?.avatar_hue) ?? null);
  const cls = size === 'xs' ? 'size-4 text-[9px] rounded-[5px]' : size === 'sm' ? 'size-6 text-[11px] rounded-md'
    : size === 'xl' ? 'size-16 text-[26px] rounded-[18px]' : size === 'ws' ? 'size-7 text-xs rounded-full' : 'size-8 text-[13px] rounded-[9px]';
  if (agent) {
    return (
      <span className={`${cls} grid shrink-0 place-items-center bg-gradient-to-br from-[#ff8a5c] via-accent to-[#e2557b] text-white`}>
        <Icon name="spark" className={size === 'xs' ? 'size-2.5' : 'size-4'} strokeWidth={2} />
      </span>
    );
  }
  return (
    <span className={`${cls} grid shrink-0 place-items-center font-bold text-white`}
          style={{ background: `hsl(${serverHue ?? hueOf(id)} 48% 52%)` }}>
      {(name || id || '?').trim().charAt(0).toUpperCase()}
    </span>
  );
}
