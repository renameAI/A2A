// 마이페이지 — 표시 이름(서버, 팀원에게 보임) · 아바타 색 · 알림(이 컴퓨터에만) · 계정 정보
import { useEffect, useState } from 'react';
import Icon from './Icon.jsx';
import { Avatar } from './Mentions.jsx';
import { a2a, useStore, saveDisplayName, saveAvatarHue, setPref } from './store.js';
import { ago } from './format.js';

const HUES = [12, 32, 145, 175, 205, 230, 265, 320];
const ROLE = { owner: '소유자', admin: '관리자', member: '멤버', guest: '게스트' };

function Section({ title, note, children }) {
  return (
    <section className="border-b border-card-line px-5 py-4 last:border-0">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-semibold">{title}</h3>
        {note && <span className="text-[11px] text-card-faint">{note}</span>}
      </div>
      {children}
    </section>
  );
}

function Row({ label, children }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5 text-[13px]">
      <span className="text-card-sub">{label}</span>
      <span className="min-w-0 truncate text-right font-medium">{children}</span>
    </div>
  );
}

// 연결된 도구 — EMA Station(MCP). 로그인은 새 탭에서 직접, 앱은 토큰만(키체인으로 암호화) 가진다.
const EMA_TEXT = {
  off: '연결 안 됨', idle: '연결 확인 중…', connecting: '연결하는 중…',
  login: '새 탭에서 EMA에 로그인해 주세요', on: '연결됨', error: '연결이 끊겼어요',
};
function EmaConnect({ ema }) {
  const st = ema?.status || 'off';
  const busy = st === 'connecting' || st === 'login' || st === 'idle';
  return (
    <div id="ema-connect" className="flex items-center gap-3 text-[13px]">
      <span className="grid size-9 shrink-0 place-items-center rounded-[10px] bg-raise text-[11px] font-bold">EMA</span>
      <span className="min-w-0 flex-1">
        <span className="block font-medium">EMA Station</span>
        <span className={`block truncate text-[11.5px] ${st === 'on' ? 'text-ok' : st === 'error' ? 'text-warn' : 'text-card-faint'}`}>
          {EMA_TEXT[st]}{st === 'on' ? ` · 도구 ${ema.tools}개 · AI가 등록 기업·오피스아워를 조회해요` : ''}{st === 'error' && ema.error ? ` — ${ema.error}` : ''}
        </span>
      </span>
      {st === 'on' ? (
        <button onClick={() => a2a.send('ema:disconnect')} className="h-8 rounded-lg px-3 text-xs font-semibold ring-1 ring-card-line hover:bg-raise">연결 끊기</button>
      ) : (
        <button disabled={busy} onClick={() => a2a.send('ema:connect')}
                className="h-8 rounded-lg bg-card-ink px-3 text-xs font-semibold text-card disabled:opacity-40">{st === 'error' ? '다시 연결' : '연결'}</button>
      )}
    </div>
  );
}

// 업무 기록 올리기(L1) — 어디까지 올릴지 고른다. 업무 지도·하네스 개선이 팀 단위로 돌려면 필요(docs/HARNESS.md)
const SYNC = [
  ['off', '끔', '이 컴퓨터에만 남겨요'],
  ['signals', '신호만', '어떤 도구를 썼고 무엇을 승인/거절했는지 — 원문·주소·입력 없음'],
  ['hashes', '해시까지', '+ 원문 해시·문서 종류·사이트 이름(내용은 안 올라가요)'],
  ['full', '원문까지', '+ 마스킹한 원문 — 서버에서 재실행 비교를 하려면 필요'],
];
function SyncChoice({ sync }) {
  const cur = sync?.mode || 'off';
  return (
    <div id="sync-choice" className="space-y-1">
      {SYNC.map(([k, t, d]) => (
        <label key={k} className="flex cursor-default items-start gap-2.5 rounded-lg px-1 py-1 text-[13px] hover:bg-raise">
          <input type="radio" name="sync" value={k} checked={cur === k} onChange={() => setPref('sync', k)} className="mt-1 accent-[#ff6b45]" />
          <span><span className="font-medium">{t}</span><span className="block text-[11.5px] text-card-faint">{d}</span></span>
        </label>
      ))}
      {cur !== 'off' && (
        <div className="flex items-center gap-2 pt-1 text-[11.5px] text-card-sub">
          {sync.running ? '올리는 중…' : sync.last ? `마지막 ${ago(Date.parse(sync.last))} · 이번 실행 ${sync.sent}건` : '곧 올려요'}
          {sync.server?.mine && <span>· 서버에 내 기록 {sync.server.mine.events}건</span>}
          {sync.error && <span className="text-warn">· {sync.error}</span>}
          <button onClick={() => a2a.invoke('onto:call', 'syncNow')} className="ml-auto rounded-lg px-2 py-0.5 ring-1 ring-card-line hover:bg-raise">지금 올리기</button>
        </div>
      )}
    </div>
  );
}

