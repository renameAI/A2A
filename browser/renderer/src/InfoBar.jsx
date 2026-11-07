// 주소 줄 아래 안내 줄 — 비밀번호 저장 제안 / 저장된 로그인 채우기 / 잠깐 보이는 결과.
// 카드 안에 띄우면 웹페이지가 덮어 버려서 찾기 바와 같은 자리를 쓴다.
import { useState } from 'react';
import Icon from './Icon.jsx';
import { useStore, resolvePw, fillPw, hideFill } from './store.js';

const host = (o) => { try { return new URL(o).host; } catch { return o; } };

function Bar({ children, tone }) {
  return (
    <div className={`no-drag mb-2 flex min-h-9 shrink-0 items-center gap-2 rounded-[11px] py-1 pr-1 pl-3 text-[12.5px] shadow-md ring-1
                     ${tone === 'warn' ? 'bg-card text-warn ring-warn/40' : 'bg-card text-card-ink ring-card-line'}`}>
      {children}
    </div>
  );
}
const Btn = ({ onClick, primary, children, title }) => (
  <button onClick={onClick} title={title}
          className={`h-7 shrink-0 rounded-lg px-3 text-xs font-semibold ${primary ? 'bg-accent text-white' : 'text-card-sub hover:bg-raise hover:text-card-ink'}`}>
    {children}
  </button>
);

export default function InfoBar() {
  const S = useStore((s) => s.S);
  const offer = useStore((s) => s.pwOffer);
  const fillMap = useStore((s) => s.pwFill);
  const note = useStore((s) => s.pwNote);
  const [pick, setPick] = useState(null);
  const cur = S.tabs.find((t) => t.active);
  if (!cur) return null;

  if (offer && offer.tabId === cur.id) {
    return (
      <Bar>
        <Icon name="key" className="size-4 text-accent" />
        <span className="min-w-0 flex-1 truncate">
          <b>{host(offer.origin)}</b>{offer.update ? '의 비밀번호가 바뀌었어요. 저장된 것을 바꿀까요?' : '의 로그인 정보를 저장할까요?'}
          {offer.username && <span className="ml-1.5 text-card-faint">{offer.username}</span>}
          <span className="ml-1.5 text-card-faint">· 이 컴퓨터에 암호화</span>
        </span>
        {!offer.update && <Btn onClick={() => resolvePw(offer.id, 'never')}>이 사이트는 안 함</Btn>}
        <Btn onClick={() => resolvePw(offer.id, 'dismiss')}>나중에</Btn>
        <Btn primary onClick={() => resolvePw(offer.id, 'save')}>{offer.update ? '바꾸기' : '저장'}</Btn>
      </Bar>
    );
  }
  const fill = fillMap[cur.id];
  if (fill?.accounts?.length) {
    const sel = fill.accounts.find((a) => a.id === pick) || fill.accounts[0];
    return (
      <Bar>
        <Icon name="key" className="size-4 text-accent" />
        <span className="min-w-0 flex-1 truncate"><b>{host(fill.origin)}</b> 저장된 로그인이 있어요</span>
        {fill.accounts.length > 1 ? (
          <select value={sel.id} onChange={(e) => setPick(e.target.value)}
                  className="h-7 max-w-[180px] rounded-lg bg-raise px-2 text-xs font-medium text-card-ink outline-none">
            {fill.accounts.map((a) => <option key={a.id} value={a.id}>{a.username || '(아이디 없음)'}</option>)}
          </select>
        ) : <span className="max-w-[180px] truncate text-card-sub">{sel.username || '(아이디 없음)'}</span>}
        <Btn onClick={() => hideFill(cur.id)} title="이번엔 닫기">닫기</Btn>
        <Btn primary onClick={() => fillPw(cur.id, sel.id)}>채우기</Btn>
      </Bar>
    );
  }
  if (note) {
    return (
      <Bar tone={note.tone}>
        <Icon name={note.tone === 'warn' ? 'alert' : 'check'} className={`size-4 ${note.tone === 'warn' ? '' : 'text-ok'}`} />
        <span>{note.text}</span>
      </Bar>
    );
  }
  return null;
}
