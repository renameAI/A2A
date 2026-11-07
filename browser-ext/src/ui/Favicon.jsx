// 사이트 아이콘 — 없거나 깨지면 첫 글자 색 칸
import { useEffect, useState } from 'react';
import Icon from './Icon.jsx';
import { hostOf, hue } from './format.js';

export function Favicon({ url, favicon, title, loading, newtab }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [favicon]);
  if (loading) return <span className="grid size-4 place-items-center"><span className="size-3.5 animate-spin rounded-full border-2 border-sub border-r-transparent opacity-60" /></span>;
  if (newtab) return <Icon name="spark" className="size-4 text-sub" />;
  if (favicon && !broken) return <img src={favicon} onError={() => setBroken(true)} className="size-4 shrink-0 rounded-[4px]" alt="" />;
  const label = (title || hostOf(url) || '?').trim().charAt(0).toUpperCase();
  return (
    <span className="grid size-4 shrink-0 place-items-center rounded-[5px] text-[10px] font-bold text-white"
          style={{ background: hue(hostOf(url) || title) }}>{label}</span>
  );
}