// 에이전트 기본 모델 — 업무 유형마다 정한 모델이 있으면 그쪽이 먼저(업무 지도 → 업무 유형)
function ModelChoice({ m }) {
  if (!m) return null;
  return (
    <div id="model-choice" className="space-y-1">
      {m.choices.map((c) => (
        <label key={c.id} className="flex cursor-default items-start gap-2.5 rounded-lg px-1 py-1 text-[13px] hover:bg-raise">
          <input type="radio" name="agentModel" value={c.id} checked={m.base === c.id} onChange={() => setPref('agentModel', c.id)} className="mt-1 accent-[#ff6b45]" />
          <span><span className="font-medium">{c.label}</span>
            <span className="block text-[11.5px] text-card-faint">{c.id.includes('opus') ? '깊게 생각 · 비쌈(입력 $4 / 출력 $20, 100만 토큰당)' : '중간급 · 절반 가격(입력 $2 / 출력 $10)'}</span></span>
        </label>
      ))}
      <div className="pt-1 text-[11.5px] text-card-faint">업무 유형별로 재실행 비교를 해 보고 유형마다 따로 정할 수 있어요(업무 지도 → 업무 유형).</div>
    </div>
  );
}

// 저장된 비밀번호 — 목록에는 비밀번호가 없다. 보기는 Touch ID/확인을 거쳐 한 건만, 20초 뒤 다시 가린다.
function Passwords() {
  const [vault, setVault] = useState(null);
  const [shown, setShown] = useState({});        // id → 비밀번호(잠깐)
  const [armed, setArmed] = useState(null);      // 삭제 확인 대기 id
  const load = () => a2a.invoke('pw:list').then(setVault).catch(() => setVault({ entries: [], available: false }));
  useEffect(() => { load(); const off = a2a.on('pw:resolved', load); return off; }, []);
  const reveal = async (id) => {
    const pw = await a2a.invoke('pw:reveal', id).catch(() => null);
    if (!pw) return;
    setShown((s) => ({ ...s, [id]: pw }));
    setTimeout(() => setShown((s) => { const n = { ...s }; delete n[id]; return n; }), 20000);
  };
  const remove = (id) => {
    if (armed !== id) { setArmed(id); setTimeout(() => setArmed((a) => (a === id ? null : a)), 3000); return; }
    a2a.send('pw:remove', id);
    setArmed(null);
    setTimeout(load, 150);
  };
  if (!vault) return <div className="text-xs text-card-faint">불러오는 중…</div>;
  if (!vault.available || vault.locked) {
    return <div className="text-[12px] text-warn">{vault.locked ? '금고 파일을 풀 수 없어 잠가 두었습니다(키체인 키가 바뀌었을 수 있어요).' : '이 컴퓨터에선 암호화 저장을 쓸 수 없어 비밀번호를 저장하지 않습니다.'}</div>;
  }
  if (!vault.entries.length) {
    return <div className="text-[12px] text-card-faint">아직 없어요. 웹사이트에 로그인하면 저장할지 물어봅니다.</div>;
  }
  return (
    <div className="flex flex-col gap-1">
      {vault.entries.map((e) => (
        <div key={e.id} className="group flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-raise">
          <Icon name="key" className="size-3.5 shrink-0 text-card-faint" />
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-[13px] font-medium">{(() => { try { return new URL(e.origin).host; } catch { return e.origin; } })()}</div>
            <div className="truncate text-[11.5px] text-card-faint">
              {e.username || '(아이디 없음)'} · {shown[e.id] ? <span className="font-mono text-card-ink select-text">{shown[e.id]}</span> : '••••••••'} · {ago(e.lastUsed || e.created)}
            </div>
          </div>
          <button title="보기 (Touch ID)" onClick={() => (shown[e.id] ? setShown((s) => { const n = { ...s }; delete n[e.id]; return n; }) : reveal(e.id))}
                  className="grid size-7 place-items-center rounded-md text-card-sub hover:bg-card hover:text-card-ink"><Icon name="eye" className="size-3.5" /></button>
          <button title={armed === e.id ? '한 번 더 누르면 삭제' : '삭제'} onClick={() => remove(e.id)}
                  className={`flex h-7 items-center gap-1 rounded-md px-1.5 text-[11px] font-semibold ${armed === e.id ? 'bg-warn/15 text-warn' : 'text-card-sub hover:bg-card hover:text-card-ink'}`}>
            <Icon name="trash" className="size-3.5" />{armed === e.id && '삭제?'}
          </button>
        </div>
      ))}
    </div>
  );
}

