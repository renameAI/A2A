// 에이전트 답변 마크다운 → React 요소. 제목·목록·표·코드 블록·인용·굵게·코드·링크만 다룬다.
// HTML 문자열은 만들지 않는다(dangerouslySetInnerHTML 없음) — 답변은 웹페이지 내용을 옮겨 오기도 한다.
import { useState } from 'react';
import { a2a, get } from './store.js';
import Icon from './Icon.jsx';

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s)>\]|]+|@에이전트|@[A-Za-z0-9._%+\-]+)/;

function Link({ href, children }) {
  return (
    <a href={href} onClick={(e) => { e.preventDefault(); a2a.send('tab:new', href); }}
       className="cursor-pointer break-all text-info underline decoration-info/30 underline-offset-2 hover:decoration-info">
      {children}
    </a>
  );
}

export function Inline({ text }) {
  return text.split(INLINE).map((p, i) => {
    if (!p) return null;
    if (p.startsWith('**') && p.endsWith('**') && p.length > 4) return <strong key={i} className="font-semibold">{p.slice(2, -2)}</strong>;
    if (p.startsWith('`') && p.endsWith('`') && p.length > 2) {
      return <code key={i} className="rounded-[5px] bg-raise px-1.5 py-px font-mono text-[12px]">{p.slice(1, -1)}</code>;
    }
    const md = p.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
    if (md) return <Link key={i} href={md[2]}>{md[1]}</Link>;
    if (/^https?:\/\//.test(p)) return <Link key={i} href={p}>{p}</Link>;
    if (p === '@에이전트') return <span key={i} className="rounded bg-accent-soft px-1 font-semibold text-accent">@에이전트</span>;
    if (p.startsWith('@')) {
      const m = get().members[p.slice(1)];
      if (m) return <span key={i} className="rounded bg-info/12 px-1 font-semibold text-info">@{m.display_name}</span>;
    }
    return p;
  });
}

const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
const isRule = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

function blocks(src) {
  const lines = src.split('\n');
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      const body = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) body.push(lines[i]);
      out.push({ k: 'code', text: body.join('\n') });
    } else if (/^\s*\|/.test(line) && isRule(lines[i + 1] || '')) {
      const head = cells(line);
      const rows = [];
      for (i += 2; i < lines.length && /^\s*\|/.test(lines[i]); i++) rows.push(cells(lines[i]));
      i--;
      out.push({ k: 'table', head, rows });
    } else if (/^\s*([-*]|\d+\.) /.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items = [];
      for (; i < lines.length && /^\s*([-*]|\d+\.) /.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*]|\d+\.) /, ''));
      i--;
      out.push({ k: ordered ? 'ol' : 'ul', items });
    } else if (/^#{1,4} /.test(line)) out.push({ k: 'h', text: line.replace(/^#{1,4} /, '') });
    else if (/^> /.test(line)) out.push({ k: 'quote', text: line.slice(2) });
    else if (/^\s*(---|\*\*\*)\s*$/.test(line)) out.push({ k: 'hr' });
    else if (line.trim()) out.push({ k: 'p', text: line });
  }
  return out;
}

function CodeBlock({ text }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="group relative">
      <pre className="overflow-x-auto rounded-[10px] bg-raise px-3 py-2.5 font-mono text-[12px] leading-[1.55] select-text">{text}</pre>
      <button onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1200); }}
              className="absolute top-1.5 right-1.5 hidden size-6 place-items-center rounded-md bg-field text-card-sub shadow-sm group-hover:grid"
              title="복사">
        <Icon name={copied ? 'check' : 'copy'} className="size-3.5" />
      </button>
    </div>
  );
}

export default function Markdown({ src }) {
  return (
    <div className="flex flex-col gap-2 text-[13.2px] leading-[1.62]">
      {blocks(src).map((b, i) => {
        switch (b.k) {
          case 'h': return <div key={i} className="mt-1 font-bold"><Inline text={b.text} /></div>;
          case 'p': return <div key={i}><Inline text={b.text} /></div>;
          case 'quote': return <div key={i} className="border-l-2 border-card-line pl-3 text-card-sub"><Inline text={b.text} /></div>;
          case 'hr': return <hr key={i} className="border-card-line" />;
          case 'code': return <CodeBlock key={i} text={b.text} />;
          case 'ul':
          case 'ol': {
            const Tag = b.k;
            return (
              <Tag key={i} className={`flex flex-col gap-0.5 pl-5 ${b.k === 'ol' ? 'list-decimal' : 'list-disc'} marker:text-card-faint`}>
                {b.items.map((t, j) => <li key={j}><Inline text={t} /></li>)}
              </Tag>
            );
          }
          case 'table':
            return (
              <div key={i} className="overflow-x-auto rounded-[10px] ring-1 ring-card-line">
                <table className="w-full border-collapse text-[12.5px]">
                  <thead className="bg-raise text-left">
                    <tr>{b.head.map((h, j) => <th key={j} className="px-2.5 py-1.5 font-semibold whitespace-nowrap"><Inline text={h} /></th>)}</tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j} className="border-t border-card-line align-top">
                        {r.map((c, k) => <td key={k} className="px-2.5 py-1.5"><Inline text={c} /></td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          default: return null;
        }
      })}
    </div>
  );
}
