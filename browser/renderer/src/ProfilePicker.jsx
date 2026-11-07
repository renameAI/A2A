// 프로필 선택 — 크롬의 "누가 사용하나요?"처럼, 시작할 때(또는 사이드바에서 열 때) 계정 × 워크스페이스를 고른다.
import { useEffect, useState } from 'react';
import Icon from './Icon.jsx';
import { Mark } from './NewTab.jsx';
import { useStore, listProfiles, switchProfile, showPicker, setPref } from './store.js';

const hueOf = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.codePointAt(0)) % 360; return h; };

export default function ProfilePicker() {
  const S = useStore((s) => s.S);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => { listProfiles().then(setData); }, []);

  const pick = async (user, tenant) => {
    setBusy(`${user}/${tenant}`);
    setErr(null);
    const r = await switchProfile(user, tenant);
    setBusy(null);
    if (!r?.ok) setErr(r?.error || '바꾸지 못했어요');
  };
  const cards = data?.profiles.flatMap((p) => p.tenants.map((t) => ({ user: p.user, ...t }))) || [];
  const cur = data?.current;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-black/25 p-8 backdrop-blur-2xl">
      <div className="flex w-full max-w-[760px] flex-col items-center">
        <Mark />
        <h1 className="mt-5 mb-1.5 text-[28px] font-bold tracking-tight text-ink [text-shadow:0_1px_14px_rgb(0_0_0/.2)]">어느 프로필로 시작할까요?</h1>
        <p className="mb-8 text-sm text-ink/80">계정과 워크스페이스를 고르면 팀 채널·에이전트가 그 워크스페이스로 연결됩니다.</p>

        {!data && <div className="text-sm text-ink/80">엔진에서 목록을 받는 중…</div>}
        {data && !cards.length && <div className="text-sm text-warn">들어갈 수 있는 워크스페이스가 없어요 — 엔진(8423)이 떠 있는지 확인하세요.</div>}

        <div className="grid w-full grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-3">
          {cards.map((c) => {
            const on = cur && cur.user === c.user && cur.tenant === c.slug;
            const key = `${c.user}/${c.slug}`;
            return (
              <button key={key} data-profile={key} onClick={() => pick(c.user, c.slug)} disabled={!!busy}
                      className={`group flex flex-col items-center gap-2.5 rounded-2xl bg-card/90 px-4 pt-6 pb-4 text-card-ink shadow-lg ring-1 transition
                                  hover:-translate-y-0.5 hover:bg-card disabled:opacity-60 ${on ? 'ring-2 ring-accent' : 'ring-card-line'}`}>
                <span className="grid size-16 place-items-center rounded-[20px] text-[26px] font-bold text-white shadow-md"
                      style={{ background: `hsl(${hueOf(c.user)} 48% 52%)` }}>
                  {c.user.charAt(0).toUpperCase()}
                </span>
                <span className="w-full truncate text-center text-[14px] font-semibold">{c.name}</span>
                <span className="flex items-center gap-1 text-[11.5px] text-card-faint">
                  <Icon name="person" className="size-3" />@{c.user}
                </span>
                <span className="text-[11px] text-card-faint">
                  {busy === key ? '여는 중…' : on ? '지금 쓰는 중' : c.plan}
                </span>
              </button>
            );
          })}
        </div>
        {err && <div className="mt-4 text-sm text-warn">{err}</div>}

        <div className="mt-8 flex items-center gap-4 text-[13px] text-ink">
          <label className="flex cursor-default items-center gap-2">
            <input type="checkbox" checked={S.prefs?.askProfile !== false} onChange={(e) => setPref('askProfile', e.target.checked)} className="size-4 accent-[#ff6b45]" />
            시작할 때 이 화면 보기
          </label>
          {cur && (
            <button onClick={() => showPicker(false)} className="rounded-lg bg-card/80 px-3 py-1.5 font-semibold text-card-ink hover:bg-card">
              지금 프로필로 계속
            </button>
          )}
        </div>
        <p className="mt-3 text-[11.5px] text-ink/60">개발용 로그인이라 계정도 고를 수 있어요. 실제 로그인을 붙이면 내 계정의 워크스페이스만 보입니다.</p>
      </div>
    </div>
  );
}