export default function MyPage() {
  const me = useStore((s) => s.me);
  const S = useStore((s) => s.S);
  const prefs = S.prefs || { notify: true };
  const [hueState, setHueState] = useState(null);    // 색 저장 실패 안내
  const [name, setName] = useState(me?.display_name || '');
  const [state, setState] = useState(null);          // null | 'saving' | { ok, error }
  useEffect(() => { setName(me?.display_name || ''); }, [me?.display_name]);

  if (!me) {
    return <div className="p-6 text-center text-xs text-card-faint">계정 정보를 불러오는 중… (엔진 8423이 떠 있어야 합니다)</div>;
  }
  const clean = name.replace(/\s+/g, ' ').trim();
  const valid = clean.length >= 1 && clean.length <= 40;
  const dirty = clean !== me.display_name;
  const save = async () => {
    if (!valid || !dirty) return;
    setState('saving');
    setState(await saveDisplayName(clean));
  };

  return (
    <div className="pb-4">
      <div className="flex flex-col items-center gap-2 px-5 pt-6 pb-5 text-center">
        <Avatar id={me.user_id} name={me.display_name} size="xl" />
        <div className="text-[17px] font-semibold">{me.display_name}</div>
        <div className="text-xs text-card-faint">@{me.user_id} · {S.tenantName || me.tenant} · {ROLE[me.role] || me.role}</div>
      </div>

      <Section title="표시 이름" note="팀원에게 이렇게 보여요">
        <form onSubmit={(e) => { e.preventDefault(); save(); }} className="flex gap-2">
          <input value={name} maxLength={60} onChange={(e) => { setName(e.target.value); setState(null); }}
                 className="h-9 min-w-0 flex-1 rounded-lg bg-field px-3 text-[13.5px] text-card-ink ring-1 ring-field-line outline-none select-text focus:ring-[1.5px] focus:ring-accent" />
          <button type="submit" disabled={!valid || !dirty || state === 'saving'}
                  className="h-9 shrink-0 rounded-lg bg-accent px-4 text-[13px] font-semibold text-white disabled:bg-raise disabled:text-card-faint">
            {state === 'saving' ? '저장 중…' : '저장'}
          </button>
        </form>
        <div className="mt-1.5 h-4 text-[11.5px]">
          {!valid && <span className="text-warn">1~40자로 적어 주세요</span>}
          {valid && state?.ok && !dirty && <span className="text-ok">저장했어요 — 팀 채팅에 바로 반영됩니다</span>}
          {state && state.ok === false && <span className="text-warn">{state.error}</span>}
        </div>
      </Section>

      <Section title="아바타 색" note="팀원에게도 보여요">
        <div className="flex flex-wrap gap-2">
          <button onClick={async () => setHueState(await saveAvatarHue(null))} title="기본(아이디로 정해진 색)"
                  className={`grid size-8 place-items-center rounded-[9px] text-[10px] font-semibold text-card-sub ring-1 ring-card-line
                              ${me.avatar_hue == null ? 'outline-2 outline-offset-2 outline-accent' : ''}`}>기본</button>
          {HUES.map((h) => (
            <button key={h} onClick={async () => setHueState(await saveAvatarHue(h))} aria-label={`색 ${h}`}
                    className={`size-8 rounded-[9px] ${me.avatar_hue === h ? 'outline-2 outline-offset-2 outline-accent' : ''}`}
                    style={{ background: `hsl(${h} 48% 52%)` }} />
          ))}
        </div>
        {hueState?.ok === false && <div className="mt-1.5 text-[11.5px] text-warn">{hueState.error}</div>}
      </Section>

      <Section title="알림" note="이 컴퓨터에서만">
        <label className="flex cursor-default items-center justify-between gap-3 text-[13px]">
          <span>
            <span className="block font-medium">시스템 알림</span>
            <span className="block text-[11.5px] text-card-faint">창이 뒤에 있을 때 승인 요청·나를 부른 메시지를 알려요</span>
          </span>
          <button role="switch" aria-checked={prefs.notify} onClick={() => setPref('notify', !prefs.notify)}
                  className={`relative h-6 w-10 shrink-0 rounded-full transition ${prefs.notify ? 'bg-accent' : 'bg-raise ring-1 ring-card-line'}`}>
            <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${prefs.notify ? 'left-[18px]' : 'left-0.5'}`} />
          </button>
        </label>
      </Section>

      <Section title="연결된 도구" note="로그인은 직접 · 토큰은 키체인 암호화">
        <EmaConnect ema={S.ema} />
      </Section>

      <Section title="에이전트 모델" note="새 작업부터 · 기본 Opus 5.5">
        <ModelChoice m={S.agentModel} />
      </Section>

      <Section title="업무 기록 올리기" note="워크스페이스로 · 기본 꺼짐">
        <SyncChoice sync={S.sync} />
      </Section>

      <Section title="저장된 비밀번호" note="이 컴퓨터 · 암호화 · AI는 못 봄">
        <Passwords />
      </Section>

      <Section title="시작할 때" note="이 컴퓨터에서만">
        <label className="flex cursor-default items-center justify-between gap-3 text-[13px]">
          <span>
            <span className="block font-medium">프로필 고르기 화면</span>
            <span className="block text-[11.5px] text-card-faint">켤 때 계정·워크스페이스를 고르는 화면을 보여 줘요(크롬처럼)</span>
          </span>
          <button role="switch" aria-checked={prefs.askProfile !== false} onClick={() => setPref('askProfile', !(prefs.askProfile !== false))}
                  className={`relative h-6 w-10 shrink-0 rounded-full transition ${prefs.askProfile !== false ? 'bg-accent' : 'bg-raise ring-1 ring-card-line'}`}>
            <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${prefs.askProfile !== false ? 'left-[18px]' : 'left-0.5'}`} />
          </button>
        </label>
      </Section>

      <Section title="계정">
        <Row label="아이디">@{me.user_id}</Row>
        <Row label="이메일">{me.email}</Row>
        <Row label="워크스페이스">{S.tenantName || me.tenant}</Row>
        <Row label="역할">{ROLE[me.role] || me.role}</Row>
        <Row label="로그인">
          <span className="inline-flex items-center gap-1 rounded-md bg-warn/10 px-1.5 py-0.5 text-[11.5px] text-warn">
            <Icon name="alert" className="size-3" />개발용 로그인
          </span>
        </Row>
      </Section>
    </div>
  );
}
